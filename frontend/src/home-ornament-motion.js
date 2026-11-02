const LOOP_SECONDS = 9;
const REFERENCE_WIDTH = 756;
const MAX_DPR = 1.5;
const COLUMNS = 40;
const ROWS = 24;
const SUBJECTS = { jade: 0, glass: 1, civic: 2 };

const VERTEX_SOURCE = `
precision highp float;
attribute vec2 a_uv;
uniform vec2 u_resolution;
uniform vec2 u_origin;
uniform vec2 u_size;
uniform float u_phase;
uniform float u_subject;
uniform float u_strength;
varying vec2 v_uv;

void main() {
  float x = a_uv.x;
  float y = a_uv.y;
  float t = u_phase;
  float reach = pow(clamp((1.0 - x) / 0.92, 0.0, 1.0), 1.65);
  vec2 move = vec2(0.0);

  if (u_subject < 0.5) {
    // A fixed right-hand stem lets the outer leaf clusters respond to wind.
    move.y = reach * (3.8 * sin(t + x * 3.3 + y * 1.7)
      + 0.6 * sin(t * 2.0 + x * 22.0 - y * 16.0));
    move.x = reach * (1.3 * sin(t + x * 4.0 - y * 2.2)
      + 0.35 * sin(t * 2.0 + y * 31.0));
  } else if (u_subject < 1.5) {
    // Rounded crests flex locally; neither the image nor its right edge drifts.
    float depth = pow(max(0.0, sin(clamp(y, 0.0, 1.0) * 3.14159265)), 1.2);
    move.y = depth * reach * (1.6 * sin(x * 12.0 + t + y * 3.0)
      + 0.6 * sin(x * 21.0 + t * 2.0 - y * 5.0));
    move.x = depth * reach * 0.55 * sin(x * 8.0 + t - y * 2.0);
  } else {
    // Wind travels from the fixed right edge into broader free-end billows.
    float freedom = pow(clamp(1.0 - x, 0.0, 1.0), 1.15);
    float wind = t * 2.0;
    move.y = freedom * (18.0 * sin(wind + x * 8.0 - y * 1.4)
      + 4.0 * sin(wind * 2.0 + x * 12.0 + y * 0.6));
    move.x = freedom * 5.0 * cos(wind + x * 7.0 + y * 0.8);
  }

  vec2 position = u_origin + a_uv * u_size + move * u_strength;
  gl_Position = vec4(position / u_resolution * vec2(2.0, -2.0)
    + vec2(-1.0, 1.0), 0.0, 1.0);
  v_uv = a_uv;
}
`;

const FRAGMENT_SOURCE = `
precision highp float;
uniform sampler2D u_texture;
uniform vec4 u_crop;
uniform float u_phase;
uniform float u_subject;
varying vec2 v_uv;

void main() {
  vec4 color = texture2D(u_texture, u_crop.xy + v_uv * u_crop.zw);
  if (u_subject > 0.5 && u_subject < 1.5) {
    float luminance = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    float light = pow(0.5 + 0.5 * sin(u_phase - v_uv.x * 7.0
      + v_uv.y * 8.0), 4.0);
    color.rgb = mix(color.rgb, vec3(0.92, 0.98, 1.0),
      0.075 * light * smoothstep(0.48, 0.90, luminance));
  }
  gl_FragColor = color;
}
`;

function imageBounds(bounds, width, height) {
  const values = bounds == null
    ? [0, 0, width, height]
    : Array.isArray(bounds)
      ? bounds
      : [bounds.x ?? bounds.left ?? 0, bounds.y ?? bounds.top ?? 0,
        bounds.width, bounds.height];
  const [x, y, cropWidth, cropHeight] = values;
  if (values.length !== 4 || !values.every(Number.isFinite)
    || x < 0 || y < 0 || cropWidth <= 0 || cropHeight <= 0
    || x + cropWidth > width || y + cropHeight > height) {
    throw new RangeError('Invalid home ornament source bounds.');
  }
  return values;
}

/**
 * Render one already-loaded ornament image; scheduling belongs to the caller.
 * bounds is a source-pixel [left, top, width, height] crop (or x/y/width/height).
 * resize takes CSS pixels, preserves the crop's aspect ratio, aligns top/right,
 * and caps the backing resolution at 1.5 DPR. It does not set CSS dimensions.
 * draw takes elapsed seconds; the local deformation repeats every nine seconds.
 * Construction errors throw. Later context loss/draw failures call onError once.
 */
