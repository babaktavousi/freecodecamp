import test from 'node:test';
import assert from 'node:assert/strict';

import {
  alignToUp,
  applyMat3,
  baselinesFromDepths,
  buildPoses,
  countInliers,
  directionToPixel,
  estimateFloor,
  expmSO3,
  multiplyMat3,
  pixelToDirection,
  refinePose,
  smallestEigenvector,
  triangulateDepth,
  yawPitchMatrix,
} from '../src/pipeline/geometry.js';

const DEG = Math.PI / 180;

function rotationError(a, b) {
  // angle of a^T b
  let trace = 0;
  for (let i = 0; i < 3; i++) {
    for (let k = 0; k < 3; k++) trace += i === 0 ? 0 : 0;
  }
  const at = [a[0], a[3], a[6], a[1], a[4], a[7], a[2], a[5], a[8]];
  const m = multiplyMat3(at, b);
  const t = Math.max(-1, Math.min(1, (m[0] + m[4] + m[8] - 1) / 2));
  return Math.acos(t) / DEG;
}

function makeRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test('equirect pixel and direction round-trip', () => {
  const width = 512;
  const height = 256;
  for (const [x, y] of [[0, 0], [255.5, 127.5], [511, 255], [10.25, 200.75]]) {
    const dir = pixelToDirection(x, y, width, height);
    assert.ok(Math.abs(Math.hypot(...dir) - 1) < 1e-6, 'direction is unit length');
    const [u, v] = directionToPixel(dir, width, height);
    const dx = Math.min(Math.abs(u - x), Math.abs(Math.abs(u - x) - width));
    assert.ok(dx < 1e-3, `x round-trip ${x} -> ${u}`);
    assert.ok(Math.abs(v - y) < 1e-3, `y round-trip ${y} -> ${v}`);
  }
});

test('forward direction sits at the centre of the frame', () => {
  const dir = pixelToDirection(256, 128, 512, 256);
  assert.ok(dir[2] > 0.999, 'centre pixel looks along +Z');
  const right = pixelToDirection(384, 128, 512, 256);
  assert.ok(right[0] > 0.999, 'quarter-turn right looks along +X');
});

test('yaw rotation maps into the expected longitude shift', () => {
  const yaw = 20 * DEG;
  const rotated = applyMat3(yawPitchMatrix(yaw, 0), pixelToDirection(256, 128, 512, 256));
  const [u] = directionToPixel(rotated, 512, 256);
  // +20 degrees of yaw moves content 20/360 of the width to the right.
  assert.ok(Math.abs(u - (256 + (20 / 360) * 512)) < 0.5, `longitude shifted to ${u}`);
});

test('smallest eigenvector of a symmetric matrix', () => {
  // Diagonal matrix: the answer must be the axis with the smallest entry.
  const v = smallestEigenvector(new Float64Array([5, 0, 0, 0, 0.25, 0, 0, 0, 9]));
  assert.ok(Math.abs(Math.abs(v[1]) - 1) < 1e-6, `expected the Y axis, got ${v}`);
});

test('expmSO3 matches a known yaw rotation', () => {
  const r = expmSO3([0, 30 * DEG, 0]);
  const expected = yawPitchMatrix(30 * DEG, 0);
  for (let i = 0; i < 9; i++) assert.ok(Math.abs(r[i] - expected[i]) < 1e-6);
});

test('refinePose recovers rotation and translation from clean correspondences', () => {
  const rng = makeRng(42);
  const yaw = 2 * DEG;
  const truth = yawPitchMatrix(-yaw, 0); // maps frame B into frame A
  const tTruth = [0.25, -0.03, 0.97];
  const norm = Math.hypot(...tTruth);
  for (let i = 0; i < 3; i++) tTruth[i] /= norm;
  const baseline = 0.42;

  const p = [];
  const q = [];
  for (let i = 0; i < 600; i++) {
    const point = [(rng() - 0.5) * 8, (rng() - 0.5) * 3, (rng() - 0.5) * 12];
    const len = Math.hypot(...point);
    if (len < 1.5) continue;
    p.push(point[0] / len, point[1] / len, point[2] / len);
    // X_B = R^T (X_A - b t)
    const shifted = [
      point[0] - baseline * tTruth[0],
      point[1] - baseline * tTruth[1],
      point[2] - baseline * tTruth[2],
    ];
    const b = [
      truth[0] * shifted[0] + truth[3] * shifted[1] + truth[6] * shifted[2],
      truth[1] * shifted[0] + truth[4] * shifted[1] + truth[7] * shifted[2],
      truth[2] * shifted[0] + truth[5] * shifted[1] + truth[8] * shifted[2],
    ];
    const bl = Math.hypot(...b);
    q.push(b[0] / bl, b[1] / bl, b[2] / bl);
  }

  for (const initErrorDeg of [0, 1, 4]) {
    const start = yawPitchMatrix(-yaw + initErrorDeg * DEG, 0);
    const { rotation, translation } = refinePose(
      Float32Array.from(p), Float32Array.from(q), start
    );
    assert.ok(rotationError(rotation, truth) < 0.05,
      `rotation error ${rotationError(rotation, truth).toFixed(3)} deg from ${initErrorDeg} deg start`);
    const dot = Math.abs(
      translation[0] * tTruth[0] + translation[1] * tTruth[1] + translation[2] * tTruth[2]
    );
    assert.ok(Math.acos(Math.min(1, dot)) / DEG < 0.2, 'translation direction recovered');
  }
});

