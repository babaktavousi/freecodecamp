import * as THREE from 'three';

/**
 * Point-to-point, multi-segment and single-point measurement on the cloud.
 *
 * Clicks snap to real reconstructed points (GPU picking in Viewer.pick), so a
 * measurement always refers to something that was actually observed rather
 * than to an interpolated surface.
 */
export class MeasureTool {
  constructor(viewer, viewportEl) {
    this.viewer = viewer;
    this.viewport = viewportEl;
    this.mode = null;
    this.units = 'm';
    this.measurements = [];
    this.selectedId = null;
    this.nextId = 1;
    this.onChange = null;

    this.group = new THREE.Group();
    this.viewer.scene.add(this.group);

    this.labels = document.createElement('div');
    this.labels.className = 'measure-labels';
    this.viewport.appendChild(this.labels);

    this.pending = [];
    this.hoverPoint = null;

    this.snapMarker = new THREE.Mesh(
      new THREE.SphereGeometry(1, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xffd479, depthTest: false, transparent: true, opacity: 0.9 })
    );
    this.snapMarker.renderOrder = 10;
    this.snapMarker.visible = false;
    this.viewer.scene.add(this.snapMarker);

    this.rubberBand = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color: 0xffd479, dashSize: 0.12, gapSize: 0.08, depthTest: false })
    );
    this.rubberBand.renderOrder = 9;
    this.rubberBand.visible = false;
    this.rubberBand.frustumCulled = false;
    this.viewer.scene.add(this.rubberBand);

    this._tmp = new THREE.Vector3();
  }

  setMode(mode) {
    this.mode = mode;
    if (!mode) this.cancelPending();
    this.viewport.classList.toggle('is-measuring', !!mode);
    this.snapMarker.visible = false;
    this.rubberBand.visible = false;
  }

  setUnits(units) {
    this.units = units;
    this._rebuildLabels();
    this.onChange?.();
  }

  // ------------------------------------------------------------ interaction

  handleHover(x, y) {
    if (!this.mode) {
      this.snapMarker.visible = false;
      return null;
    }
    const hit = this.viewer.pick(x, y, 8);
    this.hoverPoint = hit ? hit.position : null;
    if (hit) {
      const scale = this._markerScale(hit.position);
      this.snapMarker.position.copy(hit.position);
      this.snapMarker.scale.setScalar(scale);
      this.snapMarker.visible = true;
    } else {
      this.snapMarker.visible = false;
    }

    if (this.pending.length && hit) {
      const last = this.pending[this.pending.length - 1];
      const positions = this.rubberBand.geometry.attributes.position;
      positions.setXYZ(0, last.x, last.y, last.z);
      positions.setXYZ(1, hit.position.x, hit.position.y, hit.position.z);
      positions.needsUpdate = true;
      this.rubberBand.geometry.computeBoundingSphere();
      this.rubberBand.computeLineDistances();
      this.rubberBand.visible = true;
      this._setLiveLabel(last, hit.position);
    } else {
      this.rubberBand.visible = false;
      this._setLiveLabel(null);
    }
    return hit;
  }

  handleClick(x, y) {
    if (!this.mode) return false;
    const hit = this.viewer.pick(x, y, 8);
    if (!hit) return false;

    if (this.mode === 'point') {
      this._commit('point', [hit.position.clone()]);
      return true;
    }

    this.pending.push(hit.position.clone());
    if (this.mode === 'distance' && this.pending.length === 2) {
      this._commit('distance', this.pending.slice());
      this.pending = [];
      this.rubberBand.visible = false;
      this._setLiveLabel(null);
    }
    return true;
  }

  /** Finish a multi-segment path (double click, Enter, or tool switch). */
  finishPending() {
    if (this.mode === 'path' && this.pending.length >= 2) {
      this._commit('path', this.pending.slice());
    }
    this.cancelPending();
  }

  cancelPending() {
    this.pending = [];
    this.rubberBand.visible = false;
    this._setLiveLabel(null);
  }

  _commit(kind, points) {
    const measurement = {
      id: this.nextId++,
      kind,
      points,
      ...describe(kind, points),
    };
    this.measurements.push(measurement);
    this._build(measurement);
    this.selectedId = measurement.id;
    this._refreshSelection();
    this.onChange?.();
  }

  remove(id) {
    const index = this.measurements.findIndex((m) => m.id === id);
    if (index < 0) return;
    const [m] = this.measurements.splice(index, 1);
    this._dispose(m);
    if (this.selectedId === id) this.selectedId = null;
    this.onChange?.();
  }

  clear() {
    for (const m of this.measurements) this._dispose(m);
    this.measurements = [];
    this.selectedId = null;
    this.cancelPending();
    this.onChange?.();
  }

  select(id) {
    this.selectedId = id;
    this._refreshSelection();
    this.onChange?.();
  }

  get selected() {
    return this.measurements.find((m) => m.id === this.selectedId) || null;
  }

  /** Rescale every stored measurement, used after a scale calibration. */
  applyMatrix(matrix) {
    for (const m of this.measurements) {
      m.points = m.points.map((p) => p.clone().applyMatrix4(matrix));
      Object.assign(m, describe(m.kind, m.points));
      this._dispose(m);
      this._build(m);
    }
    this._refreshSelection();
    this.onChange?.();
  }

  // ---------------------------------------------------------------- visuals

  _build(m) {
    const colour = m.kind === 'point' ? 0x5ed6a4 : 0x4da3ff;
    const group = new THREE.Group();

    if (m.points.length > 1) {
      const geometry = new THREE.BufferGeometry().setFromPoints(m.points);
      const line = new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({ color: colour, depthTest: false })
      );
      line.renderOrder = 8;
      line.frustumCulled = false;
      group.add(line);
    }

    const markerGeometry = new THREE.SphereGeometry(1, 14, 10);
    for (const p of m.points) {
      const marker = new THREE.Mesh(
        markerGeometry,
        new THREE.MeshBasicMaterial({ color: colour, depthTest: false })
      );
      marker.position.copy(p);
      marker.scale.setScalar(this._markerScale(p) * 0.8);
      marker.renderOrder = 9;
      marker.userData.isMarker = true;
      group.add(marker);
    }

    this.group.add(group);
    m.object = group;

    const label = document.createElement('div');
    label.className = 'measure-label';
    label.dataset.id = String(m.id);
    label.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      this.select(m.id);
    });
    this.labels.appendChild(label);
    m.label = label;
    this._updateLabelText(m);
  }

  _dispose(m) {
    if (m.object) {
      m.object.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
      this.group.remove(m.object);
      m.object = null;
    }
    m.label?.remove();
    m.label = null;
  }

  _updateLabelText(m) {
    if (!m.label) return;
    if (m.kind === 'point') {
      const p = m.points[0];
      m.label.innerHTML =
        `<span class="ml-value">X ${p.x.toFixed(2)} &nbsp; Y ${p.y.toFixed(2)} &nbsp; Z ${p.z.toFixed(2)}</span>`;
    } else {
      m.label.innerHTML = `<span class="ml-value">${formatLength(m.length, this.units)}</span>`;
    }
  }

  _rebuildLabels() {
    for (const m of this.measurements) this._updateLabelText(m);
  }

  _refreshSelection() {
    for (const m of this.measurements) {
      const active = m.id === this.selectedId;
      m.label?.classList.toggle('is-selected', active);
      m.object?.traverse((o) => {
        if (o.material && o.material.color) {
          o.material.color.set(active ? 0xffd479 : (m.kind === 'point' ? 0x5ed6a4 : 0x4da3ff));
        }
      });
    }
  }

  _setLiveLabel(from, to) {
    if (!this._liveLabel) {
      this._liveLabel = document.createElement('div');
      this._liveLabel.className = 'measure-label is-live';
      this.labels.appendChild(this._liveLabel);
    }
    if (!from || !to) {
      this._liveLabel.style.display = 'none';
      this._livePos = null;
      return;
    }
    const distance = from.distanceTo(to);
    const total = pathLength(this.pending) + distance;
    this._liveLabel.style.display = '';
    this._liveLabel.innerHTML = this.mode === 'path' && this.pending.length > 1
      ? `<span class="ml-value">${formatLength(distance, this.units)}</span>` +
        `<span class="ml-sub">total ${formatLength(total, this.units)}</span>`
      : `<span class="ml-value">${formatLength(distance, this.units)}</span>`;
    this._livePos = from.clone().add(to).multiplyScalar(0.5);
  }

  _markerScale(point) {
    const distance = this.viewer.camera.position.distanceTo(point);
    return Math.max(distance * 0.006, 0.004);
  }

  /** Called every frame: keep labels and marker sizes locked to the view. */
  update() {
    const camera = this.viewer.camera;
    const width = this.viewport.clientWidth;
    const height = this.viewport.clientHeight;

    for (const m of this.measurements) {
      if (!m.label) continue;
      const anchor = m.kind === 'point' ? m.points[0] : midpointOf(m.points);
      this._positionLabel(m.label, anchor, width, height, camera);
      m.object?.children.forEach((child) => {
        if (child.userData.isMarker) child.scale.setScalar(this._markerScale(child.position) * 0.8);
      });
    }
    if (this._liveLabel && this._livePos) {
      this._positionLabel(this._liveLabel, this._livePos, width, height, camera);
    }
    if (this.snapMarker.visible) {
      this.snapMarker.scale.setScalar(this._markerScale(this.snapMarker.position));
    }
  }

  _positionLabel(el, point, width, height, camera) {
    this._tmp.copy(point).project(camera);
    if (this._tmp.z > 1 || this._tmp.z < -1) {
      el.style.visibility = 'hidden';
      return;
    }
    el.style.visibility = 'visible';
    const x = (this._tmp.x * 0.5 + 0.5) * width;
    const y = (-this._tmp.y * 0.5 + 0.5) * height;
    el.style.transform = `translate(-50%, -50%) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  }

  toCSV() {
    const rows = [['id', 'type', `length_${this.units}`, 'dx', 'dy', 'dz', 'points']];
    for (const m of this.measurements) {
      rows.push([
        m.id,
        m.kind,
        m.kind === 'point' ? '' : convert(m.length, this.units).toFixed(4),
        m.kind === 'point' ? '' : convert(m.dx, this.units).toFixed(4),
        m.kind === 'point' ? '' : convert(m.dy, this.units).toFixed(4),
        m.kind === 'point' ? '' : convert(m.dz, this.units).toFixed(4),
        m.points.map((p) => `${p.x.toFixed(4)} ${p.y.toFixed(4)} ${p.z.toFixed(4)}`).join(' | '),
      ]);
    }
    return rows.map((r) => r.join(',')).join('\n');
  }
}

function describe(kind, points) {
  if (kind === 'point') return { length: 0, dx: 0, dy: 0, dz: 0 };
  const length = pathLength(points);
  const a = points[0];
  const b = points[points.length - 1];
  return { length, dx: Math.abs(b.x - a.x), dy: Math.abs(b.y - a.y), dz: Math.abs(b.z - a.z) };
}

function pathLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += points[i].distanceTo(points[i - 1]);
  return total;
}

function midpointOf(points) {
  if (points.length === 2) return points[0].clone().add(points[1]).multiplyScalar(0.5);
  const mid = Math.floor(points.length / 2);
  return points[mid].clone();
}

const UNIT_FACTORS = { m: 1, cm: 100, mm: 1000, ft: 3.280839895 };

export function convert(metres, units) {
  return metres * (UNIT_FACTORS[units] ?? 1);
}

export function formatLength(metres, units) {
  if (!Number.isFinite(metres)) return '—';
  if (units === 'ft') {
    const totalInches = metres * 39.3700787;
    const feet = Math.floor(totalInches / 12);
    const inches = totalInches - feet * 12;
    return `${feet}' ${inches.toFixed(1)}"`;
  }
  const value = convert(metres, units);
  const decimals = units === 'm' ? 3 : units === 'cm' ? 1 : 0;
  return `${value.toFixed(decimals)} ${units}`;
}
