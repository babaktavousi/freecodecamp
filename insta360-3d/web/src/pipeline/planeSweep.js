/**
 * Spherical plane-sweep multi-view stereo on WebGL2.
 *
 * For each keyframe the reference sphere is swept through a stack of
 * inverse-depth hypotheses. Every hypothesis places each pixel's ray at a
 * candidate depth, projects that point into the neighbouring keyframes
 * (sampled directly in equirectangular space) and scores the photometric
 * agreement. The winning hypothesis per pixel is the depth.
 *
 * The GPU keeps only running accumulators rather than a full cost volume, so
 * memory stays flat in the number of layers.
 */

const VERTEX_SHADER = `#version 300 es
out vec2 vUv;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  vUv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const COMMON = `
precision highp float;
precision highp sampler2D;
const float PI = 3.141592653589793;

vec3 dirFromUv(vec2 uv) {
  float lon = uv.x * 2.0 * PI - PI;
  float lat = PI * 0.5 - uv.y * PI;
  float cl = cos(lat);
  return vec3(cl * sin(lon), sin(lat), cl * cos(lon));
}

vec2 uvFromDir(vec3 d) {
  float lon = atan(d.x, d.z);
  float lat = asin(clamp(d.y, -1.0, 1.0));
  return vec2((lon + PI) / (2.0 * PI), (PI * 0.5 - lat) / PI);
}`;

const GRAY_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uSource;
out float outGray;
void main() {
  vec3 c = texture(uSource, vUv).rgb;
  outGray = dot(c, vec3(0.299, 0.587, 0.114));
}`;

const BLUR_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uSource;
uniform vec2 uStep;
uniform int uRadius;
out float outValue;
void main() {
  float sum = 0.0;
  float count = 0.0;
  for (int i = -12; i <= 12; i++) {
    if (i < -uRadius || i > uRadius) continue;
    sum += texture(uSource, vUv + uStep * float(i)).r;
    count += 1.0;
  }
  outValue = sum / count;
}`;

const HIGHPASS_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uGray;
uniform sampler2D uBlur;
out float outValue;
void main() {
  outValue = texture(uGray, vUv).r - texture(uBlur, vUv).r;
}`;

const COST_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uRef;
uniform sampler2D uNb0;
uniform sampler2D uNb1;
uniform sampler2D uNb2;
uniform sampler2D uNb3;
uniform sampler2D uNb4;
uniform sampler2D uNb5;
uniform mat3 uRefRot;
uniform vec3 uRefC;
uniform mat3 uNbRot[6];
uniform vec3 uNbC[6];
uniform int uNbCount;
uniform float uInvDepth;
out float outCost;

float nbCost(vec3 X, float ref, int i, sampler2D tex) {
  vec3 local = transpose(uNbRot[i]) * (X - uNbC[i]);
  vec2 uv = uvFromDir(normalize(local));
  return abs(ref - texture(tex, uv).r);
}