test('refinePose survives noise and outliers', () => {
  const rng = makeRng(7);
  const truth = yawPitchMatrix(-1.5 * DEG, 0);
  const tTruth = [0.1, 0, 0.995];
  const baseline = 0.4;
  const p = [];
  const q = [];
  let count = 0;
  while (count < 900) {
    const point = [(rng() - 0.5) * 10, (rng() - 0.5) * 3, (rng() - 0.5) * 14];
    const len = Math.hypot(...point);
    if (len < 1.5) continue;
    count++;
    p.push(point[0] / len, point[1] / len, point[2] / len);
    const shifted = [
      point[0] - baseline * tTruth[0],
      point[1] - baseline * tTruth[1],
      point[2] - baseline * tTruth[2],
    ];
    let b = [
      truth[0] * shifted[0] + truth[3] * shifted[1] + truth[6] * shifted[2],
      truth[1] * shifted[0] + truth[4] * shifted[1] + truth[7] * shifted[2],
      truth[2] * shifted[0] + truth[5] * shifted[1] + truth[8] * shifted[2],
    ];
    if (count % 7 === 0) b = [rng() - 0.5, rng() - 0.5, rng() - 0.5]; // ~14% outliers
    else b = b.map((v) => v + (rng() - 0.5) * 0.006); // ~1.5 px at 512 wide
    const bl = Math.hypot(...b);
    q.push(b[0] / bl, b[1] / bl, b[2] / bl);
  }

  const { rotation, translation, inliers } = refinePose(
    Float32Array.from(p), Float32Array.from(q), yawPitchMatrix(0, 0)
  );
  assert.ok(rotationError(rotation, truth) < 1.5,
    `rotation error ${rotationError(rotation, truth).toFixed(2)} deg`);
  const dot = Math.abs(
    translation[0] * tTruth[0] + translation[1] * tTruth[1] + translation[2] * tTruth[2]
  );
  assert.ok(Math.acos(Math.min(1, dot)) / DEG < 8, 'translation direction is close');
  assert.ok(inliers > 600, `kept ${inliers} inliers`);
  assert.ok(countInliers(Float32Array.from(p), Float32Array.from(q), rotation, translation) > 400);
});

test('triangulateDepth returns depth in baseline units', () => {
  const t = [0, 0, 1];
  // A point 5 baselines ahead and 1 to the right.
  const X = [1, 0, 5];
  const p = X.map((v) => v / Math.hypot(...X));
  const XB = [X[0], X[1], X[2] - 1];
  const q = XB.map((v) => v / Math.hypot(...XB));
  const depth = triangulateDepth(p, q, t);
  assert.ok(Math.abs(depth - Math.hypot(...X)) < 1e-4, `got ${depth}`);
});

test('triangulateDepth is negative for points behind the motion', () => {
  const t = [0, 0, 1];
  const X = [0.5, 0, -6];
  const p = X.map((v) => v / Math.hypot(...X));
  const XB = [X[0], X[1], X[2] - 1];
  const q = XB.map((v) => v / Math.hypot(...XB));
  assert.ok(triangulateDepth(p, q, t) > 0, 'a point behind is still in front of its own ray');
});

test('baselinesFromDepths tracks changes in pace', () => {
  const constant = baselinesFromDepths([8, 8, 8, 8, 8]);
  for (const b of constant) assert.ok(Math.abs(b - 1) < 1e-6, 'constant depth means constant baseline');

  // Halving the depth-in-baseline-units means the camera moved twice as far.
  const speeding = baselinesFromDepths([10, 10, 10, 5, 5, 5, 5], 1);
  assert.ok(speeding[3] > speeding[2], 'baseline grows when relative depth shrinks');
});

