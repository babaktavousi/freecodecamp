import {
  alignToUp,
  applyMat3,
  buildPoses,
  estimateFloor,
  median,
  pixelToDirection,
  removeSparse,
} from './geometry.js';
import { extractKeyframes, grayFromImageData } from './frames.js';
import { PlaneSweepGL } from './planeSweep.js';

/**
 * Drives the full video-to-point-cloud pipeline in the browser.
 *
 * Motion estimation runs in a worker (CPU bound), the dense depth stage runs
 * on the GPU here, and points are emitted per keyframe so the viewer fills in
 * while the reconstruction is still running.
 */
export class Reconstructor extends EventTarget {
  constructor() {
    super();
    this.aborted = false;
  }

  cancel() {
    this.aborted = true;
    this.controller?.abort();
    this.worker?.terminate();
    this.worker = null;
  }

  _emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  _log(message) {
    this._emit('log', message);
  }

  _progress(fraction, label) {
    this._emit('progress', { fraction, label });
  }

  /**
   * @param {HTMLVideoElement} video
   * @param {object} options
   * @returns {Promise<{points: number, transform: Float32Array|null, path: number[][]}>}
   */
  async run(video, options = {}) {
    const {
      sweepWidth = 768,
      motionWidth = 384,
      layers = 128,
      spacing = 0.35,
      maxFrames = 48,
      neighbours = 3,
      minDepth = 1.0,
      maxDepth = 45.0,
      minConfidence = 0.06,
      minTexture = 0.012,
      maxLatitudeDeg = 72,
      cameraHeight = 1.6,
      pointBudget = 3_500_000,
    } = options;

    this.aborted = false;
    this.controller = new AbortController();

    this._progress(0.02, 'Reading keyframes…');
    const { frames } = await extractKeyframes(video, {
      spacing,
      maxFrames,
      width: sweepWidth,
      startTime: video.currentTime > 0.1 ? video.currentTime : 0,
      signal: this.controller.signal,
      onProgress: (i, n) => this._progress(0.02 + 0.13 * (i / n), `Reading keyframe ${i} of ${n}…`),
    });
    this._log(`${frames.length} keyframes at ${sweepWidth}×${sweepWidth >> 1}, ${spacing.toFixed(2)} s apart`);
    if (frames.length < 4) throw new Error('Need at least 4 keyframes — use a longer clip or closer spacing.');

    this._progress(0.16, 'Estimating camera motion…');
    const grays = frames.map((f) => grayFromImageData(f, motionWidth));
    const motion = await this._estimateMotion(grays, motionWidth, motionWidth >> 1);
    if (this.aborted) throw new DOMException('Cancelled', 'AbortError');

    const { centres, rotations } = buildPoses(
      motion.rotations.map((r) => Float32Array.from(r)),
      motion.directions,
      motion.baselines
    );
    const pathLength = trajectoryLength(centres);
    this._log(`camera path: ${centres.length / 3} poses, ${pathLength.toFixed(2)} baseline units`);
    this._emit('poses', { centres, rotations });

    this._progress(0.42, 'Preparing depth sweep…');
    const sweeper = new PlaneSweepGL(sweepWidth, sweepWidth >> 1);
    try {
      for (const frame of frames) sweeper.addFrame(frame);

      const perFrameBudget = Math.floor(pointBudget / frames.length);
      const collected = [];
      let total = 0;

      for (let i = 0; i < frames.length; i++) {
        if (this.aborted) throw new DOMException('Cancelled', 'AbortError');
        const nb = [];
        for (let d = -neighbours; d <= neighbours; d++) {
          const j = i + d;
          if (d !== 0 && j >= 0 && j < frames.length) nb.push(j);
        }
        if (nb.length < 2) continue;

        const result = sweeper.sweep({
          refIndex: i,
          neighbours: nb,
          centres,
          rotations,
          layers,
          minDepth,
          maxDepth,
        });

        const chunk = unproject(result, frames[i], centres, rotations, i, {
          layers, minConfidence, minTexture, maxLatitudeDeg, budget: perFrameBudget,
          medianDepth: motion.medians[Math.min(i, motion.medians.length - 1)] ?? 10,
        });
        if (chunk.count) {
          collected.push(chunk);
          total += chunk.count;
          this._emit('chunk', chunk);
        }
        this._progress(0.42 + 0.5 * ((i + 1) / frames.length),
          `Depth sweep ${i + 1} of ${frames.length} — ${total.toLocaleString()} points`);
        // Let the browser paint the points that just arrived.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }

      if (!total) {
        throw new Error('No points survived filtering. Try a longer keyframe spacing or a clip with more texture.');
      }

      this._progress(0.94, 'Setting metric scale…');
      const transform = this._metricScale(collected, centres, cameraHeight);
      this._progress(1, `Done — ${total.toLocaleString()} points`);

      const path = [];
      for (let i = 0; i < centres.length; i += 3) path.push([centres[i], centres[i + 1], centres[i + 2]]);
      return { points: total, transform, path };
    } finally {
      sweeper.dispose();
    }
  }

  _estimateMotion(grays, width, height) {
    return new Promise((resolve, reject) => {
      const worker = new Worker(new URL('./motion.worker.js', import.meta.url), { type: 'module' });
      this.worker = worker;
      worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.type === 'progress') {
          this._progress(0.16 + 0.25 * (msg.pair / msg.total), `Camera motion, pair ${msg.pair} of ${msg.total}…`);
        } else if (msg.type === 'done') {
          worker.terminate();
          this.worker = null;
          resolve(msg);
        } else if (msg.type === 'error') {
          worker.terminate();
          this.worker = null;
          reject(new Error(msg.message));
        }
      };
      worker.onerror = (event) => reject(new Error(event.message || 'Motion estimation failed.'));
      const buffers = grays.map((g) => g.buffer);
      worker.postMessage({ type: 'estimate', grays, width, height }, buffers);
    });
  }

  /**
   * Fix the one unknown a single moving camera cannot recover: absolute size.
   * The floor plane plus a known camera height turns the cloud metric, and
   * levels it at the same time.
   */
  _metricScale(chunks, centres, cameraHeight) {
    const sample = sampleFloorCandidates(chunks, 60000, centres);
    if (!sample) {
      this._log('floor not found — the model stays in relative units, use Calibrate scale');
      return null;
    }
    const plane = estimateFloor(sample.points, sample.medianRange);
    const candidates = sample.points.length / 3;
    const enough = plane && plane.inliers >= Math.max(300, candidates / 40);

    // Signed, not absolute: the camera has to be above the surface, otherwise
    // what was found is a ceiling or a mis-fit and the scale would be nonsense.
    const heights = [];
    if (plane) {
      for (let i = 0; i < centres.length; i += 3) {
        heights.push(
          plane.normal[0] * centres[i] + plane.normal[1] * centres[i + 1] + plane.normal[2] * centres[i + 2] + plane.d
        );
      }
    }
    const relative = plane ? median(heights) : 0;

    if (!enough || !(relative > 1e-6)) {
      this._log(
        `floor not found (${candidates.toLocaleString()} candidates, ` +
        `${plane ? `${plane.inliers.toLocaleString()} on the best plane` : 'nothing horizontal'}) ` +
        '— the model stays in relative units, use Calibrate scale'
      );
      return null;
    }

    const scale = cameraHeight / relative;
    const r = alignToUp(plane.normal);
    this._log(`floor from ${plane.inliers.toLocaleString()} points; camera ${relative.toFixed(3)} units above it → scale ${scale.toFixed(4)}`);

    // Rotating the floor normal onto +Y makes a floor point's height equal
    // n.x = -d, so scaling and adding d*scale drops the floor onto y = 0.
    // Column-major, as three.js Matrix4 expects.
    const m = new Float32Array(16);
    m[0] = r[0] * scale; m[4] = r[1] * scale; m[8] = r[2] * scale; m[12] = 0;
    m[1] = r[3] * scale; m[5] = r[4] * scale; m[9] = r[5] * scale; m[13] = plane.d * scale;
    m[2] = r[6] * scale; m[6] = r[7] * scale; m[10] = r[8] * scale; m[14] = 0;
    m[3] = 0; m[7] = 0; m[11] = 0; m[15] = 1;
    return m;
  }
}

