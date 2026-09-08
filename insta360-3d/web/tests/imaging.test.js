import test from 'node:test';
import assert from 'node:assert/strict';

import {
  boxBlur,
  estimateShift,
  highPass,
  makeTrackingGrid,
  resizeGray,
  sampleBilinear,
  trackPoints,
  warpEquirect,
} from '../src/pipeline/imaging.js';
import { yawPitchMatrix } from '../src/pipeline/geometry.js';

const DEG = Math.PI / 180;
const WIDTH = 256;
const HEIGHT = 128;

/**
 * Deterministic textured sphere built from value noise.
 *
 * Deliberately non-repeating: a frame made of pure sinusoids is exactly
 * periodic around the sphere, so every match has equally good impostors one
 * period away and the test would measure that ambiguity rather than the tracker.
 */
function syntheticFrame(width = WIDTH, height = HEIGHT) {
  const hash = (ix, iy, seed) => {
    const s = Math.sin(ix * 127.1 + iy * 311.7 + seed * 74.7) * 43758.5453;
    return s - Math.floor(s);
  };
  const noise = (x, y, scale, seed) => {
    const fx = x / scale;
    const fy = y / scale;
    const ix = Math.floor(fx);
    const iy = Math.floor(fy);
    let tx = fx - ix;
    let ty = fy - iy;
    tx = tx * tx * (3 - 2 * tx);
    ty = ty * ty * (3 - 2 * ty);
    const a = hash(ix, iy, seed);
    const b = hash(ix + 1, iy, seed);
    const c = hash(ix, iy + 1, seed);
    const d = hash(ix + 1, iy + 1, seed);
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
  };

  const img = new Float32Array(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      img[y * width + x] =
        0.20 +
        0.34 * noise(x, y, 3.1, 1) +
        0.26 * noise(x, y, 9.7, 2) +
        0.20 * noise(x, y, 27.3, 3);
    }
  }
  return img;
}

test('boxBlur preserves a constant image and wraps horizontally', () => {
  const flat = new Float32Array(WIDTH * HEIGHT).fill(0.4);
  const blurred = boxBlur(flat, WIDTH, HEIGHT, 4);
  for (let i = 0; i < blurred.length; i++) {
    assert.ok(Math.abs(blurred[i] - 0.4) < 1e-5, `index ${i} became ${blurred[i]}`);
  }

  // A single bright column must bleed across the seam, not stop at it.
  const seam = new Float32Array(WIDTH * HEIGHT);
  for (let y = 0; y < HEIGHT; y++) seam[y * WIDTH] = 1;
  const spread = boxBlur(seam, WIDTH, HEIGHT, 2);
  assert.ok(spread[WIDTH - 1] > 0.1, 'blur wrapped around longitude 180');
});

test('highPass removes the low frequencies', () => {
  const ramp = new Float32Array(WIDTH * HEIGHT);
  for (let y = 0; y < HEIGHT; y++) {
    for (let x = 0; x < WIDTH; x++) ramp[y * WIDTH + x] = y / HEIGHT;
  }
  const hp = highPass(ramp, WIDTH, HEIGHT, 5);
  let maxAbs = 0;
  for (let y = 20; y < HEIGHT - 20; y++) {
    for (let x = 0; x < WIDTH; x++) maxAbs = Math.max(maxAbs, Math.abs(hp[y * WIDTH + x]));
  }
  assert.ok(maxAbs < 0.02, `smooth gradient left ${maxAbs} behind`);
});

test('sampleBilinear wraps in longitude and clamps in latitude', () => {
  const img = new Float32Array(WIDTH * HEIGHT);
  img[0] = 1;                      // (0, 0)
  img[WIDTH - 1] = 1;              // (255, 0)
  const wrapped = sampleBilinear(img, WIDTH, HEIGHT, -0.5, 0);
  assert.ok(Math.abs(wrapped - 1) < 1e-6, `wrapped sample ${wrapped}`);
  const clamped = sampleBilinear(img, WIDTH, HEIGHT, 0, -3);
  assert.ok(Math.abs(clamped - 1) < 1e-6, `clamped sample ${clamped}`);
});

