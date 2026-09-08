import * as THREE from 'three';

/**
 * Scene, renderer and point-cloud storage.
 *
 * Points arrive in chunks (the reconstruction streams a batch per keyframe),
 * so the cloud is kept as a list of THREE.Points objects sharing one material.
 * A flat index space across the chunks makes GPU picking a single readback.
 */
export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setClearColor(0x0a0d13, 1);
    this.renderer.localClippingEnabled = true;
    // Point colours are the video's own sRGB values and PLY files store the
    // same. Passing them through unconverted shows the cloud in the colours the
    // camera recorded, which is what other point-cloud viewers do.
    this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace;

    this.scene = new THREE.Scene();
    this.scene.fog = null;
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.02, 4000);
    this.camera.position.set(4, 2.2, -4);

    this.chunks = [];
    this.pointCount = 0;
    this.bounds = new THREE.Box3();
    // Framing, the grid and the reported extents use the robust box so a few
    // stray points cannot push the real model into a corner of the screen.
    this.displayBounds = new THREE.Box3();
    this.colorMode = 'rgb';

    this.clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6);
    this.material = new THREE.PointsMaterial({
      size: 3,
      sizeAttenuation: false,
      vertexColors: true,
      map: makeDiscTexture(),
      alphaTest: 0.5,
      transparent: false,
    });

    this.cloudGroup = new THREE.Group();
    this.scene.add(this.cloudGroup);

    this.grid = null;
    this.pathLine = null;
    this.helpers = new THREE.Group();
    this.scene.add(this.helpers);

    this.pickTarget = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      type: THREE.UnsignedByteType,
    });
    this.pickMaterial = makePickMaterial();

    this._frames = 0;
    this._fpsTime = performance.now();
    this.fps = 0;
    this.onBeforeRender = null;

    this._onResize = () => this.resize();
    addEventListener('resize', this._onResize);
    this.resize();
    this._loop();
  }

  resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _loop() {
    requestAnimationFrame(() => this._loop());
    if (this.onBeforeRender) this.onBeforeRender();
    this.renderer.render(this.scene, this.camera);

    this._frames++;
    const now = performance.now();
    if (now - this._fpsTime > 500) {
      this.fps = Math.round((this._frames * 1000) / (now - this._fpsTime));
      this._frames = 0;
      this._fpsTime = now;
    }
  }

  // ---------------------------------------------------------------- points

  /**
   * @param {Float32Array} positions flattened xyz
   * @param {Float32Array} colors flattened rgb in 0..1
   * @param {Float32Array} [confidence] per point, for the confidence colour mode
   */
  addChunk(positions, colors, confidence = null) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(colors), 3));
    geometry.userData.rgb = colors;
    geometry.userData.confidence = confidence;

    const points = new THREE.Points(geometry, this.material);
    points.frustumCulled = false;
    points.userData.indexOffset = this.pointCount;
    this.cloudGroup.add(points);
    this.chunks.push(points);
    this.pointCount += positions.length / 3;

    geometry.computeBoundingBox();
    this.bounds.union(geometry.boundingBox);
    if (this.colorMode !== 'rgb') this._applyColorMode(points);
    return points;
  }

  clearCloud() {
    for (const chunk of this.chunks) {
      chunk.geometry.dispose();
      this.cloudGroup.remove(chunk);
    }
    this.chunks = [];
    this.pointCount = 0;
    this.bounds.makeEmpty();
    this.displayBounds.makeEmpty();
    this._heights = null;
    this.setCameraPath(null);
  }

  /** Apply a similarity transform to every point (used for metric scaling). */
  transformCloud(matrix) {
    const v = new THREE.Vector3();
    this.bounds.makeEmpty();
    for (const chunk of this.chunks) {
      const pos = chunk.geometry.getAttribute('position');
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(matrix);
        pos.setXYZ(i, v.x, v.y, v.z);
      }
      pos.needsUpdate = true;
      chunk.geometry.computeBoundingBox();
      this.bounds.union(chunk.geometry.boundingBox);
    }
    if (this._cameraPath) {
      const path = this._cameraPath.map((p) => new THREE.Vector3(...p).applyMatrix4(matrix).toArray());
      this.setCameraPath(path);
    }
    if (this.colorMode === 'height') this.setColorMode('height');
  }

  setPointSize(size) {
    this.material.size = size;
    this.material.needsUpdate = true;
  }

  setColorMode(mode) {
    this.colorMode = mode;
    for (const chunk of this.chunks) this._applyColorMode(chunk);
  }

  _applyColorMode(chunk) {
    const attr = chunk.geometry.getAttribute('color');
    const rgb = chunk.geometry.userData.rgb;
    const conf = chunk.geometry.userData.confidence;
    const pos = chunk.geometry.getAttribute('position');
    const out = attr.array;

    if (this.colorMode === 'rgb' || (this.colorMode === 'confidence' && !conf)) {
      out.set(rgb);
    } else if (this.colorMode === 'height') {
      const [low, high] = this.heightRamp();
      const min = low;
      const span = Math.max(high - low, 1e-6);
      for (let i = 0; i < pos.count; i++) {
        const t = Math.min(1, Math.max(0, (pos.getY(i) - min) / span));
        const c = turbo(t);
        out[i * 3] = c[0]; out[i * 3 + 1] = c[1]; out[i * 3 + 2] = c[2];
      }
    } else if (this.colorMode === 'confidence') {
      for (let i = 0; i < pos.count; i++) {
        const t = Math.min(1, conf[i] / 0.6);
        const c = turbo(t);
        out[i * 3] = c[0]; out[i * 3 + 1] = c[1]; out[i * 3 + 2] = c[2];
      }
    }
    attr.needsUpdate = true;
  }

  // --------------------------------------------------------------- helpers

  updateDisplayBounds() {
    this.displayBounds.copy(this.robustBounds());
    this._heights = this._computeHeightStats();
  }

  /**
   * Height of the walking surface.
   *
   * A low percentile rather than the minimum: reconstructions always carry
   * some points below the floor, and standing the walk camera on the lowest
   * of them would put the viewer underground.
   */
  floorLevel() {
    return this._heightStats().floor;
  }

  /** Range the height colouring ramps across. */
  heightRamp() {
    const stats = this._heightStats();
    return [stats.low, stats.high];
  }

  _heightStats() {
    if (!this._heights) this._heights = this._computeHeightStats();
    return this._heights;
  }

  _computeHeightStats(sampleLimit = 80000) {
    if (!this.pointCount) return { floor: 0, low: 0, high: 1 };
    const stride = Math.max(1, Math.floor(this.pointCount / sampleLimit));
    const ys = [];
    for (const chunk of this.chunks) {
      const pos = chunk.geometry.getAttribute('position');
      for (let i = 0; i < pos.count; i += stride) ys.push(pos.getY(i));
    }
    if (!ys.length) return { floor: 0, low: 0, high: 1 };
    ys.sort((a, b) => a - b);
    const at = (q) => ys[Math.min(ys.length - 1, Math.floor(ys.length * q))];
    // Percentiles rather than extremes: a scattering of stray points above and
    // below the model would otherwise flatten the whole colour ramp.
    return { floor: at(0.05), low: at(0.02), high: at(0.98) };
  }

  /** The box used for framing: robust when available, raw otherwise. */
  get framingBounds() {
    return this.displayBounds.isEmpty() ? this.bounds : this.displayBounds;
  }

  setGridVisible(visible) {
    if (visible && !this.grid) this._buildGrid();
    if (this.grid) this.grid.visible = visible;
    this._gridWanted = visible;
  }

  _buildGrid() {
    if (this.grid) {
      this.helpers.remove(this.grid);
      this.grid.geometry.dispose();
    }
    const size = Math.max(10, Math.ceil(this.radius() * 2.4));
    const grid = new THREE.GridHelper(size, Math.max(4, Math.round(size)), 0x33507a, 0x1e2634);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    const box = this.framingBounds;
    grid.position.y = box.isEmpty() ? 0 : box.min.y;
    const centre = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    grid.position.x = centre.x;
    grid.position.z = centre.z;
    this.helpers.add(grid);
    this.grid = grid;
  }

  refreshGrid() {
    if (this._gridWanted) this._buildGrid();
  }

  setCameraPath(points) {
    if (this.pathLine) {
      this.helpers.remove(this.pathLine);
      this.pathLine.geometry.dispose();
      this.pathLine = null;
    }
    this._cameraPath = points;
    if (!points || points.length < 2) return;
    const flat = new Float32Array(points.length * 3);
    points.forEach((p, i) => { flat[i * 3] = p[0]; flat[i * 3 + 1] = p[1]; flat[i * 3 + 2] = p[2]; });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(flat, 3));
    this.pathLine = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color: 0xffb454, transparent: true, opacity: 0.9 })
    );
    this.pathLine.frustumCulled = false;
    this.pathLine.visible = this._pathWanted !== false;
    this.helpers.add(this.pathLine);
  }

  setPathVisible(visible) {
    this._pathWanted = visible;
    if (this.pathLine) this.pathLine.visible = visible;
  }

  setClipping(enabled, height) {
    if (!enabled) {
      this.material.clippingPlanes = null;
      this.material.needsUpdate = true;
      return;
    }
    this.clipPlane.constant = height;
    this.material.clippingPlanes = [this.clipPlane];
    this.material.needsUpdate = true;
  }

  // --------------------------------------------------------------- queries

  center(out = new THREE.Vector3()) {
    return this.bounds.isEmpty() ? out.set(0, 0, 0) : this.bounds.getCenter(out);
  }

  /**
   * Bounding box of the bulk of the cloud, ignoring the tails.
   *
   * A handful of stray points can otherwise put the true model in a corner of
   * the frame after zoom-to-fit, and stretch the ground grid across the scene.
   */
  robustBounds(percentile = 0.01, sampleLimit = 120000) {
    if (this.bounds.isEmpty() || !this.pointCount) return this.bounds.clone();
    const stride = Math.max(1, Math.floor(this.pointCount / sampleLimit));
    const axes = [[], [], []];
    for (const chunk of this.chunks) {
      const pos = chunk.geometry.getAttribute('position');
      for (let i = 0; i < pos.count; i += stride) {
        axes[0].push(pos.getX(i));
        axes[1].push(pos.getY(i));
        axes[2].push(pos.getZ(i));
      }
    }
    if (axes[0].length < 32) return this.bounds.clone();
    const box = new THREE.Box3();
    const lo = [0, 0, 0];
    const hi = [0, 0, 0];
    for (let a = 0; a < 3; a++) {
      axes[a].sort((x, y) => x - y);
      const n = axes[a].length;
      lo[a] = axes[a][Math.floor(n * percentile)];
      hi[a] = axes[a][Math.min(n - 1, Math.floor(n * (1 - percentile)))];
    }
    box.set(new THREE.Vector3(...lo), new THREE.Vector3(...hi));
    return box;
  }

  radius() {
    const box = this.framingBounds;
    if (box.isEmpty()) return 5;
    return Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.5);
  }

  /**
   * Nearest reconstructed point under the cursor.
   *
   * Renders point indices into a small offscreen square centred on the cursor
   * and takes the hit closest to the middle, which snaps a click onto real
   * geometry instead of an interpolated surface guess.
   *
   * @returns {{position: THREE.Vector3, index: number}|null}
   */
  pick(x, y, radiusPx = 7) {
    if (!this.chunks.length) return null;
    const size = radiusPx * 2 + 1;
    if (this.pickTarget.width !== size) this.pickTarget.setSize(size, size);

    const dpr = this.renderer.getPixelRatio();
    const width = Math.floor(this.canvas.clientWidth * dpr);
    const height = Math.floor(this.canvas.clientHeight * dpr);
    const px = Math.floor(x * dpr);
    const py = Math.floor((this.canvas.clientHeight - y) * dpr);

    // A camera whose viewport is the small window around the cursor.
    const pickCamera = this.camera.clone();
    pickCamera.setViewOffset(width, height, px - radiusPx, height - py - radiusPx, size, size);
    pickCamera.updateProjectionMatrix();

    const prevSize = this.pickMaterial.uniforms.uSize.value;
    this.pickMaterial.uniforms.uSize.value = Math.max(2, this.material.size);
    this.pickMaterial.clippingPlanes = this.material.clippingPlanes;

    const overrides = [];
    for (const chunk of this.chunks) {
      overrides.push(chunk.material);
      chunk.material = this.pickMaterial.clone();
      chunk.material.uniforms.uOffset.value = chunk.userData.indexOffset;
      chunk.material.clippingPlanes = this.material.clippingPlanes;
    }

    // Only the cloud may write into the ID buffer. The grid, camera path and
    // measurement graphics would otherwise be decoded as point indices.
    const hidden = [];
    for (const child of this.scene.children) {
      if (child !== this.cloudGroup && child.visible) {
        child.visible = false;
        hidden.push(child);
      }
    }

    const prevTarget = this.renderer.getRenderTarget();
    const prevClear = this.renderer.getClearColor(new THREE.Color());
    this.renderer.setRenderTarget(this.pickTarget);
    this.renderer.setClearColor(0xffffff, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, pickCamera);
    this.renderer.setRenderTarget(prevTarget);
    this.renderer.setClearColor(prevClear, 1);
    for (const child of hidden) child.visible = true;

    const buffer = new Uint8Array(size * size * 4);
    this.renderer.readRenderTargetPixels(this.pickTarget, 0, 0, size, size, buffer);

    this.chunks.forEach((chunk, i) => {
      chunk.material.dispose();
      chunk.material = overrides[i];
    });
    this.pickMaterial.uniforms.uSize.value = prevSize;

    let bestIndex = -1;
    let bestDist = Infinity;
    for (let row = 0; row < size; row++) {
      for (let col = 0; col < size; col++) {
        const o = (row * size + col) * 4;
        if (buffer[o] === 255 && buffer[o + 1] === 255 && buffer[o + 2] === 255) continue;
        const index = buffer[o] | (buffer[o + 1] << 8) | (buffer[o + 2] << 16);
        const dx = col - radiusPx;
        const dy = (size - 1 - row) - radiusPx;
        const dist = dx * dx + dy * dy;
        if (dist < bestDist) { bestDist = dist; bestIndex = index; }
      }
    }
    if (bestIndex < 0) return null;
    return { position: this.positionOf(bestIndex), index: bestIndex };
  }

  positionOf(globalIndex) {
    for (const chunk of this.chunks) {
      const start = chunk.userData.indexOffset;
      const attr = chunk.geometry.getAttribute('position');
      if (globalIndex >= start && globalIndex < start + attr.count) {
        const i = globalIndex - start;
        return new THREE.Vector3(attr.getX(i), attr.getY(i), attr.getZ(i));
      }
    }
    return null;
  }
}

