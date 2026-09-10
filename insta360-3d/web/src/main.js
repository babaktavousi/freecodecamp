import * as THREE from 'three';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';

import { Viewer } from './viewer/Viewer.js';
import { MODE_HINTS, NavController } from './viewer/NavController.js';
import { MeasureTool, convert, formatLength } from './viewer/MeasureTool.js';
import { Reconstructor } from './pipeline/Reconstructor.js';
import { loadVideo } from './pipeline/frames.js';
import { downloadBlob, exportPLY } from './io/ply.js';
import { exportLAS, exportPTS } from './io/las.js';

const SAMPLE_VIDEO = '../samples/walkthrough_360.webm';
const SAMPLE_CLOUD = '../samples/walkthrough_cloud.ply';

const $ = (id) => document.getElementById(id);
const viewport = $('viewport');

if (!supportsWebGL2()) {
  $('empty-state').innerHTML =
    '<h1>This browser cannot run the viewer</h1>' +
    '<p>Point clouds are drawn with WebGL2, which is not available here. ' +
    'Chrome, Edge, Firefox and Safari 15+ on the desktop all support it; some ' +
    'virtual machines and remote sessions disable hardware acceleration.</p>';
  throw new Error('WebGL2 unavailable');
}

function supportsWebGL2() {
  try {
    return !!document.createElement('canvas').getContext('webgl2');
  } catch {
    return false;
  }
}

const viewer = new Viewer($('canvas'));
const nav = new NavController(viewer, $('canvas'));
const measure = new MeasureTool(viewer, viewport);

const state = {
  reconstructor: null,
  running: false,
  videoReady: false,
  units: 'm',
  sourceName: null,
};

viewer.onBeforeRender = () => {
  nav.update();
  measure.update();
  updateStatus();
};

viewer.setGridVisible(true);
viewer.setPointSize(3);

// ---------------------------------------------------------------- navigation

function setNavMode(mode) {
  nav.setMode(mode);
  document.querySelectorAll('.nav-mode').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.mode === mode);
  });
  $('mode-hint').textContent = MODE_HINTS[mode];
  $('status-mode').textContent = mode[0].toUpperCase() + mode.slice(1);
  viewport.classList.toggle('is-walking', mode === 'walk' || mode === 'fly');
}

document.querySelectorAll('.nav-mode').forEach((btn) => {
  btn.addEventListener('click', () => {
    setMeasureMode(null);
    setNavMode(btn.dataset.mode);
  });
});

function setMeasureMode(mode) {
  if (measure.mode === 'path' && mode !== 'path') measure.finishPending();
  measure.setMode(mode);
  nav.consumeClicks = !!mode;
  document.querySelectorAll('.measure-mode').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.measure === mode);
  });
  if (mode) {
    if (nav.mode !== 'orbit' && nav.mode !== 'pan') setNavMode('orbit');
    $('mode-hint').textContent = mode === 'path'
      ? 'Path — click points along the run, double-click or press Enter to finish'
      : mode === 'point'
        ? 'Point — click to read out a coordinate'
        : 'Distance — click two points; hold right mouse to orbit while measuring';
  } else {
    $('mode-hint').textContent = MODE_HINTS[nav.mode];
  }
}

document.querySelectorAll('.measure-mode').forEach((btn) => {
  btn.addEventListener('click', () => {
    setMeasureMode(measure.mode === btn.dataset.measure ? null : btn.dataset.measure);
  });
});

let lastHover = 0;
nav.onHover = (x, y) => {
  // Each hover costs an offscreen render of the whole cloud, so cap the rate
  // rather than picking on every coalesced pointer event.
  const now = performance.now();
  if (measure.mode && now - lastHover < 33) return;
  lastHover = now;

  const hit = measure.mode ? measure.handleHover(x, y) : null;
  const readout = $('cursor-readout');
  if (hit) {
    readout.hidden = false;
    readout.textContent =
      `X ${hit.position.x.toFixed(2)}  Y ${hit.position.y.toFixed(2)}  Z ${hit.position.z.toFixed(2)}`;
  } else {
    readout.hidden = true;
  }
};

nav.onClick = (x, y) => {
  measure.handleClick(x, y);
};

$('canvas').addEventListener('dblclick', () => {
  if (measure.mode === 'path') measure.finishPending();
});