function trajectoryLength(centres) {
  let total = 0;
  for (let i = 3; i < centres.length; i += 3) {
    total += Math.hypot(
      centres[i] - centres[i - 3],
      centres[i + 1] - centres[i - 2],
      centres[i + 2] - centres[i - 1]
    );
  }
  return total;
}

/**
 * Turn one sweep result into world-space coloured points.
 * Rejects low-confidence and textureless pixels, hypotheses that ran into the
 * ends of the depth range, and the poles (selfie stick below, sky above).
 */
function unproject(result, image, centres, rotations, index, options) {
  const { layers, minConfidence, minTexture, maxLatitudeDeg, budget, medianDepth } = options;
  const width = image.width;
  const height = image.height;
  const rotation = rotations[index];
  const cx = centres[index * 3];
  const cy = centres[index * 3 + 1];
  const cz = centres[index * 3 + 2];

  const latLimit = (maxLatitudeDeg * Math.PI) / 180;
  const keep = [];
  for (let y = 0; y < height; y++) {
    const lat = Math.PI / 2 - ((y + 0.5) / height) * Math.PI;
    if (Math.abs(lat) > latLimit) continue; // selfie stick below, sky above
    for (let x = 0; x < width; x++) {
      const p = (y * width + x) * 4;
      const confidence = result[p + 1];
      const layer = result[p + 2];
      const texture = result[p + 3];
      if (confidence < minConfidence || texture < minTexture) continue;
      if (layer <= 0 || layer >= layers - 1) continue;
      keep.push(y * width + x);
    }
  }

  const stride = keep.length > budget ? Math.ceil(keep.length / budget) : 1;
  const count = Math.floor(keep.length / stride);
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const confidences = new Float32Array(count);
  const dir = [0, 0, 0];
  const world = [0, 0, 0];

  for (let i = 0, k = 0; i < count; i++, k += stride) {
    const pixel = keep[k];
    const x = pixel % width;
    const y = (pixel / width) | 0;
    const depth = result[pixel * 4];
    pixelToDirection(x, y, width, height, dir);
    applyMat3(rotation, dir, world);
    positions[i * 3] = cx + world[0] * depth;
    positions[i * 3 + 1] = cy + world[1] * depth;
    positions[i * 3 + 2] = cz + world[2] * depth;
    const p = pixel * 4;
    colors[i * 3] = image.data[p] / 255;
    colors[i * 3 + 1] = image.data[p + 1] / 255;
    colors[i * 3 + 2] = image.data[p + 2] / 255;
    confidences[i] = result[pixel * 4 + 1];
  }

  const cell = Math.max(medianDepth / 40, 1e-4);
  const cleaned = removeSparse(positions, colors, confidences, cell, 4);
  return { ...cleaned, keyframe: index };
}