void main() {
  vec3 world = uRefRot * dirFromUv(vUv);
  vec3 X = uRefC + world / uInvDepth;
  float ref = texture(uRef, vUv).r;

  float c[6];
  c[0] = c[1] = c[2] = c[3] = c[4] = c[5] = 1e9;
  if (uNbCount > 0) c[0] = nbCost(X, ref, 0, uNb0);
  if (uNbCount > 1) c[1] = nbCost(X, ref, 1, uNb1);
  if (uNbCount > 2) c[2] = nbCost(X, ref, 2, uNb2);
  if (uNbCount > 3) c[3] = nbCost(X, ref, 3, uNb3);
  if (uNbCount > 4) c[4] = nbCost(X, ref, 4, uNb4);
  if (uNbCount > 5) c[5] = nbCost(X, ref, 5, uNb5);

  if (uNbCount <= 2) {
    outCost = uNbCount == 1 ? c[0] : 0.5 * (c[0] + c[1]);
    return;
  }
  // Mean of the two best views: an occlusion in one neighbour should not
  // destroy an otherwise well supported hypothesis.
  float b0 = 1e9, b1 = 1e9;
  for (int i = 0; i < 6; i++) {
    float v = c[i];
    if (v < b0) { b1 = b0; b0 = v; }
    else if (v < b1) { b1 = v; }
  }
  outCost = 0.5 * (b0 + b1);
}`;

const UPDATE_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uCost;
uniform sampler2D uA;
uniform sampler2D uB;
uniform vec2 uTexel;
uniform float uLayer;
layout(location = 0) out vec4 outA;
layout(location = 1) out vec4 outB;

void main() {
  // Aggregate the raw cost over a small window: single pixels are far too
  // ambiguous to pick a depth from.
  float cost = 0.0;
  for (int dy = -2; dy <= 2; dy++) {
    for (int dx = -2; dx <= 2; dx++) {
      cost += texture(uCost, vUv + uTexel * vec2(float(dx), float(dy))).r;
    }
  }
  cost /= 25.0;

  vec4 a = texture(uA, vUv);   // best, bestLayer, second, costPrev
  vec4 b = texture(uB, vUv);   // costPrev2, costAtBestMinus1, costAtBestPlus1, -
  float best = a.x, bestK = a.y, second = a.z, costPrev = a.w;
  float costPrev2 = b.x, cMinus = b.y, cPlus = b.z;

  bool improved = cost < best;
  // The runner-up has to be a competing *local* minimum: layers next to the
  // winner always cost nearly the same, and counting them would drive every
  // confidence value to zero.
  bool isLocalMin = costPrev < costPrev2 && costPrev < cost && bestK != uLayer - 1.0;

  float newSecond = improved ? min(second, best) : second;
  newSecond = isLocalMin ? min(newSecond, costPrev) : newSecond;

  outA = vec4(
    improved ? cost : best,
    improved ? uLayer : bestK,
    newSecond,
    cost);
  outB = vec4(
    costPrev,
    improved ? costPrev : cMinus,
    bestK == uLayer - 1.0 ? cost : cPlus,
    0.0);
}`;

const FINALIZE_SHADER = `#version 300 es
${COMMON}
in vec2 vUv;
uniform sampler2D uA;
uniform sampler2D uB;
uniform sampler2D uRef;
uniform vec2 uTexel;
uniform float uInvFar;
uniform float uInvStep;
out vec4 outValue;

void main() {
  vec4 a = texture(uA, vUv);
  vec4 b = texture(uB, vUv);
  float best = a.x, bestK = a.y, second = a.z;
  float cMinus = b.y, cPlus = b.z;

  // Sub-layer peak in inverse-depth space.
  float sub = 0.0;
  if (cMinus < 1e8 && cPlus < 1e8) {
    float denom = cMinus - 2.0 * best + cPlus;
    if (abs(denom) > 1e-9) sub = clamp(0.5 * (cMinus - cPlus) / denom, -1.0, 1.0);
  }
  float inv = uInvFar + (bestK + sub) * uInvStep;
  float depth = 1.0 / max(inv, 1e-6);
  float confidence = best > 1e-6 ? (second - best) / (best + 1e-3) : 0.0;

  // Local contrast, so textureless surfaces can be dropped: their winning
  // layer is arbitrary.
  float texture_ = 0.0;
  for (int dy = -2; dy <= 2; dy++) {
    for (int dx = -2; dx <= 2; dx++) {
      texture_ += abs(texture(uRef, vUv + uTexel * vec2(float(dx), float(dy))).r);
    }
  }
  outValue = vec4(depth, confidence, bestK, texture_ / 25.0);
}`;

export class PlaneSweepGL {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.canvas = typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(width, height)
      : Object.assign(document.createElement('canvas'), { width, height });

    const gl = this.canvas.getContext('webgl2', {
      antialias: false,
      depth: false,
      preserveDrawingBuffer: false,
    });
    if (!gl) throw new Error('WebGL2 is required for reconstruction in the browser.');
    this.gl = gl;
    if (!gl.getExtension('EXT_color_buffer_float')) {
      throw new Error('This GPU/browser lacks EXT_color_buffer_float, needed for depth sweeping.');
    }
    // Neighbours are sampled at arbitrary positions on the sphere, so the
    // high-pass images want interpolation; without it every match snaps to a
    // texel centre and the depth quantises with it.
    this.floatLinear = !!gl.getExtension('OES_texture_float_linear');

