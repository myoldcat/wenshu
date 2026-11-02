import { useEffect, useRef } from 'react';
import { api } from './local-api';

// Each tab owns a short lease; a closed/offline tab cannot block idle work forever.
export function useWritingActivity(editing) {
  const session = useRef(null);
  if (!session.current) session.current = `${Date.now()}-${Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-')}`;
  const sequence = useRef(0);
  useEffect(() => {
    if (!editing) return;
    let timer;
    const report = active => {
      const body = JSON.stringify({ sessionId: session.current, active, sequence: ++sequence.current });
      // Errors are retried by the next heartbeat; the server expires stale leases.
      void api('/ai/writing-activity', { method: 'POST', body, keepalive: true }).catch(() => {});
    };
    const visible = () => {
      clearInterval(timer);
      const active = document.visibilityState === 'visible';
      report(active);
      if (active) timer = setInterval(() => report(true), 20000);
    };
    const leave = () => { clearInterval(timer); report(false); };
    visible();
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('pagehide', leave);
    window.addEventListener('pageshow', visible);
    return () => {
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('pagehide', leave);
      window.removeEventListener('pageshow', visible);
      leave();
    };
  }, [editing]);
}
