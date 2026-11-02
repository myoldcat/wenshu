import { normalizeGraphMaterial } from './graph-material-preferences.js';

const slots = { concept: 0, entity: 1, claim: 2, fact: 3, method: 4, document: 5 };
const loads = new Map();
const colors = {
  warm: ['#42594b', '#dfd9cb', '#b97860', '#ece6db', '#d7d9c3', '#e8ddc6'],
  liquid: ['#d5e1d8', '#eeece4', '#e6cbbf', '#e3e7e6', '#dde1cf', '#eee3cc'],
  metal: ['#4e5852', '#c2c3be', '#ba8c75', '#d0d1ce', '#bfc2ad', '#ccb78b'],
};

export function graphMaterialSlot(node, root = false) {
  // A graph's root is a focus, not a new semantic type.
  if (node.type === 'document') return slots.document;
  if (node.subtype === 'method' && !root) return slots.method;
  return slots[node.type] ?? slots.concept;
}

export function graphMaterialStyle(material, node, root = false) {
  const id = normalizeGraphMaterial(material), slot = graphMaterialSlot(node, root);
  const lightInk = id === 'warm' && [0, 2].includes(slot) || id === 'metal' && [0, 5].includes(slot);
  return { id, slot, fill: colors[id][slot], ink: id === 'metal' && slot === 5 ? '#fff8e7' : lightInk ? '#fffdf4' : '#16271f',
    glow: slot === 2 ? '#ab7664' : '#6c9680' };
}

/** Bounds identify the generated object's alpha, never its hidden RGB backdrop. */
export function alphaBounds(pixels, width, height, threshold = 32) {
  let left = width, top = height, right = -1, bottom = -1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (pixels[(y * width + x) * 4 + 3] <= threshold) continue;
    left = Math.min(left, x); top = Math.min(top, y); right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  return right < left ? null : { left, top, width: right - left + 1, height: bottom - top + 1 };
}

function imageLoaded(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('节点材质未能载入，请重试。'));
    img.src = src;
  });
}

// Display sprite cells at their actual alpha bounds. This samples the original
// generated PNGs; it does not paint substitute shapes or bake icons into skins.
export function loadGraphMaterial(material) {
  const id = normalizeGraphMaterial(material);
  if (loads.has(id)) return loads.get(id);
  const pending = imageLoaded(`/assets/graph-materials/${id}-atlas.png`).then(async atlas => {
    const width = Math.floor(atlas.naturalWidth / 3), height = Math.floor(atlas.naturalHeight / 2);
    const sprites = [];
    for (let slot = 0; slot < 6; slot++) {
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(atlas, (slot % 3) * width, Math.floor(slot / 3) * height, width, height, 0, 0, width, height);
      const pixels = ctx.getImageData(0, 0, width, height).data;
      const body = alphaBounds(pixels, width, height, id === 'liquid' ? 72 : 192) || alphaBounds(pixels, width, height);
      if (!body) throw new Error('节点材质内容为空。');
      const src = canvas.toDataURL('image/png');
      // Decode before G6 draws, avoiding blank skins or late image paints after
      // rapid material switches. Cache one image per cell, shared by all nodes.
      await imageLoaded(src);
      sprites.push({ src, body, width, height });
    }
    return { id, sprites };
  }).catch(error => { loads.delete(id); throw error; });
  loads.set(id, pending);
  return pending;
}

export function graphSkinGeometry(sprite, size) {
  const [width, height] = Array.isArray(size) ? size : [size, size];
  const { body } = sprite;
  // Preserve the generated object's aspect ratio rather than stretching it.
  const scale = Math.min(width / body.width, height / body.height);
  return { x: -(body.left + body.width / 2) * scale, y: -(body.top + body.height / 2) * scale,
    width: sprite.width * scale, height: sprite.height * scale };
}

export function cancelMaterialMotion(node) {
  node.materialAnimation?.cancel();
  node.materialAnimation = null;
  if (node.materialAnimationShape && !node.materialAnimationShape.destroyed) node.materialAnimationShape.attr({ transform: '' });
  node.materialAnimationShape = null;
}

export function syncMaterialMotion(node, shape, enabled, seed = 0) {
  if (!enabled || !shape) { cancelMaterialMotion(node); return; }
  if (node.materialAnimation && node.materialAnimationShape === shape) return;
  cancelMaterialMotion(node);
  node.materialAnimationShape = shape;
  // Animate only the optical skin, never node positions, anchors, icons or
  // labels. Slow sub-pixel deformation gives liquid light a quiet flow.
  node.materialAnimation = shape.animate([
    { transform: 'rotate(-2deg) scale(1, 1)' },
    { transform: 'rotate(2deg) scale(1.012, 0.988)' },
    { transform: 'rotate(-2deg) scale(1, 1)' },
  ], { duration: 7200 + seed % 5 * 600, iterations: Infinity, easing: 'ease-in-out' });
}

export function drawMaterialSkin(node, attributes, container) {
  const sprite = attributes.materialSprite;
  if (!sprite) {
    cancelMaterialMotion(node);
    node.upsert('material-skin', 'image', false, container);
    return;
  }
  const previous = node.shapeMap['material-skin'];
  if (previous && node.materialSource !== sprite.src) cancelMaterialMotion(node);
  node.materialSource = sprite.src;
  const shape = node.upsert('material-skin', 'image', {
    src: sprite.src, ...graphSkinGeometry(sprite, attributes.size), zIndex: 1,
    pointerEvents: 'none', transformOrigin: 'center', opacity: attributes.opacity ?? 1,
  }, container);
  syncMaterialMotion(node, shape, attributes.materialMotion, attributes.materialSeed);
}

export function resizeMaterialSkin(node) {
  const sprite = node.attributes.materialSprite, shape = node.shapeMap['material-skin'], key = node.getShape('key');
  if (!sprite || !shape || !key) return;
  const attrs = key.attributes;
  const size = Number.isFinite(attrs.r) ? attrs.r * 2 : [attrs.width, attrs.height];
  shape.attr({ ...graphSkinGeometry(sprite, size), opacity: node.attributes.opacity ?? 1 });
}
