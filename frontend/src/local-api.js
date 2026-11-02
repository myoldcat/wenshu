export async function api(path, { signal, ...options } = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    signal,
    headers: { 'X-Wenshu-Client': 'local', ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers },
  });
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    const detail = data?.detail || data?.error;
    const error = new Error(typeof detail === 'string' ? detail : '文枢暂时无法完成这项操作，请稍后重试。');
    error.status = response.status;
    error.detail = detail;
    if (typeof detail?.message === 'string') error.message = detail.message;
    throw error;
  }
  if (!data) throw new Error('暂时未能读取内容，请稍后重试。');
  return data;
}

export const normalizeDocument = doc => ({
  ...doc,
  tags: Array.isArray(doc.tags) ? doc.tags : [],
  classificationPaths: Array.isArray(doc.classificationPaths) ? doc.classificationPaths : [],
  articleType: ['fact', 'opinion', 'technical'].includes(doc.articleType) ? doc.articleType : null,
  articleTypeManual: doc.articleTypeManual === true,
  concepts: Array.isArray(doc.concepts) ? doc.concepts : [],
  kind: doc.kind === 'article' ? '文档' : (doc.kind || '文档'),
  summary: doc.summary || '',
  source: doc.source || '本地导入',
  date: doc.date || doc.createdAt?.slice(0, 10) || '',
});

export const connectionMessage = error => error?.message === 'Failed to fetch'
  ? '暂时无法连接文枢，请稍后重试。'
  : error?.message || '文枢暂不可用，请稍后重试。';