addEventListener('keydown', (event) => {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
  const key = event.key.toLowerCase();
  if (event.ctrlKey || event.metaKey) return;

  const navKeys = { o: 'orbit', p: 'pan', z: 'zoom', l: 'look', f: 'fly' };
  if (!measure.mode && navKeys[key] && !['w', 'a', 's', 'd'].includes(key)) {
    setNavMode(navKeys[key]);
    return;
  }
  if (key === 'd' && event.shiftKey) { setMeasureMode('distance'); return; }
  if (key === 'a' && event.shiftKey) { setMeasureMode('path'); return; }
  if (key === 'escape') { measure.cancelPending(); setMeasureMode(null); }
  if (key === 'enter') measure.finishPending();
  if (key === 'home') { nav.fit(); }
  if (key === 'delete' || key === 'backspace') {
    if (measure.selectedId != null) measure.remove(measure.selectedId);
  }
});

$('view-preset').addEventListener('change', (event) => {
  if (event.target.value) nav.setView(event.target.value);
  event.target.value = '';
});
$('btn-fit').addEventListener('click', () => nav.fit());
$('btn-help').addEventListener('click', () => $('help-dialog').showModal());

document.querySelectorAll('.panel-collapse').forEach((btn) => {
  btn.addEventListener('click', () => {
    $(btn.dataset.target).classList.toggle('is-collapsed');
    setTimeout(() => viewer.resize(), 200);
  });
});

// ------------------------------------------------------------------- display

bindRange('opt-point-size', 'out-point-size', (v) => {
  viewer.setPointSize(v);
  return v.toFixed(1);
});
$('opt-color-mode').addEventListener('change', (e) => viewer.setColorMode(e.target.value));
$('opt-show-path').addEventListener('change', (e) => viewer.setPathVisible(e.target.checked));
$('opt-show-grid').addEventListener('change', (e) => viewer.setGridVisible(e.target.checked));

$('opt-clip').addEventListener('change', (e) => {
  $('clip-field').hidden = !e.target.checked;
  updateClipping();
});
$('opt-clip-height').addEventListener('input', updateClipping);

function updateClipping() {
  const enabled = $('opt-clip').checked;
  if (!enabled || viewer.framingBounds.isEmpty()) {
    viewer.setClipping(false);
    $('out-clip-height').textContent = '—';
    return;
  }
  const box = viewer.framingBounds;
  const t = Number($('opt-clip-height').value) / 100;
  const y = THREE.MathUtils.lerp(box.min.y, box.max.y, t);
  viewer.setClipping(true, y);
  $('out-clip-height').textContent = formatLength(y - box.min.y, state.units);
}

bindRange('opt-layers', 'out-layers', (v) => String(v));
bindRange('opt-stride', 'out-stride', (v) => `${v.toFixed(2)} s`);

function bindRange(inputId, outputId, format) {
  const input = $(inputId);
  const output = $(outputId);
  const apply = () => { output.textContent = format(Number(input.value)); };
  input.addEventListener('input', apply);
  apply();
}

// -------------------------------------------------------------- measurements

measure.onChange = renderMeasurements;

function renderMeasurements() {
  const list = $('measure-list');
  list.innerHTML = '';
  if (!measure.measurements.length) {
    list.innerHTML = '<div class="measure-empty">No measurements yet.</div>';
  }
  for (const m of measure.measurements) {
    const item = document.createElement('div');
    item.className = 'measure-item' + (m.id === measure.selectedId ? ' is-selected' : '');
    const value = m.kind === 'point'
      ? `${m.points[0].x.toFixed(2)}, ${m.points[0].y.toFixed(2)}, ${m.points[0].z.toFixed(2)}`
      : formatLength(m.length, state.units);
    const detail = m.kind === 'point'
      ? 'coordinate'
      : `ΔX ${formatLength(m.dx, state.units)} · ΔY ${formatLength(m.dy, state.units)} · ΔZ ${formatLength(m.dz, state.units)}` +
        (m.kind === 'path' ? ` · ${m.points.length - 1} segments` : '');
    item.innerHTML =
      `<div class="mi-top"><span class="mi-value">${value}</span>` +
      `<span class="mi-kind">${m.kind}</span>` +
      `<button class="mi-del" title="Delete">&times;</button></div>` +
      `<div class="mi-detail">${detail}</div>`;
    item.addEventListener('click', (event) => {
      if (event.target.classList.contains('mi-del')) {
        measure.remove(m.id);
        return;
      }
      measure.select(m.id);
      nav.focusOn(m.kind === 'point' ? m.points[0] : midpoint(m.points));
    });
    list.appendChild(item);
  }
  renderCalibrationTargets();
}

