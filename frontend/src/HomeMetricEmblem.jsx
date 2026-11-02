import React from 'react';
import { BookmarkSimple, Books, Clock, Files, GearSix, Notebook, PenNib, Star } from '@phosphor-icons/react';
import './home-metric-icons.css';

const SYMBOLS = {
  writing: [Notebook, PenNib],
  library: [Books, Files],
  saved: [BookmarkSimple, Star],
  processing: [GearSix, Clock],
};

export default function HomeMetricEmblem({ kind, children }) {
  const [Main, Detail] = SYMBOLS[kind] || SYMBOLS.library;
  return <span className="home-metric-emblem" data-kind={kind} aria-hidden="true">
    <span className="home-metric-emblem-art">
      <Main className="home-metric-symbol is-main" weight="duotone"/>
      <Detail className="home-metric-symbol is-detail" weight="duotone"/>
      {kind === 'library' && children && <span className="home-metric-trend">{children}</span>}
    </span>
  </span>;
}
