/**
 * Image operations for equirectangular frames, on plain Float32Arrays so they
 * run in a worker. Ports of the numpy versions in tools/spherical.py.
 */

import { directionToPixel, pixelToDirection } from './geometry.js';

export function toGray(rgba, width, height) {
  const out = new Float32Array(width * height);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) {
    out[i] = (0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]) / 255;
  }
  return out;
}

/** Area-average downscale to an exact 2:1 equirectangular size. */
export function resizeGray(src, sw, sh, dw, dh) {
  const out = new Float32Array(dw * dh);
  const fx = sw / dw;
  const fy = sh / dh;
  for (let y = 0; y < dh; y++) {
    const y0 = Math.floor(y * fy);
    const y1 = Math.min(sh, Math.max(y0 + 1, Math.floor((y + 1) * fy)));
    for (let x = 0; x < dw; x++) {
      const x0 = Math.floor(x * fx);
      const x1 = Math.min(sw, Math.max(x0 + 1, Math.floor((x + 1) * fx)));
      let sum = 0, count = 0;
      for (let yy = y0; yy < y1; yy++) {
        for (let xx = x0; xx < x1; xx++) { sum += src[yy * sw + xx]; count++; }
      }
      out[y * dw + x] = sum / Math.max(count, 1);
    }
  }
  return out;
}

/** Separable box blur; wraps horizontally because the image is a full sphere. */
export function boxBlur(src, width, height, radius) {
  if (radius <= 0) return src.slice();
  const k = 2 * radius + 1;
  const tmp = new Float32Array(width * height);
  const out = new Float32Array(width * height);

  for (let y = 0; y < height; y++) {
    const row = y * width;
    let sum = 0;
    for (let i = -radius; i <= radius; i++) sum += src[row + ((i % width) + width) % width];
    for (let x = 0; x < width; x++) {
      tmp[row + x] = sum / k;
      const outIdx = ((x - radius) % width + width) % width;
      const inIdx = ((x + radius + 1) % width + width) % width;
      sum += src[row + inIdx] - src[row + outIdx];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let i = -radius; i <= radius; i++) {
      sum += tmp[Math.min(height - 1, Math.max(0, i)) * width + x];
    }
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / k;
      const outY = Math.min(height - 1, Math.max(0, y - radius));
      const inY = Math.min(height - 1, Math.max(0, y + radius + 1));
      sum += tmp[inY * width + x] - tmp[outY * width + x];
    }
  }
  return out;
}

/** Local contrast only: removes exposure and vignetting differences. */
export function highPass(src, width, height, radius = 5) {
  const blurred = boxBlur(src, width, height, radius);
  const out = new Float32Array(src.length);
  for (let i = 0; i < src.length; i++) out[i] = src[i] - blurred[i];
  return out;
}

export function sampleBilinear(img, width, height, x, y) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const xa = ((x0 % width) + width) % width;
  const xb = ((x0 + 1) % width + width) % width;
  const ya = Math.min(height - 1, Math.max(0, y0));
  const yb = Math.min(height - 1, Math.max(0, y0 + 1));
  const top = img[ya * width + xa] * (1 - fx) + img[ya * width + xb] * fx;
  const bottom = img[yb * width + xa] * (1 - fx) + img[yb * width + xb] * fx;
  return top * (1 - fy) + bottom * fy;
}

/**
 * Halve an image, low-passing first with a [1 2 1] kernel.
 *
 * Plain 2x2 averaging aliases fine repeating detail — tiling, railings, brick —
 * into the coarse pyramid levels, and a bad coarse match cannot be recovered by
 * the small search windows used further down.
 */
export function downsample2(img, width, height) {
  const w = width >> 1;
  const h = height >> 1;
  const tmp = new Float32Array(width * height);
  for (let y = 0; y < height; y++) {
    const row = y * width;
    for (let x = 0; x < width; x++) {
      const left = img[row + ((x - 1 + width) % width)];
      const right = img[row + ((x + 1) % width)];
      tmp[row + x] = 0.25 * left + 0.5 * img[row + x] + 0.25 * right;
    }
  }
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y * 2 - 1);
    const y1 = y * 2;
    const y2 = Math.min(height - 1, y * 2 + 1);
    for (let x = 0; x < w; x++) {
      const sx = x * 2;
      out[y * w + x] =
        0.25 * tmp[y0 * width + sx] + 0.5 * tmp[y1 * width + sx] + 0.25 * tmp[y2 * width + sx];
    }
  }
  return out;
}