function makeDiscTexture() {
  const size = 32;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 1, 0, Math.PI * 2);
  ctx.fill();
  // Used only as an alpha mask for round points, so it carries no colour.
  return new THREE.CanvasTexture(canvas);
}

/** Encodes each point's global index into the framebuffer for picking. */
function makePickMaterial() {
  return new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    uniforms: { uSize: { value: 4 }, uOffset: { value: 0 } },
    clipping: true,
    vertexShader: /* glsl */`
      #include <clipping_planes_pars_vertex>
      uniform float uSize;
      uniform float uOffset;
      flat out uint vIndex;
      void main() {
        vIndex = uint(uOffset) + uint(gl_VertexID);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        #include <clipping_planes_vertex>
        gl_Position = projectionMatrix * mvPosition;
        gl_PointSize = uSize;
      }
    `,
    fragmentShader: /* glsl */`
      #include <clipping_planes_pars_fragment>
      flat in uint vIndex;
      out vec4 fragColor;
      void main() {
        #include <clipping_planes_fragment>
        vec2 d = gl_PointCoord - vec2(0.5);
        if (dot(d, d) > 0.25) discard;
        fragColor = vec4(
          float(vIndex & 255u) / 255.0,
          float((vIndex >> 8) & 255u) / 255.0,
          float((vIndex >> 16) & 255u) / 255.0,
          1.0);
      }
    `,
  });
}

/** Compact approximation of the Turbo colormap. */
export function turbo(t) {
  const x = Math.min(1, Math.max(0, t));
  return [
    Math.min(1, Math.max(0, 0.13 + x * (4.29 + x * (-14.03 + x * (12.65 - 2.9 * x))))),
    Math.min(1, Math.max(0, 0.09 + x * (2.19 + x * (0.71 + x * (-5.51 + 3.0 * x))))),
    Math.min(1, Math.max(0, 0.11 + x * (7.34 + x * (-24.5 + x * (28.0 - 10.6 * x))))),
  ];
}

/**
 * Clipping planes need the vertex shader chunks; three only injects them when
 * the material declares `clipping: true`, which the pick material does above.
 */
