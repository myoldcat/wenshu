// A continuous low-resolution color field, smoothly enlarged behind the UI.
// Pointer motion deforms only its nearby area; the background has no glass cues.
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
const SURFACES = new WeakMap();
const POINTER_RADIUS = 240;

const FIELDS = {
  jade: [
    { x: .18, y: .12, width: .65, height: 270, phase: .3, color: 0, opacity: .29, tilt: -.20, bend: .17 },
    { x: .76, y: .69, width: .65, height: 290, phase: 2.5, color: 1, opacity: .25, tilt: .18, bend: -.12 },
    { x: .43, y: 1.22, width: .56, height: 270, phase: 4.8, color: 2, opacity: .15, tilt: -.14, bend: .20 },
    { x: .94, y: .74, width: .63, height: 350, phase: 1.7, color: 0, opacity: .11, body: true, tilt: -.20, bend: .13 },
  ],
  glass: [
    { x: .24, y: .18, width: .63, height: 330, phase: .7, color: 0, opacity: .30, tilt: .10, bend: .29 },
    { x: .77, y: .41, width: .61, height: 350, phase: 3.2, color: 1, opacity: .28, tilt: -.16, bend: -.26 },
    { x: .52, y: 1.20, width: .58, height: 290, phase: 5.1, color: 2, opacity: .18, tilt: .08, bend: .22 },
    { x: .08, y: .77, width: .60, height: 360, phase: 2.1, color: 1, opacity: .11, body: true, tilt: -.12, bend: -.20 },
  ],
  civic: [
    { x: .22, y: .31, width: .70, height: 310, phase: .1, color: 0, opacity: .27, tilt: .24, bend: -.19 },
    { x: .83, y: .23, width: .65, height: 330, phase: 2.7, color: 1, opacity: .29, tilt: -.22, bend: .20 },
    { x: .55, y: 1.11, width: .62, height: 290, phase: 4.5, color: 2, opacity: .16, tilt: .14, bend: -.15 },
    { x: .88, y: .81, width: .67, height: 360, phase: 1.8, color: 0, opacity: .10, body: true, tilt: -.20, bend: .16 },
  ],
};

function surfaceFor(ctx, width, height) {
  const scale = Math.min(200 / width, 140 / height, 1);
  const columns = Math.max(1, Math.round(width * scale));
  const rows = Math.max(1, Math.round(height * scale));
  let surface = SURFACES.get(ctx);
  if (!surface || surface.columns !== columns || surface.rows !== rows) {
    const canvas = typeof OffscreenCanvas === 'function'
      ? new OffscreenCanvas(columns, rows)
      : document.createElement('canvas');
    canvas.width = columns;
    canvas.height = rows;
    const context = canvas.getContext('2d');
    surface = { canvas, context, columns, rows, pixels: context.createImageData(columns, rows) };
    SURFACES.set(ctx, surface);
  }
  return surface;
}

function movingFields({ theme, width, height, palette, time }) {
  const header = clamp(height * .32, 230, 280);
  const rate = theme === 'glass' ? .20 : theme === 'civic' ? .16 : .18;
  return (FIELDS[theme] || FIELDS.jade).map(field => {
    const drift = time * rate * (field.body ? 1 : 1.16);
    const depth = field.body ? .45 : 1;
    const phase = field.phase;
    const widthBreath = field.body ? 1 : 1 + Math.sin(drift * .91 + phase) * .03;
    const heightBreath = field.body ? 1 : 1 + Math.sin(drift * .83 + phase * .7) * .06;
    return {
      x: width * field.x + Math.sin(drift * .69 + phase) * width * (field.body ? .035 : .045) * depth,
      y: (field.body ? height : header) * field.y + Math.cos(drift + phase) * (field.body ? 31 : 43) * depth,
      inverseX: 2 / (width * field.width * widthBreath),
      inverseY: 2 / (clamp(field.height * (height / 900) ** .16, 200, 380) * heightBreath),
      skew: field.tilt + Math.sin(drift * .73 + phase) * (field.body ? .10 : .14),
      bend: field.bend + Math.cos(drift * .81 + phase) * (field.body ? .055 : .08),
      roundness: theme === 'jade' ? 1.12 : theme === 'civic' ? 1.32 : 1.20,
      opacity: field.opacity,
      color: palette.colors[field.color],
    };
  });
}

export function drawThemeTexture(ctx, options) {
  const { width, height, pointer = { x: 0, y: 0 }, flow = { x: 0, y: 0 } } = options;
  if (width <= 0 || height <= 0) return;
  const surface = surfaceFor(ctx, width, height);
  const fields = movingFields(options);
  const { columns, rows, pixels } = surface;
  const data = pixels.data;
  const stepX = width / columns, stepY = height / rows;
  const cursorX = (pointer.x + 1) * width * .5;
  const cursorY = (pointer.y + 1) * height * .5;
  const flowX = clamp(flow.x || 0, -1, 1) * 68;
  const flowY = clamp(flow.y || 0, -1, 1) * 68;
  const radiusSquared = POINTER_RADIUS * POINTER_RADIUS;
  const hasFlow = Math.abs(flowX) + Math.abs(flowY) > .001;

  for (let row = 0; row < rows; row += 1) {
    const pixelY = (row + .5) * stepY;
    for (let column = 0; column < columns; column += 1) {
      const pixelX = (column + .5) * stepX;
      let x = pixelX, y = pixelY;
      if (hasFlow) {
        const dx = pixelX - cursorX, dy = pixelY - cursorY;
        const distanceSquared = dx * dx + dy * dy;
        if (distanceSquared < radiusSquared) {
          const q = distanceSquared / radiusSquared;
          // A 2D Gaussian with a smooth compact envelope. Its value and slope
          // reach zero at 240px, so a remote area never follows the pointer.
          const influence = Math.exp(-q / .44) * (1 - q) * (1 - q);
          x -= flowX * influence;
          y -= flowY * influence;
        }
      }

      let total = 0, red = 0, green = 0, blue = 0;
      for (const field of fields) {
        const dx = (x - field.x) * field.inverseX;
        const dy = (y - field.y) * field.inverseY;
        const u = dx + field.skew * dy;
        const v = dy + field.bend * dx * dx / (1 + dx * dx);
        const distance = u * u + v * v;
        if (distance > 5) continue;
        // Smooth analytic density; no nested silhouettes or polygon edges.
        const density = field.opacity * Math.exp(-1.7 * Math.pow(distance, field.roundness));
        total += density;
        red += field.color[0] * density;
        green += field.color[1] * density;
        blue += field.color[2] * density;
      }
      const index = (row * columns + column) * 4;
      if (total > .0001) {
        data[index] = red / total;
        data[index + 1] = green / total;
        data[index + 2] = blue / total;
        // Stable sub-byte dither prevents visible quantization bands without
        // introducing a moving texture or sparkling noise.
        const dither = (((column * 17 + row * 31) % 23) / 22 - .5) * .65;
        data[index + 3] = clamp((1 - Math.exp(-total)) * 255 + dither, 0, 255);
      } else {
        data[index] = data[index + 1] = data[index + 2] = data[index + 3] = 0;
      }
    }
  }

  surface.context.putImageData(pixels, 0, 0);
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(surface.canvas, 0, 0, width, height);
  ctx.restore();
}
