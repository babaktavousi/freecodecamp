/**
 * Spherical geometry for equirectangular reconstruction.
 *
 * Conventions match tools/spherical.py: longitude 0 at the horizontal centre of
 * the image and along +Z, +X right, +Y up, latitude +90 deg at the top row.
 * Everything here is plain arithmetic on typed arrays so it runs in a worker
 * and under `node --test` without a DOM.
 */

/** Unit ray direction for a pixel centre of an equirectangular image. */
export function pixelToDirection(x, y, width, height, out = [0, 0, 0]) {
  const lon = ((x + 0.5) / width) * 2 * Math.PI - Math.PI;
  const lat = Math.PI / 2 - ((y + 0.5) / height) * Math.PI;
  const cosLat = Math.cos(lat);
  out[0] = cosLat * Math.sin(lon);
  out[1] = Math.sin(lat);
  out[2] = cosLat * Math.cos(lon);
  return out;
}

/** Equirectangular pixel coordinates for a unit direction. */
export function directionToPixel(d, width, height, out = [0, 0]) {
  const lon = Math.atan2(d[0], d[2]);
  const lat = Math.asin(Math.max(-1, Math.min(1, d[1])));
  out[0] = ((lon + Math.PI) / (2 * Math.PI)) * width - 0.5;
  out[1] = ((Math.PI / 2 - lat) / Math.PI) * height - 0.5;
  return out;
}

export function normalize(v, out = v) {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  out[0] = v[0] / n;
  out[1] = v[1] / n;
  out[2] = v[2] / n;
  return out;
}

export function cross(a, b, out = [0, 0, 0]) {
  const x = a[1] * b[2] - a[2] * b[1];
  const y = a[2] * b[0] - a[0] * b[2];
  const z = a[0] * b[1] - a[1] * b[0];
  out[0] = x; out[1] = y; out[2] = z;
  return out;
}

export const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** Row-major 3x3 rotation about Y (yaw) then X (pitch): Ry * Rx. */
export function yawPitchMatrix(yaw, pitch) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  return new Float32Array([
    cy, sy * sp, sy * cp,
    0, cp, -sp,
    -sy, cy * sp, cy * cp,
  ]);
}

/** m * v for a row-major 3x3. */
export function applyMat3(m, v, out = [0, 0, 0]) {
  const x = m[0] * v[0] + m[1] * v[1] + m[2] * v[2];
  const y = m[3] * v[0] + m[4] * v[1] + m[5] * v[2];
  const z = m[6] * v[0] + m[7] * v[1] + m[8] * v[2];
  out[0] = x; out[1] = y; out[2] = z;
  return out;
}

/** transpose(m) * v, i.e. world -> camera for a rotation matrix. */
export function applyMat3Transpose(m, v, out = [0, 0, 0]) {
  const x = m[0] * v[0] + m[3] * v[1] + m[6] * v[2];
  const y = m[1] * v[0] + m[4] * v[1] + m[7] * v[2];
  const z = m[2] * v[0] + m[5] * v[1] + m[8] * v[2];
  out[0] = x; out[1] = y; out[2] = z;
  return out;
}

/** Smallest-eigenvalue eigenvector of a symmetric 3x3 (row-major). */
export function smallestEigenvector(m) {
  // Shift by the largest Gershgorin bound so inverse power iteration on
  // (mu*I - M) converges to the smallest eigenvector without a matrix inverse.
  let mu = 0;
  for (let r = 0; r < 3; r++) {
    const rowSum = Math.abs(m[r * 3]) + Math.abs(m[r * 3 + 1]) + Math.abs(m[r * 3 + 2]);
    mu = Math.max(mu, rowSum);
  }
  const s = [
    mu - m[0], -m[1], -m[2],
    -m[3], mu - m[4], -m[5],
    -m[6], -m[7], mu - m[8],
  ];
  let v = [0.577, 0.577, 0.577];
  for (let i = 0; i < 64; i++) {
    const x = s[0] * v[0] + s[1] * v[1] + s[2] * v[2];
    const y = s[3] * v[0] + s[4] * v[1] + s[5] * v[2];
    const z = s[6] * v[0] + s[7] * v[1] + s[8] * v[2];
    const n = Math.hypot(x, y, z);
    if (n < 1e-20) break;
    v = [x / n, y / n, z / n];
  }
  return v;
}

