import * as THREE from 'three';

/**
 * Navigation modelled on desktop model-review tools (Navisworks, Recap):
 * a pivot-based Orbit plus Pan, Zoom, Look, Walk and Fly, with the middle and
 * right mouse buttons always available as Pan and Orbit whatever tool is active.
 */
const MODES = ['orbit', 'pan', 'zoom', 'look', 'walk', 'fly'];

export const MODE_HINTS = {
  orbit: 'Orbit — drag to rotate, middle-drag to pan, wheel to zoom, double-click to set the pivot',
  pan: 'Pan — drag to slide the view, wheel to zoom',
  zoom: 'Zoom — drag up and down, or use the wheel',
  look: 'Look — drag to turn the camera in place',
  walk: 'Walk — WASD to move at a fixed eye height, drag to look, Shift to go faster',
  fly: 'Fly — WASD to move, Q/E for down/up, drag to look, Shift to go faster',
};

export class NavController {
  constructor(viewer, dom) {
    this.viewer = viewer;
    this.camera = viewer.camera;
    this.dom = dom;
    this.mode = 'orbit';
    this.enabled = true;

    this.pivot = new THREE.Vector3();
    this.eyeHeight = 1.6;
    this.walkSpeed = 1.8;
    this.floorY = 0;

    this._pointers = new Map();
    this._drag = null;
    this._keys = new Set();
    this._lastTime = performance.now();
    this._spherical = new THREE.Spherical();

    this.onModeChange = null;
    this.onClick = null; // (x, y, event) for tools that consume clicks
    this.consumeClicks = false;

    this._bind();
  }