test('warpEquirect by a yaw shifts content by the matching longitude', () => {
  const frame = syntheticFrame();
  const yaw = 15 * DEG;
  const warped = warpEquirect(frame, WIDTH, HEIGHT, yawPitchMatrix(yaw, 0));
  const shift = (yaw / (2 * Math.PI)) * WIDTH;

  let error = 0;
  let count = 0;
  for (let y = 30; y < HEIGHT - 30; y++) {
    for (let x = 0; x < WIDTH; x++) {
      const expected = sampleBilinear(frame, WIDTH, HEIGHT, x + shift, y);
      error += Math.abs(warped[y * WIDTH + x] - expected);
      count++;
    }
  }
  assert.ok(error / count < 5e-3, `mean warp error ${(error / count).toFixed(5)}`);
});

test('estimateShift recovers a known yaw with the pipeline sign convention', () => {
  const frame = syntheticFrame();
  for (const yawDeg of [0, 6, -9]) {
    const yaw = yawDeg * DEG;
    // A frame taken after yawing by `yaw` sees the same world rotated back.
    const rotated = warpEquirect(frame, WIDTH, HEIGHT, yawPitchMatrix(yaw, 0));
    const { dx } = estimateShift(highPass(frame, WIDTH, HEIGHT, 5), highPass(rotated, WIDTH, HEIGHT, 5), WIDTH, HEIGHT);
    // motion.worker.js turns the shift into a rotation as yaw = -dx * 2pi / width.
    const recovered = (-dx / WIDTH) * 2 * Math.PI;
    assert.ok(Math.abs(recovered - yaw) / DEG < 1.2,
      `expected ${yawDeg} deg, recovered ${(recovered / DEG).toFixed(2)} deg`);
  }
});

test('trackPoints follows a known displacement to sub-pixel accuracy', () => {
  const frame = syntheticFrame();
  const shiftX = 2.6;
  const shiftY = -1.4;
  const moved = new Float32Array(WIDTH * HEIGHT);
  for (let y = 0; y < HEIGHT; y++) {
    for (let x = 0; x < WIDTH; x++) {
      // moved(x) = frame(x + shift): content appears displaced by -shift.
      moved[y * WIDTH + x] = sampleBilinear(frame, WIDTH, HEIGHT, x + shiftX, y + shiftY);
    }
  }

  const points = makeTrackingGrid(WIDTH, HEIGHT, 12, 6);
  const { matched } = trackPoints(
    highPass(frame, WIDTH, HEIGHT, 5),
    highPass(moved, WIDTH, HEIGHT, 5),
    WIDTH, HEIGHT, points
  );

  const errors = [];
  for (let i = 0; i < points.length / 2; i++) {
    errors.push(Math.hypot(
      matched[i * 2] - (points[i * 2] - shiftX),
      matched[i * 2 + 1] - (points[i * 2 + 1] - shiftY)
    ));
  }
  errors.sort((a, b) => a - b);
  const medianError = errors[errors.length >> 1];
  assert.ok(medianError < 0.35, `median tracking error ${medianError.toFixed(3)} px`);
});

test('resizeGray halves an image by averaging', () => {
  const img = new Float32Array(WIDTH * HEIGHT);
  for (let i = 0; i < img.length; i++) img[i] = (i % 7) / 7;
  const small = resizeGray(img, WIDTH, HEIGHT, WIDTH / 2, HEIGHT / 2);
  assert.equal(small.length, (WIDTH / 2) * (HEIGHT / 2));
  const expected = (img[0] + img[1] + img[WIDTH] + img[WIDTH + 1]) / 4;
  assert.ok(Math.abs(small[0] - expected) < 1e-6);
});