/** Rodrigues exponential map for a rotation vector (row-major 3x3). */
export function expmSO3(w) {
  const theta = Math.hypot(w[0], w[1], w[2]);
  if (theta < 1e-12) return new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  const k = [w[0] / theta, w[1] / theta, w[2] / theta];
  const s = Math.sin(theta);
  const c = 1 - Math.cos(theta);
  const kx = [0, -k[2], k[1], k[2], 0, -k[0], -k[1], k[0], 0];
  const out = new Float32Array(9);
  for (let r = 0; r < 3; r++) {
    for (let col = 0; col < 3; col++) {
      let kk = 0;
      for (let t = 0; t < 3; t++) kk += kx[r * 3 + t] * kx[t * 3 + col];
      out[r * 3 + col] = (r === col ? 1 : 0) + s * kx[r * 3 + col] + c * kk;
    }
  }
  return out;
}

/** a * b for row-major 3x3 matrices. */
export function multiplyMat3(a, b) {
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

/** Solve a 3x3 system by Cramer's rule; null when near-singular. */
export function solve3(m, rhs) {
  const det =
    m[0] * (m[4] * m[8] - m[5] * m[7]) -
    m[1] * (m[3] * m[8] - m[5] * m[6]) +
    m[2] * (m[3] * m[7] - m[4] * m[6]);
  if (Math.abs(det) < 1e-18) return null;
  const out = [0, 0, 0];
  for (let col = 0; col < 3; col++) {
    const c = m.slice();
    c[col] = rhs[0]; c[col + 3] = rhs[1]; c[col + 6] = rhs[2];
    out[col] =
      (c[0] * (c[4] * c[8] - c[5] * c[7]) -
        c[1] * (c[3] * c[8] - c[5] * c[6]) +
        c[2] * (c[3] * c[7] - c[4] * c[6])) / det;
  }
  return out;
}

/**
 * Joint relative rotation and translation direction from correspondences.
 *
 * `p` are unit directions in frame A, `q` the matching directions in frame B;
 * both satisfy the spherical epipolar constraint (p x (R q)) . t = 0, where R
 * maps B's camera frame into A's. Alternating the closed-form null space for t
 * with a small-angle least-squares update for R converges from a yaw-only
 * start and, unlike the eight-point algorithm, needs no SVD.
 *
 * @returns {{rotation: Float32Array, translation: number[], inliers: number}}
 */
export function refinePose(p, q, rotationInit, iterations = 6) {
  const n = p.length / 3;
  let rotation = Float32Array.from(rotationInit);
  let t = [0, 0, 1];
  const weights = new Float64Array(n).fill(1);
  const qr = new Float32Array(n * 3);
  const normals = new Float32Array(n * 3);
  const norms = new Float64Array(n);
  const residuals = new Float64Array(n);
  const a = [0, 0, 0], b = [0, 0, 0], c = [0, 0, 0];

  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < n; i++) {
      b[0] = q[i * 3]; b[1] = q[i * 3 + 1]; b[2] = q[i * 3 + 2];
      applyMat3(rotation, b, c);
      qr[i * 3] = c[0]; qr[i * 3 + 1] = c[1]; qr[i * 3 + 2] = c[2];
      a[0] = p[i * 3]; a[1] = p[i * 3 + 1]; a[2] = p[i * 3 + 2];
      cross(a, c, b);
      const len = Math.hypot(b[0], b[1], b[2]);
      norms[i] = len;
      if (len > 1e-7) {
        normals[i * 3] = b[0] / len;
        normals[i * 3 + 1] = b[1] / len;
        normals[i * 3 + 2] = b[2] / len;
      } else {
        normals[i * 3] = normals[i * 3 + 1] = normals[i * 3 + 2] = 0;
      }
    }

    const m = new Float64Array(9);
    for (let i = 0; i < n; i++) {
      const w = weights[i];
      const x = normals[i * 3], y = normals[i * 3 + 1], z = normals[i * 3 + 2];
      m[0] += w * x * x; m[1] += w * x * y; m[2] += w * x * z;
      m[4] += w * y * y; m[5] += w * y * z; m[8] += w * z * z;
    }
    m[3] = m[1]; m[6] = m[2]; m[7] = m[5];
    const tNew = smallestEigenvector(m);
    if (dot(tNew, t) < 0) { tNew[0] = -tNew[0]; tNew[1] = -tNew[1]; tNew[2] = -tNew[2]; }
    t = tNew;

    for (let i = 0; i < n; i++) {
      residuals[i] = normals[i * 3] * t[0] + normals[i * 3 + 1] * t[1] + normals[i * 3 + 2] * t[2];
    }
    const scale = Math.max(1.4826 * median(residuals.map(Math.abs)), 1e-5);
    for (let i = 0; i < n; i++) {
      const r = residuals[i] / (3 * scale);
      weights[i] = norms[i] > 1e-7 ? 1 / (1 + r * r) : 0;
    }

    // J = (t (p . qr) - p (qr . t)) / |p x qr|, scaled like the residual so the
    // iteration does not drift.
    const hess = new Float64Array(9);
    const grad = [0, 0, 0];
    const jac = [0, 0, 0];
    for (let i = 0; i < n; i++) {
      if (norms[i] <= 1e-7) continue;
      const inv = 1 / norms[i];
      const px = p[i * 3], py = p[i * 3 + 1], pz = p[i * 3 + 2];
      const qx = qr[i * 3], qy = qr[i * 3 + 1], qz = qr[i * 3 + 2];
      const pq = px * qx + py * qy + pz * qz;
      const qt = qx * t[0] + qy * t[1] + qz * t[2];
      jac[0] = (t[0] * pq - px * qt) * inv;
      jac[1] = (t[1] * pq - py * qt) * inv;
      jac[2] = (t[2] * pq - pz * qt) * inv;
      const w = weights[i];
      for (let r = 0; r < 3; r++) {
        grad[r] += w * jac[r] * residuals[i];
        for (let col = 0; col < 3; col++) hess[r * 3 + col] += w * jac[r] * jac[col];
      }
    }
    hess[0] += 1e-9; hess[4] += 1e-9; hess[8] += 1e-9;

    const delta = solve3(hess, grad);
    if (!delta) break;
    const omega = delta.map((v) => Math.max(-0.2, Math.min(0.2, -v)));
    if (Math.hypot(...omega) < 1e-7) break;
    rotation = multiplyMat3(expmSO3(omega), rotation);
  }

  let inliers = 0;
  for (let i = 0; i < n; i++) if (weights[i] > 0.5) inliers++;
  return { rotation, translation: t, inliers };
}

