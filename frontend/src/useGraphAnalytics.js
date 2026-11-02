import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectionMessage } from './local-api';
import { wikiRoot } from './wiki-unified-state';

// An analysis belongs to the complete semantic snapshot, never the loaded page
// of a neighborhood. Key fencing also covers servers which ignore AbortSignal.
export function useGraphAnalytics({ mode, topicId, snapshotId, scope, active, onConflict }) {
  const [state, setState] = useState({ key: '', data: null, loading: false, error: '' });
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0), conflictRef = useRef(onConflict);
  conflictRef.current = onConflict;
  const params = new URLSearchParams({ scope });
  if (scope === 'topic' && topicId) params.set('topic_id', topicId);
  if (snapshotId) params.set('snapshot_id', snapshotId);
  const path = snapshotId && (scope === 'all' || topicId) ? `${wikiRoot(mode)}/analytics?${params}` : '';
  const key = path ? `${path}|${revision}` : '';
  const currentKey = useRef(key); currentKey.current = key;
  useEffect(() => {
    if (!active || !key) return;
    const request = ++sequence.current, controller = new AbortController();
    const valid = () => !controller.signal.aborted && sequence.current === request && currentKey.current === key;
    setState(previous => ({ key, data: previous.key === key ? previous.data : null, loading: true, error: '' }));
    api(path, { signal: controller.signal }).then(data => {
      if (!valid()) return;
      if (data.snapshotId !== snapshotId || data.mode !== mode) {
        setState({ key, data: null, loading: false, error: '图谱版本已变化，请刷新后重新计算。' });
        conflictRef.current?.();
        return;
      }
      setState({ key, data, loading: false, error: '' });
    }).catch(error => {
      if (!valid() || error.name === 'AbortError') return;
      setState({ key, data: null, loading: false, error: error.status === 409 ? '图谱已更新，正在刷新；排名不会混用旧版本。' : connectionMessage(error) });
      if (error.status === 409) conflictRef.current?.();
    });
    return () => { sequence.current += 1; controller.abort(); };
  }, [active, key, path, snapshotId, mode]);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  return { ...(state.key === key && key ? state : { data: null, loading: active && !!key, error: '' }), refresh };
}
