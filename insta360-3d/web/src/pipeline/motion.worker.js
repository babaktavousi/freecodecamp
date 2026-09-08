/**
 * Camera motion for a sequence of equirectangular keyframes.
 *
 * Runs off the main thread because the block matching is the one genuinely
 * CPU-bound stage; the dense depth stage afterwards is GPU work.
 */

import {
  baselinesFromDepths,
  countInliers,
  median,
  pixelToDirection,
  refinePose,
  triangulateDepth,
  yawPitchMatrix,
} from './geometry.js';
import {
  estimateShift,
  highPass,
  makeTrackingGrid,
  resizeGray,
  trackPoints,
  warpEquirect,
} from './imaging.js';

const HYPOTHESES_DEG = [0, 6, -6, 13, -13];

self.onmessage = (event) => {
  const msg = event.data;
  if (msg.type !== 'estimate') return;
  try {
    postMessage(estimate(msg));
  } catch (error) {
    postMessage({ type: 'error', message: error?.message || String(error) });
  }
};

function estimate({ grays, width, height, options = {} }) {
  const n = grays.length;
  const hp = grays.map((g) => highPass(g, width, height, 5));

  const shiftWidth = Math.min(width, 256);
  const shiftHeight = shiftWidth >> 1;
  const shiftFrames = hp.map((g) => resizeGray(g, width, height, shiftWidth, shiftHeight));

  const points = makeTrackingGrid(width, height, options.cols ?? 72, options.rows ?? 26);
  const rotations = [];
  const directions = [];
  const medians = [];

  for (let i = 0; i < n - 1; i++) {
    const { dx } = estimateShift(shiftFrames[i], shiftFrames[i + 1], shiftWidth, shiftHeight);
    // A horizontal shift of dx pixels is a yaw of -dx * 2pi / width: the
    // rotation has to undo the shift to carry frame B onto frame A.
    const yaw = -((dx * width) / shiftWidth / width) * 2 * Math.PI;
    const solved = solvePair(hp[i], hp[i + 1], width, height, points, yawPitchMatrix(yaw, 0));

    rotations.push(Array.from(solved.rotation));
    directions.push(solved.translation);
    medians.push(solved.medianDepth);
    postMessage({ type: 'progress', pair: i + 1, total: n - 1 });
  }

  const baselines = baselinesFromDepths(medians);
  return {
    type: 'done',
    rotations,
    directions,
    medians,
    baselines,
  };
}

function solvePair(refHp, nextHp, width, height, points, rotationInit, passes = 2) {
  let rotation = rotationInit;
  let translation = [0, 0, 1];
  let medianDepth = 10;

  for (let pass = 0; pass < passes; pass++) {
    const warped = warpEquirect(nextHp, width, height, rotation);
    const { matched, cost } = trackPoints(refHp, warped, width, height, points);

    // Keep the best-matching 80%: the rest are occlusions and blank surfaces.
    const sorted = Array.from(cost).sort((a, b) => a - b);
    const limit = sorted[Math.floor(sorted.length * 0.8)] ?? Infinity;

    const kept = [];
    for (let i = 0; i < cost.length; i++) if (cost[i] <= limit) kept.push(i);

    const p = new Float32Array(kept.length * 3);
    const q = new Float32Array(kept.length * 3);
    const dir = [0, 0, 0];
    kept.forEach((idx, k) => {
      pixelToDirection(points[idx * 2], points[idx * 2 + 1], width, height, dir);
      p[k * 3] = dir[0]; p[k * 3 + 1] = dir[1]; p[k * 3 + 2] = dir[2];
      // A match at warped pixel x came from raw direction R * d(x).
      pixelToDirection(matched[idx * 2], matched[idx * 2 + 1], width, height, dir);
      const x = rotation[0] * dir[0] + rotation[1] * dir[1] + rotation[2] * dir[2];
      const y = rotation[3] * dir[0] + rotation[4] * dir[1] + rotation[5] * dir[2];
      const z = rotation[6] * dir[0] + rotation[7] * dir[1] + rotation[8] * dir[2];
      const len = Math.hypot(x, y, z) || 1;
      q[k * 3] = x / len; q[k * 3 + 1] = y / len; q[k * 3 + 2] = z / len;
    });

    // The coarse shift search occasionally locks onto repeating architecture,
    // so the first pass tries several yaw starts and keeps whichever explains
    // the correspondences best.
    const candidates = pass === 0 ? HYPOTHESES_DEG : [0];
    let best = null;
    for (const deg of candidates) {
      const start = multiply(rotation, yawPitchMatrix((deg * Math.PI) / 180, 0));
      const result = refinePose(p, q, start);
      const score = countInliers(p, q, result.rotation, result.translation);
      if (!best || score > best.score) best = { score, ...result };
    }
    rotation = best.rotation;
    translation = best.translation;

    if (pass === passes - 1) {
      medianDepth = medianDepthOf(p, q, rotation, translation);
      if (medianDepth < 0) {
        translation = translation.map((v) => -v);
        medianDepth = -medianDepth;
      }
      if (!Number.isFinite(medianDepth) || medianDepth <= 0) medianDepth = 10;
    }
  }

  return { rotation, translation, medianDepth };
}

function medianDepthOf(p, q, rotation, t) {
  const n = p.length / 3;
  const depths = [];
  const a = [0, 0, 0], b = [0, 0, 0], c = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    a[0] = p[i * 3]; a[1] = p[i * 3 + 1]; a[2] = p[i * 3 + 2];
    b[0] = q[i * 3]; b[1] = q[i * 3 + 1]; b[2] = q[i * 3 + 2];
    c[0] = rotation[0] * b[0] + rotation[1] * b[1] + rotation[2] * b[2];
    c[1] = rotation[3] * b[0] + rotation[4] * b[1] + rotation[5] * b[2];
    c[2] = rotation[6] * b[0] + rotation[7] * b[1] + rotation[8] * b[2];
    const z = triangulateDepth(a, c, t);
    if (Number.isFinite(z)) depths.push(z);
  }
  return median(depths);
}

function multiply(a, b) {
  const out = new Float32Array(9);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      let sum = 0;
      for (let k = 0; k < 3; k++) sum += a[r * 3 + k] * b[k * 3 + c];
      out[r * 3 + c] = sum;
    }
  }
  return out;
}