/** Count of correspondences whose epipolar residual is below `threshold`. */
export function countInliers(p, q, rotation, t, threshold = 0.004) {
  const n = p.length / 3;
  const a = [0, 0, 0], b = [0, 0, 0], c = [0, 0, 0];
  let count = 0;
  for (let i = 0; i < n; i++) {
    b[0] = q[i * 3]; b[1] = q[i * 3 + 1]; b[2] = q[i * 3 + 2];
    applyMat3(rotation, b, c);
    a[0] = p[i * 3]; a[1] = p[i * 3 + 1]; a[2] = p[i * 3 + 2];
    cross(a, c, b);
    const len = Math.hypot(b[0], b[1], b[2]);
    if (len < 1e-7) continue;
    if (Math.abs((b[0] * t[0] + b[1] * t[1] + b[2] * t[2]) / len) < threshold) count++;
  }
  return count;
}

/**
 * Depth along p in units of the baseline, for a pure-translation pair.
 * Z/b = |q x t| / |q x p|, negative when the point is behind the camera.
 */
export function triangulateDepth(p, q, t) {
  const qxp = cross(q, p);
  const qxt = cross(q, t);
  const denom = Math.hypot(qxp[0], qxp[1], qxp[2]);
  if (denom < 1e-6) return NaN;
  const z = Math.hypot(qxt[0], qxt[1], qxt[2]) / denom;
  return dot(qxp, qxt) >= 0 ? z : -z;
}