function midpoint(points) {
  return points[0].clone().add(points[points.length - 1]).multiplyScalar(0.5);
}

$('opt-units').addEventListener('change', (event) => {
  state.units = event.target.value;
  measure.setUnits(state.units);
  $('calib-unit').textContent = state.units === 'ft' ? 'ft' : state.units;
  renderMeasurements();
  updateStatus(true);
  updateClipping();
});

$('btn-clear-measures').addEventListener('click', () => measure.clear());
$('btn-export-measures').addEventListener('click', () => {
  if (!measure.measurements.length) return;
  downloadBlob(new Blob([measure.toCSV()], { type: 'text/csv' }), 'measurements.csv');
});

function renderCalibrationTargets() {
  const select = $('calib-target');
  const previous = select.value;
  select.innerHTML = '<option value="">None</option>';
  for (const m of measure.measurements) {
    if (m.kind === 'point') continue;
    const option = document.createElement('option');
    option.value = String(m.id);
    option.textContent = `#${m.id} — ${formatLength(m.length, state.units)}`;
    select.appendChild(option);
  }
  select.value = previous;
  $('btn-calibrate').disabled = !select.value;
}

$('calib-target').addEventListener('change', () => {
  $('btn-calibrate').disabled = !$('calib-target').value || !Number($('calib-length').value);
});
$('calib-length').addEventListener('input', () => {
  $('btn-calibrate').disabled = !$('calib-target').value || !Number($('calib-length').value);
});

$('btn-calibrate').addEventListener('click', () => {
  const id = Number($('calib-target').value);
  const target = measure.measurements.find((m) => m.id === id);
  const trueLength = Number($('calib-length').value);
  if (!target || !(trueLength > 0) || !(target.length > 0)) return;

  // The entered value is in the display units; the model works in metres.
  const trueMetres = trueLength / (convert(1, state.units) || 1);
  const factor = trueMetres / target.length;
  const matrix = new THREE.Matrix4().makeScale(factor, factor, factor);
  applyTransform(matrix);
  log(`scale calibrated from measurement #${id}: ×${factor.toFixed(4)}`);
});

function applyTransform(matrix) {
  viewer.transformCloud(matrix);
  viewer.updateDisplayBounds();
  measure.applyMatrix(matrix);
  viewer.refreshGrid();
  nav.fit();
  updateStatus(true);
  updateClipping();
}

// ---------------------------------------------------------------- file input

$('btn-open-video').addEventListener('click', () => $('file-video').click());
$('btn-pick-video').addEventListener('click', () => $('file-video').click());
$('btn-open-cloud').addEventListener('click', () => $('file-cloud').click());
$('btn-empty-cloud').addEventListener('click', () => $('file-cloud').click());
$('file-video').addEventListener('change', (e) => e.target.files[0] && useVideoFile(e.target.files[0]));
$('file-cloud').addEventListener('change', (e) => e.target.files[0] && loadCloudFile(e.target.files[0]));
$('btn-load-sample').addEventListener('click', loadSample);
$('btn-empty-sample').addEventListener('click', loadSample);

const sourceBox = $('source-box');
['dragenter', 'dragover'].forEach((type) => {
  sourceBox.addEventListener(type, (e) => { e.preventDefault(); sourceBox.classList.add('is-dragover'); });
  viewport.addEventListener(type, (e) => e.preventDefault());
});
['dragleave', 'drop'].forEach((type) => {
  sourceBox.addEventListener(type, () => sourceBox.classList.remove('is-dragover'));
});
[sourceBox, viewport].forEach((el) => {
  el.addEventListener('drop', (event) => {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (/\.ply$/i.test(file.name)) loadCloudFile(file);
    else useVideoFile(file);
  });
});

async function useVideoFile(file) {
  if (/\.insv$/i.test(file.name)) {
    setSourceInfo(null, 'This is a raw .insv file. Export it as an equirectangular MP4 from Insta360 Studio first.');
    return;
  }
  await useVideoSource(URL.createObjectURL(file), file.name);
}