/**
 * Resample an equirectangular image at directions rotated by `rotation`
 * (row-major 3x3), removing that rotation from the frame.
 */
export function warpEquirect(img, width, height, rotation) {
  const out = new Float32Array(width * height);
  const dir = [0, 0, 0];
  const rotated = [0, 0, 0];
  const uv = [0, 0];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      pixelToDirection(x, y, width, height, dir);
      rotated[0] = rotation[0] * dir[0] + rotation[1] * dir[1] + rotation[2] * dir[2];
      rotated[1] = rotation[3] * dir[0] + rotation[4] * dir[1] + rotation[5] * dir[2];
      rotated[2] = rotation[6] * dir[0] + rotation[7] * dir[1] + rotation[8] * dir[2];
      directionToPixel(rotated, width, height, uv);
      out[y * width + x] = sampleBilinear(img, width, height, uv[0], uv[1]);
    }
  }
  return out;
}

/**
 * Coarse yaw (and slight pitch) between two frames, as an image shift.
 *
 * The search is deliberately bounded: a walking capture cannot swing far
 * between keyframes, and repeating architecture creates convincing false
 * matches all the way around the sphere.
 */
export function estimateShift(a, b, width, height, { maxYawDeg = 30, maxPitchPx = 3, bandDeg = 45 } = {}) {
  const band = Math.round((height * bandDeg) / 180);
  const r0 = Math.max(1, (height >> 1) - (band >> 1));
  const r1 = Math.min(height - 1, (height >> 1) + (band >> 1));
  const limit = Math.max(2, Math.round((width * maxYawDeg) / 360));

  let best = { cost: Infinity, dx: 0, dy: 0 };
  const costs = new Map();
  for (let dy = -maxPitchPx; dy <= maxPitchPx; dy++) {
    for (let dx = -limit; dx <= limit; dx++) {
      let sum = 0;
      let count = 0;
      for (let y = r0; y < r1; y += 2) {
        const ys = Math.min(height - 1, Math.max(0, y + dy));
        const rowA = y * width;
        const rowB = ys * width;
        for (let x = 0; x < width; x += 2) {
          const xs = ((x + dx) % width + width) % width;
          sum += Math.abs(a[rowA + x] - b[rowB + xs]);
          count++;
        }
      }
      const cost = sum / count;
      if (dy === 0) costs.set(dx, cost);
      if (cost < best.cost) best = { cost, dx, dy };
    }
  }

  let sub = 0;
  if (best.dy === 0 && costs.has(best.dx - 1) && costs.has(best.dx + 1)) {
    const c0 = costs.get(best.dx - 1);
    const c1 = costs.get(best.dx);
    const c2 = costs.get(best.dx + 1);
    const denom = c0 - 2 * c1 + c2;
    if (Math.abs(denom) > 1e-9) sub = Math.max(-1, Math.min(1, (0.5 * (c0 - c2)) / denom));
  }
  return { dx: best.dx + sub, dy: best.dy };
}

/**
 * Coarse-to-fine block matching for a sparse set of points, with sub-pixel
 * refinement. Integer-only matches leave enough noise per pair to compound
 * into metres of heading drift across a long walk.
 *
 * @param {Float32Array} points flattened xy pairs at full resolution
 * @returns {{matched: Float32Array, cost: Float32Array}}
 */