export function median(values) {
  const arr = Array.from(values).filter(Number.isFinite).sort((a, b) => a - b);
  if (!arr.length) return NaN;
  const mid = arr.length >> 1;
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

/**
 * Relative baseline per pair from the median triangulated depth.
 *
 * Depth comes out in units of its own pair's baseline, so when the scene depth
 * changes slowly between neighbouring keyframes b_{i+1}/b_i = m_i/m_{i+1}.
 * Ratios are clamped so one bad pair cannot wreck the whole chain.
 */
export function baselinesFromDepths(medians, smooth = 5) {
  const n = medians.length;
  if (!n) return [];
  const sm = new Float64Array(n);
  const half = Math.max(0, Math.floor(smooth / 2));
  for (let i = 0; i < n; i++) {
    let sum = 0, count = 0;
    for (let k = -half; k <= half; k++) {
      const j = Math.min(n - 1, Math.max(0, i + k));
      sum += medians[j];
      count++;
    }
    sm[i] = sum / count;
  }
  const b = new Float64Array(n);
  b[0] = 1;
  for (let i = 1; i < n; i++) {
    const ratio = Math.min(1.6, Math.max(0.6, sm[i - 1] / Math.max(sm[i], 1e-6)));
    b[i] = b[i - 1] * ratio;
  }
  const norm = median(b) || 1;
  for (let i = 0; i < n; i++) b[i] /= norm;
  return Array.from(b);
}

/**
 * Chain relative poses into world rotations and camera centres.
 *
 * Insta360 X-series clips are exported horizon locked, so by default the
 * accumulated rotation is projected back onto its yaw component: a few tenths
 * of a degree of tilt error per pair otherwise compounds until floors bend and
 * the metric floor fit has nothing planar to lock onto.
 *
 * @returns {{centres: Float32Array, rotations: Float32Array[]}}
 */
export function buildPoses(relativeRotations, directions, baselines, lockHorizon = true) {
  const rotations = [new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1])];
  const n = relativeRotations.length + 1;
  const centres = new Float32Array(n * 3);
  const step = [0, 0, 0];

  for (let i = 0; i < relativeRotations.length; i++) {
    const d = directions[i];
    const b = baselines[i];
    applyMat3(rotations[i], [d[0] * b, d[1] * b, d[2] * b], step);
    centres[(i + 1) * 3] = centres[i * 3] + step[0];
    centres[(i + 1) * 3 + 1] = centres[i * 3 + 1] + step[1];
    centres[(i + 1) * 3 + 2] = centres[i * 3 + 2] + step[2];

    let next = multiplyMat3(rotations[i], relativeRotations[i]);
    if (lockHorizon) next = yawPitchMatrix(Math.atan2(next[2], next[8]), 0);
    rotations.push(next);
  }
  return { centres, rotations };
}

/**
 * Locate the floor in a reconstructed cloud.
 *
 * RANSAC alone struggles here: a reconstructed floor is a slab tens of
 * centimetres thick, not a surface, so a band tight enough to be selective
 * locks onto one face of it while a band loose enough to capture it stops
 * discriminating. Instead the densest height layer is found first — the floor
 * is the flattest, most-seen surface in a walking capture — and a plane is
 * fitted to that layer by reweighted least squares.
 *
 * @param {Float32Array} points flattened Nx3, ideally the lower part of the cloud
 * @param {number} scaleHint typical scene range, used to size the bins
 * @returns {{normal: number[], d: number, inliers: number}|null}
 */
export function estimateFloor(points, scaleHint) {
  const n = points.length / 3;
  if (n < 200) return null;

  const ys = new Float64Array(n);
  for (let i = 0; i < n; i++) ys[i] = points[i * 3 + 1];
  const sorted = Float64Array.from(ys).sort();
  const low = sorted[Math.floor(n * 0.01)];
  const high = sorted[Math.floor(n * 0.99)];
  const span = high - low;
  if (!(span > 0)) return null;

  const bin = Math.max(scaleHint / 200, span / 400, 1e-6);
  const bins = Math.max(4, Math.ceil(span / bin));
  const counts = new Int32Array(bins);
  for (let i = 0; i < n; i++) {
    const b = Math.floor((ys[i] - low) / bin);
    if (b >= 0 && b < bins) counts[b]++;
  }
  // Smooth over three bins so a single spike cannot win.
  let bestBin = 0;
  let bestCount = -1;
  for (let b = 0; b < bins; b++) {
    const c = (counts[b - 1] ?? 0) + counts[b] + (counts[b + 1] ?? 0);
    if (c > bestCount) { bestCount = c; bestBin = b; }
  }
  const level = low + (bestBin + 0.5) * bin;

  const band = Math.max(scaleHint / 40, bin * 4);
  const slab = [];
  for (let i = 0; i < n; i++) {
    if (Math.abs(ys[i] - level) <= band) {
      slab.push(points[i * 3], ys[i], points[i * 3 + 2]);
    }
  }
  if (slab.length < 300) return null;

  // Reweighted least squares on the slab: start from horizontal and let the
  // fit tilt only as far as the points actually support.
  let normal = [0, 1, 0];
  let d = -level;
  const count = slab.length / 3;
  const weights = new Float64Array(count).fill(1);
  for (let iter = 0; iter < 5; iter++) {
    let sw = 0, sx = 0, sy = 0, sz = 0;
    for (let i = 0; i < count; i++) {
      const w = weights[i];
      sw += w;
      sx += w * slab[i * 3];
      sy += w * slab[i * 3 + 1];
      sz += w * slab[i * 3 + 2];
    }
    if (sw < 1e-9) break;
    const cx = sx / sw, cy = sy / sw, cz = sz / sw;

    const cov = new Float64Array(9);
    for (let i = 0; i < count; i++) {
      const w = weights[i];
      const x = slab[i * 3] - cx;
      const y = slab[i * 3 + 1] - cy;
      const z = slab[i * 3 + 2] - cz;
      cov[0] += w * x * x; cov[1] += w * x * y; cov[2] += w * x * z;
      cov[4] += w * y * y; cov[5] += w * y * z; cov[8] += w * z * z;
    }
    cov[3] = cov[1]; cov[6] = cov[2]; cov[7] = cov[5];
    normal = smallestEigenvector(cov);
    if (normal[1] < 0) { normal[0] = -normal[0]; normal[1] = -normal[1]; normal[2] = -normal[2]; }
    d = -(normal[0] * cx + normal[1] * cy + normal[2] * cz);

    const residuals = new Float64Array(count);
    for (let i = 0; i < count; i++) {
      residuals[i] = Math.abs(
        normal[0] * slab[i * 3] + normal[1] * slab[i * 3 + 1] + normal[2] * slab[i * 3 + 2] + d
      );
    }
    const scale = Math.max(1.4826 * median(residuals), band / 20);
    for (let i = 0; i < count; i++) {
      const r = residuals[i] / (2 * scale);
      weights[i] = 1 / (1 + r * r);
    }
  }

  // A floor that came out steeply tilted is not a floor.
  if (normal[1] < 0.9) return null;
  // Support is counted over the whole band: a reconstructed floor is a slab,
  // and a thinner test would reject good fits on noisier models.
  let inliers = 0;
  for (let i = 0; i < count; i++) {
    const dist = Math.abs(
      normal[0] * slab[i * 3] + normal[1] * slab[i * 3 + 1] + normal[2] * slab[i * 3 + 2] + d
    );
    if (dist < band) inliers++;
  }
  return { normal, d, inliers };
}

