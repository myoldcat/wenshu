import React, { useEffect, useState } from 'react';
import { ArrowsClockwise, CaretRight, Leaf, Drop, CircleHalfTilt } from '@phosphor-icons/react';
import { DEFAULT_GRAPH_MATERIAL, GRAPH_MATERIALS } from './graph-material-preferences';
import { graphSkinGeometry, loadGraphMaterial } from './graph-materials';

const MATERIAL_ICONS = { warm: Leaf, liquid: Drop, metal: CircleHalfTilt };

function MaterialSwatch({ material }) {
  const [sprite, setSprite] = useState(null);
  const Icon = MATERIAL_ICONS[material];
  useEffect(() => {
    let cancelled = false;
    setSprite(null);
    loadGraphMaterial(material).then(result => {
      if (!cancelled) setSprite(result.sprites[1]);
    }).catch(() => {
      if (!cancelled) setSprite(null);
    });
    return () => { cancelled = true; };
  }, [material]);
  const geometry = sprite && graphSkinGeometry(sprite, 34);
  return <span className={`gw-material-swatch material-${material}`} aria-hidden="true">
    {sprite && <img src={sprite.src} alt="" draggable="false" style={{ left: 17 + geometry.x, top: 17 + geometry.y, width: geometry.width, height: geometry.height }}/>}<Icon size={18} weight="duotone"/>
  </span>;
}

export const GRAPH_LAYOUTS = [
  ['force', '力导向', '按关系自然舒展，适合总览知识网络'],
  ['radial', '径向', '围绕当前主题或探索词条排列'],
  ['concentric', '同心圆', '按重要性或连接数量分层排列'],
  ['dagre', '层次', '沿关系方向分层排列'],
];
export const GRAPH_METRICS = { none: '常规大小', pagerank: 'PageRank', degree: '连接数量', betweenness: '桥梁作用', coreNumber: '核心层级' };
export const metricValue = (value, metric) => !Number.isFinite(value) ? '—'
  : ['degree', 'coreNumber'].includes(metric) ? String(value)
    : value === 0 ? '0' : value < .001 ? value.toExponential(2) : value.toFixed(4);

export function GraphPresentationControls({ layout, onLayout, material = DEFAULT_GRAPH_MATERIAL, onMaterial, metric, onMetric, motion, onMotion, scope, onScope, analytics, onNavigate, topicLabel, visibleIds }) {
  const ranking = analytics.data?.rankings?.[metric] || [];
  const approximate = analytics.data?.algorithms?.betweenness?.approximate || analytics.data?.algorithms?.betweenness?.approximation;
  return <div className="gw-presentation-controls">
    <strong>节点材质</strong>
    <div className="gw-material-options" role="group" aria-label="节点材质">{GRAPH_MATERIALS.map(({ id, label, description }) => <button key={id} type="button" title={description} aria-label={`${label}材质`} aria-pressed={material === id} onClick={() => onMaterial?.(id)}><MaterialSwatch material={id}/><span>{label}</span></button>)}</div>
    <strong>布局方式</strong>
    <div className="gw-layout-options" role="group" aria-label="图谱布局">{GRAPH_LAYOUTS.map(([id, label, description]) => <button key={id} title={description} aria-pressed={layout === id} onClick={() => onLayout(id)}><i className={`gw-layout-glyph layout-${id}`} aria-hidden="true"/>{label}</button>)}</div>
    <label className="gw-label-toggle gw-motion-toggle"><input type="checkbox" checked={motion} onChange={event => onMotion(event.target.checked)}/>交互动效<span>跟随系统减少动态效果偏好</span></label>
    <div className="gw-ranking-heading"><strong>重要节点</strong><button onClick={analytics.refresh} disabled={analytics.loading || !analytics.data} aria-label="重新计算重要节点" title="重新计算重要节点"><ArrowsClockwise size={14}/></button></div>
    <label className="gw-analysis-field">节点大小<select aria-label="重要节点指标" value={metric} onChange={event => onMetric(event.target.value)}>{Object.entries(GRAPH_METRICS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
    <label className="gw-analysis-field">计算范围<select aria-label="重要节点计算范围" value={scope} onChange={event => onScope(event.target.value)}><option value="topic">当前主题</option><option value="all">全部主题</option></select></label>
    <p className="gw-analysis-context">{scope === 'all' ? '全部主题' : topicLabel || '当前主题'}{analytics.data ? ` · ${analytics.data.graph.nodeCount} 个词条 · ${analytics.data.graph.edgeCount} 条语义关系` : ''}</p>
    {analytics.loading && <p className="gw-analysis-state" role="status">正在计算完整范围的重要性…</p>}
    {analytics.error && <p className="gw-error-text" role="alert">{analytics.error}<button onClick={analytics.refresh}>重试</button></p>}
    {metric !== 'none' && !analytics.loading && !analytics.error && analytics.data && <>
      <div className="gw-ranking-list" aria-label={`${GRAPH_METRICS[metric]}排名`}>{ranking.slice(0, 10).map((node, index) => <button key={node.id} onClick={() => onNavigate(node)} aria-label={`查看重要节点：${node.label}`}><span className="gw-rank-number">{node.rank || index + 1}</span><span className="gw-rank-name"><strong>{node.label}</strong>{!visibleIds.has(node.id) && <small>打开词条邻域</small>}</span><span className="gw-rank-value">{metricValue(node.value, metric)}</span><CaretRight size={12}/></button>)}</div>
      {!ranking.length && <p className="gw-analysis-state">当前范围暂无可计算的语义词条。</p>}
      <p className="gw-analysis-note">{metric === 'pagerank' ? '按语义关系方向计算 PageRank。' : metric === 'betweenness' ? `按无向语义网络计算桥梁作用${approximate ? '，当前使用抽样近似' : ''}。` : metric === 'coreNumber' ? '核心层级越高，所在网络结构越紧密。' : '统计不同语义邻居，忽略关系方向。'}来源和导航连接不计分，排名覆盖完整范围。</p>
    </>}
  </div>;
}