export function createHomeOrnamentMotion(canvas, {
  image,
  subject = 'jade',
  bounds,
  onError,
} = {}) {
  if (!canvas || typeof canvas.getContext !== 'function') {
    throw new TypeError('A home ornament canvas is required.');
  }
  const imageWidth = image?.naturalWidth;
  const imageHeight = image?.naturalHeight;
  if (!image?.complete || !(imageWidth > 0) || !(imageHeight > 0)) {
    throw new TypeError('The home ornament image must finish loading first.');
  }
  if (!Object.prototype.hasOwnProperty.call(SUBJECTS, subject)) {
    throw new TypeError('Unknown home ornament subject.');
  }
  const [cropX, cropY, cropWidth, cropHeight] = imageBounds(bounds,
    imageWidth, imageHeight);
  const gl = canvas.getContext('webgl', {
    alpha: true,
    premultipliedAlpha: false,
    antialias: true,
    preserveDrawingBuffer: false,
    depth: false,
    stencil: false,
  });
  if (!gl) throw new Error('WebGL is unavailable for the home ornament.');

  const shaders = [];
  const buffers = [];
  let program;
  let texture;
  let uniforms;
  let disposed = false;
  let failed = false;
  let indexCount = 0;

  function notifyError(error) {
    if (disposed || failed) return;
    failed = true;
    if (typeof onError === 'function') onError(error);
  }

  function contextLost() {
    notifyError(new Error('The home ornament WebGL context was lost.'));
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    canvas.removeEventListener('webglcontextlost', contextLost);
    for (const buffer of buffers) gl.deleteBuffer(buffer);
    if (texture) gl.deleteTexture(texture);
    if (program) gl.deleteProgram(program);
    for (const shader of shaders) gl.deleteShader(shader);
  }

  function compile(type, source) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error('Could not allocate a home ornament shader.');
    shaders.push(shader);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) || 'Home ornament shader failed.');
    }
    return shader;
  }

  function buffer(target, values) {
    const result = gl.createBuffer();
    if (!result) throw new Error('Could not allocate a home ornament buffer.');
    buffers.push(result);
    gl.bindBuffer(target, result);
    gl.bufferData(target, values, gl.STATIC_DRAW);
    return result;
  }

  function resize(width, height, dpr = globalThis.devicePixelRatio || 1) {
    if (disposed || failed) return;
    if (!(Number.isFinite(width) && width > 0
      && Number.isFinite(height) && height > 0)) {
      throw new RangeError('Home ornament dimensions must be positive CSS pixels.');
    }
    const pixelRatio = Math.min(MAX_DPR,
      Number.isFinite(dpr) && dpr > 0 ? dpr : 1);
    const pixelWidth = Math.max(1, Math.round(width * pixelRatio));
    const pixelHeight = Math.max(1, Math.round(height * pixelRatio));
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight;

    const scale = Math.min(width / cropWidth, height / cropHeight);
    const drawnWidth = cropWidth * scale;
    const drawnHeight = cropHeight * scale;
    gl.viewport(0, 0, pixelWidth, pixelHeight);
    gl.useProgram(program);
    gl.uniform2f(uniforms.resolution, width, height);
    gl.uniform2f(uniforms.origin, width - drawnWidth, 0);
    gl.uniform2f(uniforms.size, drawnWidth, drawnHeight);
    // The approved preview used 756 CSS pixels; preserve its amplitude ratio.
    gl.uniform1f(uniforms.strength, width / REFERENCE_WIDTH);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  function draw(seconds) {
    if (disposed || failed) return;
    if (gl.isContextLost()) {
      contextLost();
      return;
    }
    try {
      const time = Number.isFinite(seconds) ? seconds : 0;
      const phase = ((time % LOOP_SECONDS) + LOOP_SECONDS) % LOOP_SECONDS;
      gl.useProgram(program);
      gl.uniform1f(uniforms.phase, phase / LOOP_SECONDS * Math.PI * 2);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawElements(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0);
    } catch (error) {
      notifyError(error);
    }
  }

  try {
    program = gl.createProgram();
    if (!program) throw new Error('Could not allocate the home ornament program.');
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX_SOURCE));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT_SOURCE));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) || 'Home ornament program failed.');
    }
    gl.useProgram(program);
    uniforms = Object.fromEntries(['resolution', 'origin', 'size', 'phase',
      'subject', 'strength', 'texture', 'crop'].map(name => [name,
      gl.getUniformLocation(program, `u_${name}`)]));

    // The mesh and UV coordinates stay fixed; resize changes only uniforms.
    const uvs = new Float32Array((COLUMNS + 1) * (ROWS + 1) * 2);
    let offset = 0;
    for (let row = 0; row <= ROWS; row++) {
      for (let column = 0; column <= COLUMNS; column++) {
        uvs[offset++] = column / COLUMNS;
        uvs[offset++] = row / ROWS;
      }
    }
    buffer(gl.ARRAY_BUFFER, uvs);
    const uvLocation = gl.getAttribLocation(program, 'a_uv');
    if (uvLocation < 0) throw new Error('The home ornament UV attribute is missing.');
    gl.enableVertexAttribArray(uvLocation);
    gl.vertexAttribPointer(uvLocation, 2, gl.FLOAT, false, 0, 0);

    const indices = new Uint16Array(COLUMNS * ROWS * 6);
    offset = 0;
    for (let row = 0; row < ROWS; row++) {
      for (let column = 0; column < COLUMNS; column++) {
        const first = row * (COLUMNS + 1) + column;
        indices.set([first, first + 1, first + COLUMNS + 1,
          first + COLUMNS + 1, first + 1, first + COLUMNS + 2], offset);
        offset += 6;
      }
    }
    buffer(gl.ELEMENT_ARRAY_BUFFER, indices);
    indexCount = indices.length;

    texture = gl.createTexture();
    if (!texture) throw new Error('Could not allocate the home ornament texture.');
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    gl.uniform1i(uniforms.texture, 0);
    gl.uniform4f(uniforms.crop, cropX / imageWidth, cropY / imageHeight,
      cropWidth / imageWidth, cropHeight / imageHeight);
    gl.uniform1f(uniforms.subject, SUBJECTS[subject]);
    gl.uniform1f(uniforms.phase, 0);
    gl.clearColor(0, 0, 0, 0);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.BLEND);

    const rect = canvas.getBoundingClientRect();
    resize(rect.width || canvas.width || 1, rect.height || canvas.height || 1);
    const error = gl.getError();
    if (error !== gl.NO_ERROR) {
      throw new Error(`Could not initialize the home ornament renderer (${error}).`);
    }
    canvas.addEventListener('webglcontextlost', contextLost);
  } catch (error) {
    dispose();
    throw error;
  }

  return { draw, resize, dispose };
}
