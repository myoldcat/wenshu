import React from 'react';
import jadeMark from './assets/brand/symbol-jade-v7.png';
import glassMark from './assets/brand/symbol-glass-v7.png';
import goldMark from './assets/brand/symbol-gold-v7.png';
import literary from './assets/brand/wordmark-xingshu-v6.png';
import technology from './assets/brand/wordmark-tech-upright-v7.png';
import chinese from './assets/brand/wordmark-lishu-v6.png';

// Keep the supplied silhouette and each generated material's depth and highlights.
// Eagerly mounted images preserve instant theme switching without recoloring them.
export function BrandMark() {
  return <span className="wenshu-mark" aria-hidden="true">
    <img className="brand-symbol brand-symbol-jade" src={jadeMark} alt="" loading="eager" decoding="async" draggable="false"/>
    <img className="brand-symbol brand-symbol-glass" src={glassMark} alt="" loading="eager" decoding="async" draggable="false"/>
    <img className="brand-symbol brand-symbol-civic" src={goldMark} alt="" loading="eager" decoding="async" draggable="false"/>
  </span>;
}

// Keep the generated letterforms, colors and textures; never substitute DOM fonts.
// Spacing is part of the artwork so extended calligraphic strokes stay intact.
// All three assets stay mounted and load eagerly for an instant theme switch.
export function BrandWordmark() {
  return <span className="brand-wordmark" aria-hidden="true">
    {[["jade", literary], ["glass", technology], ["civic", chinese]].map(([theme, source]) =>
      <img className={`brand-artwork brand-artwork-${theme}`} key={theme} src={source} alt="" loading="eager" decoding="async" draggable="false"/>
    )}
  </span>;
}
