import { automaticFinished, automaticResultLabel } from './writing-automatic.js';

export const OPS_PAGE_SIZE = 30;

export function pagedEndpoint(endpoint, limit, offset) {
  return `${endpoint}${endpoint.includes('?') ? '&' : '?'}limit=${limit}&offset=${offset}`;
}

export function projectState(value) { return ['all', 'active', 'deleted'].includes(value) ? value : 'all'; }

export function listPage(data, expectedOffset = 0) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const offset = Number.isFinite(data?.offset) ? data.offset : expectedOffset;
  const total = Number.isFinite(data?.total) ? data.total : offset + items.length;
  return { items, total, offset, hasMore: typeof data?.hasMore === 'boolean' ? data.hasMore : offset + items.length < total };
}

// Retries and page overlap must never create duplicate rows or reorder rows
// already being inspected. A refreshed server row replaces its previous value.
export function mergePage(previous, incoming) {
  const positions = new Map(previous.map((item, index) => [item.id, index]));
  const next = [...previous];
  for (const item of incoming) {
    if (positions.has(item.id)) next[positions.get(item.id)] = item;
    else { positions.set(item.id, next.length); next.push(item); }
  }
  return next;
}

export function readableValue(value) {
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed !== null && typeof parsed === 'object') return { structured: parsed, raw: value };
    } catch { /* Ordinary prompt prose remains verbatim. */ }
    return { structured: null, raw: value };
  }
  return { structured: value, raw: value === undefined ? '' : JSON.stringify(value, null, 2) };
}

export function serializedRecord(value) { return JSON.stringify(value, null, 2); }
export function operationKind(value) {
  return ({ automatic: '全自动写作', outline: '生成纲要', section: '撰写章节', edit: '修改选中文本', document: '全文写作', citation_match: '引用核对', materials: '材料推荐', images: '图片检索' })[value] || value || '写作';
}
export function operationStatus(value, context) {
  if (value === 'succeeded' && context === 'execution') return '执行已结束';
  if (value === 'succeeded' && context === 'transport') return '已收到返回';
  return ({ queued: '等待中', pending: '等待中', running: '进行中', in_progress: '进行中', succeeded: '成功', completed: '已完成', failed: '失败', cancelled: '已取消', canceled: '已取消', interrupted: '已中断', negotiated: '参数协商', valid: '校验通过', invalid: '校验未通过', discarded: '未采用', unavailable: '未记录', paused: '已暂停' })[value] || value || '未记录';
}
export function operationBodyResult(run) {
  if (run?.kind !== 'automatic' || !automaticFinished(run.automatic)) return '';
  return automaticResultLabel(run.automatic);
}
export function timeLabel(value) {
  if (!value) return '未记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date);
}
export function durationLabel(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value / 1000).toFixed(value < 1000 ? 3 : 1)} 秒` : '未记录';
}
