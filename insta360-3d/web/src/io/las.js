/**
 * LAS 1.2 export — the format that reaches Navisworks and Trimble Connect.
 *
 * PLY is a graphics format; the AEC tools do not read it. LAS is the LiDAR
 * interchange standard, and both target viewers accept it (Trimble Connect
 * directly, Navisworks Freedom after ReCap converts it to RCP).
 *
 * Point Data Record Format 2 carries XYZ plus RGB, which is the minimum that
 * keeps a photogrammetric cloud looking like the place it was captured in.
 *
 * Axes are converted from the viewer's Y-up convention to the Z-up convention
 * every CAD package assumes, otherwise the model arrives lying on its side.
 */

const HEADER_SIZE = 227;
const POINT_SIZE = 26; // format 2: 20 bytes of geometry + 6 of colour
const SCALE = 0.001; // millimetre resolution, far finer than the reconstruction

/** Y-up (three.js) to Z-up (CAD), preserving handedness. */
function toZUp(x, y, z) {
  return [x, -z, y];
}

function writeString(view, offset, text, length) {
  const bytes = new TextEncoder().encode(text.slice(0, length));
  for (let i = 0; i < bytes.length; i++) view.setUint8(offset + i, bytes[i]);
}

export function exportLAS(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.positions.length / 3;

  // Bounds first: the offset has to keep the scaled integers inside int32.
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const chunk of chunks) {
    const p = chunk.positions;
    for (let i = 0; i < p.length; i += 3) {
      const c = toZUp(p[i], p[i + 1], p[i + 2]);
      for (let k = 0; k < 3; k++) {
        if (c[k] < min[k]) min[k] = c[k];
        if (c[k] > max[k]) max[k] = c[k];
      }
    }
  }
  if (!total) {
    min.fill(0);
    max.fill(0);
  }
  const offset = min.map((v) => Math.floor(v));

  const buffer = new ArrayBuffer(HEADER_SIZE + total * POINT_SIZE);
  const view = new DataView(buffer);

  view.setUint8(0, 0x4c); // L
  view.setUint8(1, 0x41); // A
  view.setUint8(2, 0x53); // S
  view.setUint8(3, 0x46); // F
  view.setUint8(24, 1); // version major
  view.setUint8(25, 2); // version minor
  writeString(view, 26, 'Insta360 Point Cloud Studio', 32);
  writeString(view, 58, 'Insta360 Point Cloud Studio', 32);

  const now = new Date();
  const dayOfYear = Math.floor(
    (now - new Date(now.getFullYear(), 0, 0)) / 86400000,
  );
  view.setUint16(90, dayOfYear, true);
  view.setUint16(92, now.getFullYear(), true);
  view.setUint16(94, HEADER_SIZE, true);
  view.setUint32(96, HEADER_SIZE, true);
  view.setUint32(100, 0, true); // no variable length records
  view.setUint8(104, 2); // point data record format
  view.setUint16(105, POINT_SIZE, true);
  view.setUint32(107, total, true);
  view.setUint32(111, total, true); // all points are a single first return

  view.setFloat64(131, SCALE, true);
  view.setFloat64(139, SCALE, true);
  view.setFloat64(147, SCALE, true);
  view.setFloat64(155, offset[0], true);
  view.setFloat64(163, offset[1], true);
  view.setFloat64(171, offset[2], true);
  view.setFloat64(179, max[0], true);
  view.setFloat64(187, min[0], true);
  view.setFloat64(195, max[1], true);
  view.setFloat64(203, min[1], true);
  view.setFloat64(211, max[2], true);
  view.setFloat64(219, min[2], true);

  let at = HEADER_SIZE;
  for (const chunk of chunks) {
    const { positions, colors } = chunk;
    for (let i = 0; i < positions.length / 3; i++) {
      const c = toZUp(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      view.setInt32(at, Math.round((c[0] - offset[0]) / SCALE), true);
      view.setInt32(at + 4, Math.round((c[1] - offset[1]) / SCALE), true);
      view.setInt32(at + 8, Math.round((c[2] - offset[2]) / SCALE), true);

      const r = Math.max(0, Math.min(255, Math.round(colors[i * 3] * 255)));
      const g = Math.max(0, Math.min(255, Math.round(colors[i * 3 + 1] * 255)));
      const b = Math.max(0, Math.min(255, Math.round(colors[i * 3 + 2] * 255)));
      // Intensity is what greyscale viewers fall back to when they ignore RGB.
      view.setUint16(at + 12, Math.round((0.299 * r + 0.587 * g + 0.114 * b) * 257), true);
      view.setUint8(at + 14, 0b00001001); // return 1 of 1
      view.setUint8(at + 15, 1); // unclassified
      view.setUint16(at + 20, r * 257, true);
      view.setUint16(at + 22, g * 257, true);
      view.setUint16(at + 24, b * 257, true);
      at += POINT_SIZE;
    }
  }

  return new Blob([buffer], { type: 'application/octet-stream' });
}

/**
 * PTS — plain text, the lowest common denominator.
 *
 * Worth having because every scanning package on earth imports it, including
 * ones that reject a LAS they consider malformed. Costs about six times the
 * bytes of LAS, so it is the fallback rather than the default.
 */
export function exportPTS(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.positions.length / 3;

  const parts = [`${total}\n`];
  let line = [];
  for (const chunk of chunks) {
    const { positions, colors } = chunk;
    for (let i = 0; i < positions.length / 3; i++) {
      const c = toZUp(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      const r = Math.max(0, Math.min(255, Math.round(colors[i * 3] * 255)));
      const g = Math.max(0, Math.min(255, Math.round(colors[i * 3 + 1] * 255)));
      const b = Math.max(0, Math.min(255, Math.round(colors[i * 3 + 2] * 255)));
      const intensity = Math.round(0.299 * r + 0.587 * g + 0.114 * b) - 2048;
      line.push(
        `${c[0].toFixed(4)} ${c[1].toFixed(4)} ${c[2].toFixed(4)} ${intensity} ${r} ${g} ${b}\n`,
      );
      if (line.length >= 20000) {
        parts.push(line.join(''));
        line = [];
      }
    }
  }
  parts.push(line.join(''));
  return new Blob(parts, { type: 'text/plain' });
}