  _bind() {
    const dom = this.dom;
    dom.style.touchAction = 'none';
    dom.addEventListener('pointerdown', (e) => this._onPointerDown(e));
    dom.addEventListener('pointermove', (e) => this._onPointerMove(e));
    addEventListener('pointerup', (e) => this._onPointerUp(e));
    addEventListener('pointercancel', (e) => this._onPointerUp(e));
    dom.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });
    dom.addEventListener('contextmenu', (e) => e.preventDefault());
    dom.addEventListener('dblclick', (e) => this._onDoubleClick(e));
    addEventListener('keydown', (e) => this._onKey(e, true));
    addEventListener('keyup', (e) => this._onKey(e, false));
    addEventListener('blur', () => this._keys.clear());
  }

  setMode(mode) {
    if (!MODES.includes(mode)) return;
    this.mode = mode;
    if (mode === 'walk') {
      this.floorY = this.viewer.floorLevel();
      this.camera.position.y = this.floorY + this.eyeHeight;
    }
    this.onModeChange?.(mode);
  }

  /** Distance from the camera to the pivot, the natural "zoom" scale. */
  pivotDistance() {
    return Math.max(this.camera.position.distanceTo(this.pivot), 1e-3);
  }

  setPivot(point) {
    this.pivot.copy(point);
  }

  // ------------------------------------------------------------- pointers

  _localXY(e) {
    const rect = this.dom.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  _onPointerDown(e) {
    if (!this.enabled) return;
    this.dom.setPointerCapture?.(e.pointerId);
    const { x, y } = this._localXY(e);
    this._pointers.set(e.pointerId, { x, y });

    let action = null;
    if (e.button === 1) action = 'pan';
    else if (e.button === 2) action = 'orbit';
    else if (e.button === 0) {
      if (this.consumeClicks && (this.mode === 'orbit' || this.mode === 'pan')) action = 'click';
      else action = this.mode === 'walk' || this.mode === 'fly' ? 'look' : this.mode;
    }
    this._drag = { action, x, y, startX: x, startY: y, moved: 0, button: e.button };
    if (action && action !== 'click') e.preventDefault();
  }

  _onPointerMove(e) {
    if (!this.enabled) return;
    const { x, y } = this._localXY(e);
    this.onHover?.(x, y);
    const drag = this._drag;
    if (!drag) return;
    const dx = x - drag.x;
    const dy = y - drag.y;
    drag.x = x;
    drag.y = y;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    if (drag.action === 'click' && drag.moved > 4) drag.action = 'orbit';

    switch (drag.action) {
      case 'orbit': this.orbit(dx, dy); break;
      case 'pan': this.pan(dx, dy); break;
      case 'zoom': this.dolly(Math.pow(1.01, -dy)); break;
      case 'look': this.look(dx, dy); break;
      default: break;
    }
  }

  _onPointerUp(e) {
    const drag = this._drag;
    this._pointers.delete(e.pointerId);
    this._drag = null;
    if (!drag) return;
    if (drag.action === 'click' && drag.moved < 5 && this.onClick) {
      const { x, y } = this._localXY(e);
      this.onClick(x, y, e);
    }
  }

  _onWheel(e) {
    if (!this.enabled) return;
    e.preventDefault();
    const factor = Math.pow(0.94, -Math.sign(e.deltaY) * Math.min(3, Math.abs(e.deltaY) / 60 + 1));
    if (this.mode === 'walk' || this.mode === 'fly') {
      this.walkSpeed = THREE.MathUtils.clamp(this.walkSpeed * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 0.1, 40);
      return;
    }
    const { x, y } = this._localXY(e);
    this.dollyToCursor(factor, x, y);
  }

  _onDoubleClick(e) {
    const { x, y } = this._localXY(e);
    const hit = this.viewer.pick(x, y, 10);
    if (!hit) return;
    this.focusOn(hit.position);
  }

  _onKey(e, down) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
    const key = e.key.toLowerCase();
    if (down) this._keys.add(key); else this._keys.delete(key);
  }

  // -------------------------------------------------------------- motions

  orbit(dx, dy) {
    const offset = this.camera.position.clone().sub(this.pivot);
    this._spherical.setFromVector3(offset);
    this._spherical.theta -= dx * 0.006;
    this._spherical.phi = THREE.MathUtils.clamp(this._spherical.phi - dy * 0.006, 0.02, Math.PI - 0.02);
    offset.setFromSpherical(this._spherical);
    this.camera.position.copy(this.pivot).add(offset);
    this.camera.lookAt(this.pivot);
  }

  pan(dx, dy) {
    const distance = this.pivotDistance();
    const height = this.dom.clientHeight || 1;
    const worldPerPixel = (2 * Math.tan((this.camera.fov * Math.PI) / 360) * distance) / height;
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
    const move = right.multiplyScalar(-dx * worldPerPixel).add(up.multiplyScalar(dy * worldPerPixel));
    this.camera.position.add(move);
    this.pivot.add(move);
  }

  dolly(factor) {
    const offset = this.camera.position.clone().sub(this.pivot);
    const distance = offset.length();
    const next = THREE.MathUtils.clamp(distance * factor, 0.05, 8000);
    this.camera.position.copy(this.pivot).add(offset.setLength(next));
  }

  /** Zoom towards whatever is under the cursor, like a CAD wheel zoom. */
  dollyToCursor(factor, x, y) {
    const target = this._cursorTarget(x, y);
    const dir = this.camera.position.clone().sub(target);
    const distance = dir.length();
    const next = THREE.MathUtils.clamp(distance * factor, 0.05, 8000);
    this.camera.position.copy(target).add(dir.setLength(next));
    // Keep the pivot in front of the camera so orbiting stays sane afterwards.
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    const pivotDist = Math.max(target.distanceTo(this.camera.position), 0.2);
    this.pivot.copy(this.camera.position).add(forward.multiplyScalar(pivotDist));
  }

  _cursorTarget(x, y) {
    const hit = this.viewer.pick(x, y, 12);
    if (hit) return hit.position.clone();
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    return this.camera.position.clone().add(forward.multiplyScalar(this.pivotDistance()));
  }

  look(dx, dy) {
    const euler = new THREE.Euler().setFromQuaternion(this.camera.quaternion, 'YXZ');
    euler.y -= dx * 0.0035;
    euler.x = THREE.MathUtils.clamp(euler.x - dy * 0.0035, -Math.PI / 2 + 0.01, Math.PI / 2 - 0.01);
    euler.z = 0;
    this.camera.quaternion.setFromEuler(euler);
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    this.pivot.copy(this.camera.position).add(forward.multiplyScalar(this.pivotDistance()));
  }

  /** Per-frame keyboard movement for Walk and Fly. */
  update() {
    const now = performance.now();
    const dt = Math.min((now - this._lastTime) / 1000, 0.1);
    this._lastTime = now;
    if (this.mode !== 'walk' && this.mode !== 'fly') return;
    if (!this._keys.size) return;

    const speed = this.walkSpeed * (this._keys.has('shift') ? 3 : 1);
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    if (this.mode === 'walk') {
      forward.y = 0;
      forward.normalize();
    }
    const right = new THREE.Vector3().crossVectors(forward, this.camera.up).normalize();

    const move = new THREE.Vector3();
    if (this._keys.has('w') || this._keys.has('arrowup')) move.add(forward);
    if (this._keys.has('s') || this._keys.has('arrowdown')) move.sub(forward);
    if (this._keys.has('d') || this._keys.has('arrowright')) move.add(right);
    if (this._keys.has('a') || this._keys.has('arrowleft')) move.sub(right);
    if (this.mode === 'fly') {
      if (this._keys.has('e')) move.y += 1;
      if (this._keys.has('q')) move.y -= 1;
    }
    if (move.lengthSq() === 0) return;

    move.normalize().multiplyScalar(speed * dt);
    this.camera.position.add(move);
    if (this.mode === 'walk') this.camera.position.y = this.floorY + this.eyeHeight;

    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    this.pivot.copy(this.camera.position).add(dir.multiplyScalar(this.pivotDistance()));
  }

  // ----------------------------------------------------------- viewpoints

  focusOn(point, distance = null) {
    const dist = distance ?? Math.max(this.pivotDistance() * 0.45, 1.2);
    const dir = this.camera.position.clone().sub(point);
    if (dir.lengthSq() < 1e-6) dir.set(1, 0.6, 1);
    this.camera.position.copy(point).add(dir.setLength(dist));
    this.pivot.copy(point);
    this.camera.lookAt(point);
  }

  fit(box = this.viewer.framingBounds) {
    if (box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 0.5);
    const distance = radius / Math.tan((this.camera.fov * Math.PI) / 360) * 1.15;
    const dir = new THREE.Vector3(0.62, 0.48, -0.62).normalize();
    this.camera.position.copy(centre).add(dir.multiplyScalar(distance));
    this.pivot.copy(centre);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(centre);
    this.camera.near = Math.max(distance / 4000, 0.01);
    this.camera.far = distance * 12 + radius * 10;
    this.camera.updateProjectionMatrix();
  }

  setView(preset) {
    const box = this.viewer.framingBounds;
    if (box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 0.5);
    const distance = (radius / Math.tan((this.camera.fov * Math.PI) / 360)) * 1.15;
    const dirs = {
      top: [0, 1, 0.0001], bottom: [0, -1, 0.0001],
      front: [0, 0, -1], back: [0, 0, 1],
      left: [-1, 0, 0], right: [1, 0, 0],
      iso: [0.62, 0.48, -0.62],
    };
    const dir = new THREE.Vector3(...(dirs[preset] || dirs.iso)).normalize();
    this.camera.position.copy(centre).add(dir.multiplyScalar(distance));
    this.pivot.copy(centre);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(centre);
  }

  /** Start the walk at the beginning of the captured path, looking along it. */
  standAtPathStart(path) {
    if (!path || path.length < 2) return;
    const start = new THREE.Vector3(...path[0]);
    const ahead = new THREE.Vector3(...path[Math.min(4, path.length - 1)]);
    this.floorY = this.viewer.floorLevel();
    this.camera.position.set(start.x, this.floorY + this.eyeHeight, start.z);
    ahead.y = this.camera.position.y;
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(ahead);
    this.pivot.copy(ahead);
  }
}

export { MODES };
