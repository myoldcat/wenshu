import { drawThemeTexture } from './aurora-textures';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const PALETTES = {
  jade: { top: '#f1f5ee', bottom: '#f6f7f0', colors: [[99, 153, 119], [137, 172, 124], [124, 164, 151]], phase: .3 },
  glass: { top: '#f2f9fd', bottom: '#f9fcfe', colors: [[117, 178, 204], [136, 196, 198], [165, 184, 211]], phase: 1.8 },
  civic: { top: '#f8f2ef', bottom: '#faf6f0', colors: [[181, 124, 133], [191, 154, 121], [176, 142, 153]], phase: 3.4 },
};
const rgba = (color, alpha) => `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;

function glow(ctx, x, y, radiusX, radiusY, color, opacity) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(radiusX, radiusY);
  const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
  gradient.addColorStop(0, rgba(color, opacity));
  gradient.addColorStop(.45, rgba(color, opacity * .45));
  gradient.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = gradient;
  ctx.fillRect(-1, -1, 2, 2);
  ctx.restore();
}

export function drawAmbientScene(ctx, {
  theme, width, height, time = 0, pointer = { x: 0, y: 0 }, energy = 0, flow = { x: 0, y: 0 },
}) {
  if (!ctx || width <= 0 || height <= 0) return;
  const palette = PALETTES[theme] || PALETTES.jade;
  const cursor = { x: clamp(pointer.x || 0, -1, 1), y: clamp(pointer.y || 0, -1, 1) };
  const response = clamp(energy || 0, 0, 1);
  const drift = time * .075;
  ctx.save();
  const base = ctx.createLinearGradient(0, 0, width * .25, height);
  base.addColorStop(0, palette.top);
  base.addColorStop(1, palette.bottom);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, width, height);

  // A faint ground tint supports the large moving color masses. No light strands,
  // outlined lenses or refraction in the background; glass lives on UI edges.
  glow(ctx, width * (.16 + Math.sin(drift + palette.phase) * .07),
    height * .3, width * .6, height * .7,
    palette.colors[0], theme === 'glass' ? .045 : .065);
  glow(ctx, width * .8, height * .78,
    width * .65, height * .6, palette.colors[1], .045);
  drawThemeTexture(ctx, { theme, width, height, palette, time, pointer: cursor, energy: response, flow });
  ctx.restore();
}