test('buildPoses chains motion and locks the horizon', () => {
  const rel = [yawPitchMatrix(10 * DEG, 0), yawPitchMatrix(10 * DEG, 0)];
  const dirs = [[0, 0, 1], [0, 0, 1]];
  const { centres, rotations } = buildPoses(rel, dirs, [1, 1]);
  assert.equal(rotations.length, 3);
  // Pose 1 = elements 3..5, pose 2 = elements 6..8.
  assert.ok(Math.abs(centres[5] - 1) < 1e-5, 'first step is one unit forward');
  assert.ok(Math.abs(centres[3]) < 1e-5, 'and straight ahead');
  // After 10 degrees of yaw the second step veers along +X.
  assert.ok(Math.abs(centres[6] - Math.sin(10 * DEG)) < 1e-5, 'second step turns right');
  const yaw = Math.atan2(rotations[2][2], rotations[2][8]) / DEG;
  assert.ok(Math.abs(yaw - 20) < 1e-3, `accumulated yaw ${yaw}`);

  const tilted = [multiplyMat3(yawPitchMatrix(0, 5 * DEG), yawPitchMatrix(5 * DEG, 0))];
  const locked = buildPoses(tilted, [[0, 0, 1]], [1], true).rotations[1];
  assert.ok(Math.abs(locked[3]) < 1e-6 && Math.abs(locked[5]) < 1e-6, 'pitch removed by the horizon lock');
  const free = buildPoses(tilted, [[0, 0, 1]], [1], false).rotations[1];
  assert.ok(Math.abs(free[5]) > 1e-3, 'pitch preserved when unlocked');
});

test('estimateFloor finds the floor slab under walls and clutter', () => {
  const rng = makeRng(3);
  const normal = [0.05, 0.998, 0.02];
  const len = Math.hypot(...normal);
  for (let i = 0; i < 3; i++) normal[i] /= len;
  const level = -1.2;  // floor height at the origin
  const points = [];

  // A floor reconstructed as a slab 12 cm thick, as a real depth sweep gives.
  for (let i = 0; i < 6000; i++) {
    const x = (rng() - 0.5) * 10;
    const z = (rng() - 0.5) * 10;
    const y = (level - normal[0] * x - normal[2] * z) / normal[1] + (rng() - 0.5) * 0.12;
    points.push(x, y, z);
  }
  // Walls, clutter and stray points that must not capture the fit.
  for (let i = 0; i < 2500; i++) points.push((rng() - 0.5) * 10, level + rng() * 2.5, 5 + (rng() - 0.5) * 0.1);
  for (let i = 0; i < 1200; i++) points.push(rng() * 2, level + 0.4 + rng() * 0.1, rng() * 2);
  for (let i = 0; i < 400; i++) points.push((rng() - 0.5) * 40, level - 3 * rng(), (rng() - 0.5) * 40);

  const plane = estimateFloor(Float32Array.from(points), 8);
  assert.ok(plane, 'a floor was found');
  const dot = Math.abs(
    plane.normal[0] * normal[0] + plane.normal[1] * normal[1] + plane.normal[2] * normal[2]
  );
  assert.ok(Math.acos(Math.min(1, dot)) / DEG < 2, `normal off by ${(Math.acos(dot) / DEG).toFixed(2)} deg`);
  // Height of the plane at the origin should match the floor level.
  assert.ok(Math.abs(-plane.d - level) < 0.05, `level ${-plane.d} vs ${level}`);
});

test('estimateFloor refuses a scene with no horizontal surface', () => {
  const rng = makeRng(11);
  const points = [];
  for (let i = 0; i < 3000; i++) points.push((rng() - 0.5) * 8, (rng() - 0.5) * 8, 4 + (rng() - 0.5) * 0.05);
  const plane = estimateFloor(Float32Array.from(points), 8);
  assert.ok(!plane || plane.normal[1] > 0.9, 'either nothing, or something actually horizontal');
});

test('alignToUp rotates a plane normal onto +Y', () => {
  const normal = [0.2, 0.95, -0.1];
  const len = Math.hypot(...normal);
  const unit = normal.map((v) => v / len);
  const rotated = applyMat3(alignToUp(unit), unit);
  assert.ok(Math.abs(rotated[1] - 1) < 1e-5, `got ${rotated}`);
});