/**
 * Points that could plausibly be floor.
 *
 * Preference goes to what lies directly beneath the walked path: that strip is
 * seen from many keyframes at close range, so it is the best reconstructed
 * surface in the model, and it is unambiguously the floor the operator was
 * standing on. Only if too little of it survives does this fall back to the
 * lowest slice of the whole cloud.
 */
function sampleFloorCandidates(chunks, limit, centres) {
  let total = 0;
  for (const chunk of chunks) total += chunk.count;
  if (total < 500) return null;

  const step = Math.max(1, Math.floor(total / limit));
  const points = [];
  let index = 0;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.count; i++, index++) {
      if (index % step !== 0) continue;
      points.push(chunk.positions[i * 3], chunk.positions[i * 3 + 1], chunk.positions[i * 3 + 2]);
    }
  }
  if (points.length < 300) return null;

  // Scene scale, used to size the plane-fitting band.
  let cx = 0, cy = 0, cz = 0;
  const poses = centres.length / 3;
  for (let i = 0; i < centres.length; i += 3) {
    cx += centres[i]; cy += centres[i + 1]; cz += centres[i + 2];
  }
  cx /= poses; cy /= poses; cz /= poses;
  const ranges = [];
  for (let i = 0; i < points.length; i += 3) {
    ranges.push(Math.hypot(points[i] - cx, points[i + 1] - cy, points[i + 2] - cz));
  }
  ranges.sort((a, b) => a - b);
  const medianRange = ranges[ranges.length >> 1] || 1;

  // Everything within a stride or so of the path, and below it.
  const radius = 1.5;   // baseline units; one unit is one keyframe of walking
  const margin = 0.3;
  const underfoot = [];
  for (let i = 0; i < points.length; i += 3) {
    let best = Infinity;
    let bestY = 0;
    for (let c = 0; c < centres.length; c += 3) {
      const d = Math.hypot(points[i] - centres[c], points[i + 2] - centres[c + 2]);
      if (d < best) { best = d; bestY = centres[c + 1]; }
    }
    if (best < radius && points[i + 1] < bestY - margin) {
      underfoot.push(points[i], points[i + 1], points[i + 2]);
    }
  }
  if (underfoot.length >= 900) {
    return { points: Float32Array.from(underfoot), medianRange };
  }

  // Fallback: the lowest slice of the model as a whole.
  const ys = [];
  for (let i = 1; i < points.length; i += 3) ys.push(points[i]);
  ys.sort((a, b) => a - b);
  const cutoff = ys[Math.floor(ys.length * 0.3)];
  const filtered = [];
  for (let i = 0; i < points.length; i += 3) {
    if (points[i + 1] <= cutoff) filtered.push(points[i], points[i + 1], points[i + 2]);
  }
  return {
    points: Float32Array.from(filtered.length > 300 ? filtered : points),
    medianRange,
  };
}