async function useVideoSource(src, name) {
  const video = $('source-video');
  try {
    const info = await loadVideo(video, src);
    video.hidden = false;
    $('source-empty').hidden = true;
    state.videoReady = true;
    state.sourceName = name;
    $('btn-reconstruct').disabled = false;
    const ratio = info.width / info.height;
    setSourceInfo(
      { name, ...info },
      Math.abs(ratio - 2) > 0.05
        ? `This clip is ${ratio.toFixed(2)}:1, not 2:1. Equirectangular output is expected — results will be distorted.`
        : null
    );
  } catch (error) {
    setSourceInfo(null, error.message);
  }
}

function setSourceInfo(info, warning) {
  const box = $('source-info');
  box.hidden = false;
  box.innerHTML = '';
  if (info) {
    const rows = [
      ['File', info.name],
      ['Frame', `${info.width} × ${info.height}`],
      ['Length', `${info.duration.toFixed(1)} s`],
    ];
    for (const [key, value] of rows) {
      box.insertAdjacentHTML('beforeend', `<span>${key}</span><b>${value}</b>`);
    }
  }
  if (warning) box.insertAdjacentHTML('beforeend', `<span class="source-warn">${warning}</span>`);
}

async function loadSample() {
  $('btn-empty-sample').disabled = true;
  log('loading sample walkthrough…');
  await useVideoSource(SAMPLE_VIDEO, 'walkthrough_360.webm');
  try {
    const response = await fetch(SAMPLE_CLOUD);
    if (response.ok) {
      const buffer = await response.arrayBuffer();
      loadPLYBuffer(buffer, 'walkthrough_cloud.ply');
      log('loaded the prebuilt sample point cloud — press Build point cloud to reconstruct it yourself');
    } else {
      log('sample cloud not found; reconstruct the video to build one');
    }
  } catch {
    log('sample cloud not found; reconstruct the video to build one');
  }
  $('btn-empty-sample').disabled = false;
}

function loadCloudFile(file) {
  const reader = new FileReader();
  reader.onload = () => loadPLYBuffer(reader.result, file.name);
  reader.readAsArrayBuffer(file);
}

/**
 * PLYLoader converts vertex colours from sRGB into three's linear working
 * space. The reconstruction pipeline produces sRGB directly, so without this
 * a loaded cloud would be darker on screen than the same cloud reconstructed
 * here, and would export darker still.
 */
function linearToSRGB(values) {
  for (let i = 0; i < values.length; i++) {
    const c = values[i];
    values[i] = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  }
  return values;
}

function loadPLYBuffer(buffer, name) {
  let geometry;
  try {
    geometry = new PLYLoader().parse(buffer);
  } catch (error) {
    log(`could not read ${name}: ${error.message}`);
    return;
  }
  const position = geometry.getAttribute('position');
  if (!position) {
    log(`${name} has no vertex positions`);
    return;
  }
  const colorAttr = geometry.getAttribute('color');
  const colors = colorAttr
    ? linearToSRGB(Float32Array.from(colorAttr.array))
    : new Float32Array(position.count * 3).fill(0.75);

  resetModel();
  viewer.addChunk(Float32Array.from(position.array), colors);
  finishModel(`${name} — ${position.count.toLocaleString()} points`);
}

// -------------------------------------------------------------- reconstruct

$('btn-reconstruct').addEventListener('click', runReconstruction);
$('btn-cancel').addEventListener('click', () => {
  state.reconstructor?.cancel();
});