/** Rotation (row-major 3x3) taking `normal` onto +Y. */
export function alignToUp(normal) {
  const n = normalize([...normal]);
  const up = [0, 1, 0];
  const v = cross(n, up);
  const s = Math.hypot(v[0], v[1], v[2]);
  if (s < 1e-8) return new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  const c = dot(n, up);
  const k = (1 - c) / (s * s);
  const vx = [0, -v[2], v[1], v[2], 0, -v[0], -v[1], v[0], 0];
  const out = new Float32Array(9);
  for (let r = 0; r < 3; r++) {
    for (let col = 0; col < 3; col++) {
      let sum = r === col ? 1 : 0;
      sum += vx[r * 3 + col];
      let vv = 0;
      for (let t = 0; t < 3; t++) vv += vx[r * 3 + t] * vx[t * 3 + col];
      out[r * 3 + col] = sum + vv * k;
    }
  }
  return out;
}

/**
 * Drop points sitting in near-empty cells of a coarse grid.
 *
 * A real surface is seen by several keyframes and fills its neighbourhood with
 * points; a mismatched pixel lands one on its own. Removing those keeps stray
 * speckle from dominating the bounding box and the zoom-to-fit.
 *
 * @returns {{positions: Float32Array, colors: Float32Array, confidences: Float32Array|null, count: number}}
 */
export function removeSparse(positions, colors, confidences, cell, minCount) {
  const n = positions.length / 3;
  const counts = new Map();
  const keys = new Float64Array(n);
  const inv = 1 / cell;
  for (let i = 0; i < n; i++) {
    const gx = Math.floor(positions[i * 3] * inv);
    const gy = Math.floor(positions[i * 3 + 1] * inv);
    const gz = Math.floor(positions[i * 3 + 2] * inv);
    const key = ((gx & 0x1fffff) * 2097152 + (gy & 0x1fffff)) * 2097152 + (gz & 0x1fffff);
    keys[i] = key;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  let kept = 0;
  for (let i = 0; i < n; i++) if (counts.get(keys[i]) >= minCount) kept++;
  if (kept === n) return { positions, colors, confidences, count: n };

  const outP = new Float32Array(kept * 3);
  const outC = new Float32Array(kept * 3);
  const outConf = confidences ? new Float32Array(kept) : null;
  for (let i = 0, k = 0; i < n; i++) {
    if (counts.get(keys[i]) < minCount) continue;
    outP[k * 3] = positions[i * 3];
    outP[k * 3 + 1] = positions[i * 3 + 1];
    outP[k * 3 + 2] = positions[i * 3 + 2];
    outC[k * 3] = colors[i * 3];
    outC[k * 3 + 1] = colors[i * 3 + 1];
    outC[k * 3 + 2] = colors[i * 3 + 2];
    if (outConf) outConf[k] = confidences[i];
    k++;
  }
  return { positions: outP, colors: outC, confidences: outConf, count: kept };
}