export function trackPoints(a, b, width, height, points, { levels = 4, radius = 2, patch = 3 } = {}) {
  const pyrA = [{ img: a, w: width, h: height }];
  const pyrB = [{ img: b, w: width, h: height }];
  for (let i = 1; i < levels; i++) {
    const pa = pyrA[i - 1];
    const pb = pyrB[i - 1];
    if (pa.w < 32 || pa.h < 16) break;
    pyrA.push({ img: downsample2(pa.img, pa.w, pa.h), w: pa.w >> 1, h: pa.h >> 1 });
    pyrB.push({ img: downsample2(pb.img, pb.w, pb.h), w: pb.w >> 1, h: pb.h >> 1 });
  }

  const n = points.length / 2;
  const flow = new Float32Array(n * 2);
  const cost = new Float32Array(n);
  const span = 2 * radius + 1;
  const taps = (2 * patch + 1) * (2 * patch + 1);
  const ref = new Float32Array(taps);
  const cur = new Float32Array(taps);
  const grid = new Float32Array(span * span);

  for (let lvl = pyrA.length - 1; lvl >= 0; lvl--) {
    const { img: ia, w: aw, h: ah } = pyrA[lvl];
    const { img: ib } = pyrB[lvl];
    const scale = 1 / (1 << lvl);

    for (let i = 0; i < n; i++) {
      const px = points[i * 2] * scale;
      const py = points[i * 2 + 1] * scale;
      const bx = px + flow[i * 2] * scale;
      const by = py + flow[i * 2 + 1] * scale;

      let mean = 0;
      for (let oy = -patch, k = 0; oy <= patch; oy++) {
        for (let ox = -patch; ox <= patch; ox++, k++) {
          ref[k] = sampleBilinear(ia, aw, ah, px + ox, py + oy);
          mean += ref[k];
        }
      }
      mean /= taps;
      for (let k = 0; k < taps; k++) ref[k] -= mean;

      let bestCost = Infinity;
      let bestIx = radius;
      let bestIy = radius;
      for (let iy = 0; iy < span; iy++) {
        for (let ix = 0; ix < span; ix++) {
          const dx = ix - radius;
          const dy = iy - radius;
          let m = 0;
          for (let oy = -patch, k = 0; oy <= patch; oy++) {
            for (let ox = -patch; ox <= patch; ox++, k++) {
              cur[k] = sampleBilinear(ib, aw, ah, bx + ox + dx, by + oy + dy);
              m += cur[k];
            }
          }
          m /= taps;
          let sad = 0;
          for (let k = 0; k < taps; k++) sad += Math.abs(ref[k] - (cur[k] - m));
          sad /= taps;
          grid[iy * span + ix] = sad;
          if (sad < bestCost) { bestCost = sad; bestIx = ix; bestIy = iy; }
        }
      }

      const subX = parabolaOffset(
        grid[bestIy * span + Math.max(0, bestIx - 1)],
        bestCost,
        grid[bestIy * span + Math.min(span - 1, bestIx + 1)],
        bestIx > 0 && bestIx < span - 1
      );
      const subY = parabolaOffset(
        grid[Math.max(0, bestIy - 1) * span + bestIx],
        bestCost,
        grid[Math.min(span - 1, bestIy + 1) * span + bestIx],
        bestIy > 0 && bestIy < span - 1
      );

      flow[i * 2] += (bestIx - radius + subX) / scale;
      flow[i * 2 + 1] += (bestIy - radius + subY) / scale;
      cost[i] = bestCost;
    }
  }

  const matched = new Float32Array(n * 2);
  for (let i = 0; i < n * 2; i++) matched[i] = points[i] + flow[i];
  return { matched, cost };
}

function parabolaOffset(c0, c1, c2, interior) {
  if (!interior) return 0;
  const denom = c0 - 2 * c1 + c2;
  if (Math.abs(denom) < 1e-9) return 0;
  return Math.max(-0.5, Math.min(0.5, (0.5 * (c0 - c2)) / denom));
}

/** A horizon band grid of tracking points, avoiding the poles. */
export function makeTrackingGrid(width, height, cols = 72, rows = 26) {
  const points = new Float32Array(cols * rows * 2);
  let i = 0;
  for (let r = 0; r < rows; r++) {
    const y = height * 0.22 + ((height * 0.58) * r) / Math.max(1, rows - 1);
    for (let c = 0; c < cols; c++) {
      points[i++] = (width * c) / cols + width / (2 * cols);
      points[i++] = y;
    }
  }
  return points;
}