async function runReconstruction() {
  if (state.running || !state.videoReady) return;
  state.running = true;
  resetModel();
  $('btn-reconstruct').hidden = true;
  $('btn-cancel').hidden = false;
  $('progress').hidden = false;
  $('log').hidden = false;
  $('empty-state').classList.add('is-hidden');

  const reconstructor = new Reconstructor();
  state.reconstructor = reconstructor;
  reconstructor.addEventListener('progress', (event) => {
    $('progress-fill').style.width = `${(event.detail.fraction * 100).toFixed(1)}%`;
    $('progress-label').textContent = event.detail.label;
  });
  reconstructor.addEventListener('log', (event) => log(event.detail));
  reconstructor.addEventListener('poses', (event) => {
    const path = [];
    const { centres } = event.detail;
    for (let i = 0; i < centres.length; i += 3) path.push([centres[i], centres[i + 1], centres[i + 2]]);
    viewer.setCameraPath(path);
  });
  reconstructor.addEventListener('chunk', (event) => {
    const chunk = event.detail;
    viewer.addChunk(chunk.positions, chunk.colors, chunk.confidences);
    if (viewer.chunks.length === 1) {
      viewer.updateDisplayBounds();
      nav.fit();
    }
    updateStatus(true);
  });

  try {
    const result = await reconstructor.run($('source-video'), {
      sweepWidth: Number($('opt-quality').value),
      layers: Number($('opt-layers').value),
      spacing: Number($('opt-stride').value),
      cameraHeight: Number($('opt-camera-height').value),
    });
    if (result.transform) {
      applyTransform(new THREE.Matrix4().fromArray(result.transform));
      log('metric scale applied from the floor plane and camera height');
    } else {
      log('no metric scale — measure a known dimension and use Calibrate scale');
    }
    // The path was drawn when the poses arrived and moved with the cloud during
    // applyTransform; re-setting it from the result would put it back in
    // pre-scale coordinates.
    finishModel(`${result.points.toLocaleString()} points from ${state.sourceName ?? 'video'}`);
  } catch (error) {
    if (error.name === 'AbortError') log('cancelled');
    else {
      log(`failed: ${error.message}`);
      $('progress-label').textContent = error.message;
    }
  } finally {
    state.running = false;
    state.reconstructor = null;
    $('btn-reconstruct').hidden = false;
    $('btn-cancel').hidden = true;
  }
}

$('btn-save-cloud').addEventListener('click', () => {
  if (!viewer.chunks.length) return;
  $('export-dialog').showModal();
});

const EXPORTERS = {
  las: { write: exportLAS, name: 'point_cloud.las' },
  pts: { write: exportPTS, name: 'point_cloud.pts' },
  ply: { write: exportPLY, name: 'point_cloud.ply' },
};

for (const button of document.querySelectorAll('.export-option')) {
  button.addEventListener('click', () => {
    const exporter = EXPORTERS[button.dataset.format];
    if (!exporter || !viewer.chunks.length) return;
    const chunks = viewer.chunks.map((chunk) => ({
      positions: chunk.geometry.getAttribute('position').array,
      colors: chunk.geometry.userData.rgb,
    }));
    downloadBlob(exporter.write(chunks), exporter.name);
    $('export-dialog').close();
  });
}

// -------------------------------------------------------------------- model

function resetModel() {
  viewer.clearCloud();
  measure.clear();
  $('btn-save-cloud').disabled = true;
  updateStatus(true);
}

function finishModel(summary) {
  $('empty-state').classList.add('is-hidden');
  $('btn-save-cloud').disabled = false;
  viewer.updateDisplayBounds();
  viewer.refreshGrid();
  nav.fit();
  nav.eyeHeight = Number($('opt-camera-height').value) || 1.6;
  updateStatus(true);
  updateClipping();
  log(summary);
}

function log(message) {
  const el = $('log');
  el.hidden = false;
  el.textContent += (el.textContent ? '\n' : '') + message;
  el.scrollTop = el.scrollHeight;
}

let lastStatusUpdate = 0;
function updateStatus(force = false) {
  const now = performance.now();
  if (!force && now - lastStatusUpdate < 400) return;
  lastStatusUpdate = now;

  $('status-points').textContent = viewer.pointCount
    ? `${viewer.pointCount.toLocaleString()} points`
    : 'No model loaded';
  $('status-fps').textContent = viewer.pointCount ? `${viewer.fps} fps` : '';

  if (!viewer.framingBounds.isEmpty()) {
    const size = viewer.framingBounds.getSize(new THREE.Vector3());
    $('status-extent').textContent =
      `extent ${formatLength(size.x, state.units)} × ${formatLength(size.y, state.units)} × ${formatLength(size.z, state.units)}`;
    const stats = $('stats');
    stats.innerHTML =
      `<dt>Points</dt><dd>${viewer.pointCount.toLocaleString()}</dd>` +
      `<dt>Width</dt><dd>${formatLength(size.x, state.units)}</dd>` +
      `<dt>Height</dt><dd>${formatLength(size.y, state.units)}</dd>` +
      `<dt>Depth</dt><dd>${formatLength(size.z, state.units)}</dd>`;
  } else {
    $('status-extent').textContent = '';
    $('stats').innerHTML = '<dt>Points</dt><dd>0</dd>';
  }
}

setNavMode('orbit');
renderMeasurements();
updateStatus(true);

// Exposed so the parts can be driven from the console or an automated test.
window.pointCloudStudio = { viewer, nav, measure, state };
