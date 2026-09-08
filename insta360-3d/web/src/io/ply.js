/** Binary PLY export, readable by CloudCompare, MeshLab, Recap and Blender. */
export function exportPLY(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.positions.length / 3;

  const header =
    'ply\nformat binary_little_endian 1.0\n' +
    'comment created by Insta360 Point Cloud Studio\n' +
    `element vertex ${total}\n` +
    'property float x\nproperty float y\nproperty float z\n' +
    'property uchar red\nproperty uchar green\nproperty uchar blue\n' +
    'end_header\n';

  const headerBytes = new TextEncoder().encode(header);
  const stride = 15; // 3 floats + 3 bytes
  const buffer = new ArrayBuffer(headerBytes.length + total * stride);
  const bytes = new Uint8Array(buffer);
  bytes.set(headerBytes, 0);
  const view = new DataView(buffer);

  let offset = headerBytes.length;
  for (const chunk of chunks) {
    const { positions, colors } = chunk;
    for (let i = 0; i < positions.length / 3; i++) {
      view.setFloat32(offset, positions[i * 3], true);
      view.setFloat32(offset + 4, positions[i * 3 + 1], true);
      view.setFloat32(offset + 8, positions[i * 3 + 2], true);
      view.setUint8(offset + 12, Math.max(0, Math.min(255, Math.round(colors[i * 3] * 255))));
      view.setUint8(offset + 13, Math.max(0, Math.min(255, Math.round(colors[i * 3 + 1] * 255))));
      view.setUint8(offset + 14, Math.max(0, Math.min(255, Math.round(colors[i * 3 + 2] * 255))));
      offset += stride;
    }
  }
  return new Blob([buffer], { type: 'application/octet-stream' });
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