    this.programs = {
      gray: this._program(GRAY_SHADER),
      blur: this._program(BLUR_SHADER),
      highpass: this._program(HIGHPASS_SHADER),
      cost: this._program(COST_SHADER),
      update: this._program(UPDATE_SHADER),
      finalize: this._program(FINALIZE_SHADER),
    };
    this.vao = gl.createVertexArray();
    this.fbo = gl.createFramebuffer();

    this.hpTextures = [];
    this._scratch = {
      gray: this._texture(gl.R32F, { interpolate: true }),
      blurA: this._texture(gl.R32F, { interpolate: true }),
      blurB: this._texture(gl.R32F, { interpolate: true }),
      cost: this._texture(gl.R32F),
      a: [this._texture(gl.RGBA32F), this._texture(gl.RGBA32F)],
      b: [this._texture(gl.RGBA32F), this._texture(gl.RGBA32F)],
      out: this._texture(gl.RGBA32F),
    };
    this.result = new Float32Array(width * height * 4);
  }

  _program(fragmentSource) {
    const gl = this.gl;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        throw new Error(`shader: ${gl.getShaderInfoLog(shader)}`);
      }
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX_SHADER));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`link: ${gl.getProgramInfoLog(program)}`);
    }
    return program;
  }

  _texture(internalFormat, { interpolate = false } = {}) {
    const gl = this.gl;
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texStorage2D(gl.TEXTURE_2D, 1, internalFormat, this.width, this.height);
    // Longitude wraps around the sphere; latitude clamps at the poles.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    // Accumulators are read back texel for texel and must not be filtered.
    const linear = interpolate && (internalFormat === gl.RGBA8 || this.floatLinear);
    const filter = linear ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    return texture;
  }

  _bindTargets(targets) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    const buffers = [];
    targets.forEach((texture, i) => {
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, texture, 0);
      buffers.push(gl.COLOR_ATTACHMENT0 + i);
    });
    for (let i = targets.length; i < 2; i++) {
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, null, 0);
    }
    gl.drawBuffers(buffers);
    gl.viewport(0, 0, this.width, this.height);
  }

  _draw(targets) {
    const gl = this.gl;
    this._bindTargets(targets);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  _bind(program, name, texture, unit) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1i(gl.getUniformLocation(program, name), unit);
  }

  /** Upload one keyframe and store its high-pass gray image on the GPU. */
  addFrame(source) {
    const gl = this.gl;
    const rgb = this._texture(gl.RGBA8, { interpolate: true });
    gl.bindTexture(gl.TEXTURE_2D, rgb);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, source);

    const { gray, blurA, blurB } = this._scratch;
    gl.useProgram(this.programs.gray);
    this._bind(this.programs.gray, 'uSource', rgb, 0);
    this._draw([gray]);

    const radius = Math.max(2, Math.round(this.width / 128));
    gl.useProgram(this.programs.blur);
    gl.uniform1i(gl.getUniformLocation(this.programs.blur, 'uRadius'), Math.min(12, radius));
    gl.uniform2f(gl.getUniformLocation(this.programs.blur, 'uStep'), 1 / this.width, 0);
    this._bind(this.programs.blur, 'uSource', gray, 0);
    this._draw([blurA]);
    gl.uniform2f(gl.getUniformLocation(this.programs.blur, 'uStep'), 0, 1 / this.height);
    this._bind(this.programs.blur, 'uSource', blurA, 0);
    this._draw([blurB]);

    const hp = this._texture(gl.R32F, { interpolate: true });
    gl.useProgram(this.programs.highpass);
    this._bind(this.programs.highpass, 'uGray', gray, 0);
    this._bind(this.programs.highpass, 'uBlur', blurB, 1);
    this._draw([hp]);

    gl.deleteTexture(rgb);
    this.hpTextures.push(hp);
    return this.hpTextures.length - 1;
  }

  /**
   * Sweep one keyframe against its neighbours.
   * @returns {Float32Array} RGBA per pixel: depth, confidence, layer, texture
   */
  sweep({ refIndex, neighbours, centres, rotations, layers, minDepth, maxDepth }) {
    const gl = this.gl;
    const { cost, a, b, out } = this._scratch;
    const nb = neighbours.slice(0, 6);

    // Reset the accumulators: best = +inf stand-in, layer = -1.
    for (const [texA, texB] of [[a[0], b[0]], [a[1], b[1]]]) {
      this._bindTargets([texA, texB]);
      gl.clearBufferfv(gl.COLOR, 0, [1e9, -1, 1e9, 1e9]);
      gl.clearBufferfv(gl.COLOR, 1, [1e9, 1e9, 1e9, 0]);
    }

    const invFar = 1 / maxDepth;
    const invNear = 1 / minDepth;
    const step = (invNear - invFar) / Math.max(1, layers - 1);

    const costProgram = this.programs.cost;
    gl.useProgram(costProgram);
    gl.uniform1i(gl.getUniformLocation(costProgram, 'uNbCount'), nb.length);
    gl.uniformMatrix3fv(gl.getUniformLocation(costProgram, 'uRefRot'), false, columnMajor(rotations[refIndex]));
    gl.uniform3f(gl.getUniformLocation(costProgram, 'uRefC'),
      centres[refIndex * 3], centres[refIndex * 3 + 1], centres[refIndex * 3 + 2]);
    const nbRot = new Float32Array(54);
    const nbC = new Float32Array(18);
    nb.forEach((index, i) => {
      nbRot.set(columnMajor(rotations[index]), i * 9);
      nbC.set([centres[index * 3], centres[index * 3 + 1], centres[index * 3 + 2]], i * 3);
    });
    gl.uniformMatrix3fv(gl.getUniformLocation(costProgram, 'uNbRot'), false, nbRot);
    gl.uniform3fv(gl.getUniformLocation(costProgram, 'uNbC'), nbC);
    const invDepthLoc = gl.getUniformLocation(costProgram, 'uInvDepth');

    const updateProgram = this.programs.update;
    const layerLoc = gl.getUniformLocation(updateProgram, 'uLayer');

    let ping = 0;
    for (let k = 0; k < layers; k++) {
      gl.useProgram(costProgram);
      this._bind(costProgram, 'uRef', this.hpTextures[refIndex], 0);
      for (let i = 0; i < 6; i++) {
        const index = nb[Math.min(i, nb.length - 1)] ?? refIndex;
        this._bind(costProgram, `uNb${i}`, this.hpTextures[index], 1 + i);
      }
      gl.uniform1f(invDepthLoc, invFar + k * step);
      this._draw([cost]);

      gl.useProgram(updateProgram);
      this._bind(updateProgram, 'uCost', cost, 0);
      this._bind(updateProgram, 'uA', a[ping], 1);
      this._bind(updateProgram, 'uB', b[ping], 2);
      gl.uniform2f(gl.getUniformLocation(updateProgram, 'uTexel'), 1 / this.width, 1 / this.height);
      gl.uniform1f(layerLoc, k);
      this._draw([a[1 - ping], b[1 - ping]]);
      ping = 1 - ping;
    }

    const finalize = this.programs.finalize;
    gl.useProgram(finalize);
    this._bind(finalize, 'uA', a[ping], 0);
    this._bind(finalize, 'uB', b[ping], 1);
    this._bind(finalize, 'uRef', this.hpTextures[refIndex], 2);
    gl.uniform2f(gl.getUniformLocation(finalize, 'uTexel'), 1 / this.width, 1 / this.height);
    gl.uniform1f(gl.getUniformLocation(finalize, 'uInvFar'), invFar);
    gl.uniform1f(gl.getUniformLocation(finalize, 'uInvStep'), step);
    this._draw([out]);

    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, null, 0);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, out, 0);
    gl.readBuffer(gl.COLOR_ATTACHMENT0);
    gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.FLOAT, this.result);
    return this.result;
  }

  dispose() {
    const gl = this.gl;
    for (const texture of this.hpTextures) gl.deleteTexture(texture);
    this.hpTextures = [];
    for (const value of Object.values(this._scratch)) {
      if (Array.isArray(value)) value.forEach((t) => gl.deleteTexture(t));
      else gl.deleteTexture(value);
    }
    gl.deleteFramebuffer(this.fbo);
    for (const program of Object.values(this.programs)) gl.deleteProgram(program);
  }
}

/** GLSL mat3 uniforms are column major; the pipeline stores rows. */
function columnMajor(m) {
  return new Float32Array([m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]);
}
