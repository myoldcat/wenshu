import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { BookOpen, Cube, Quotes, ListBullets, FileText, User, Path } from '@phosphor-icons/react/dist/ssr';

// Use the same licensed Phosphor family as the inspector and controls. Keeping
// icons separate from the generated skins preserves legibility at every zoom.
const icons = { concept: BookOpen, entity: Cube, claim: Quotes, fact: ListBullets, document: FileText, person: User, method: Path };
const cache = new Map();
export function graphMaterialIcon(node, color) {
  const name = ['method', 'person'].includes(node.subtype) ? node.subtype : node.type;
  const key = `${name}:${color}`;
  if (!cache.has(key)) cache.set(key, `data:image/svg+xml;charset=utf-8,${encodeURIComponent(renderToStaticMarkup(createElement(icons[name] || BookOpen, { size: 32, color, weight: 'bold' })))}`);
  return cache.get(key);
}
