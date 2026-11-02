import { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import './writing-page-transition.css';

const TRANSITION_CLASS = 'writing-page-transition';

export function useWritingPageStage({ nav, demoMode, initialStage = 'projects' }) {
  const [stage, setStageState] = useState(initialStage);
  const navRef = useRef(nav), demoRef = useRef(demoMode), renderedStage = useRef(stage), intendedStage = useRef(stage);
  const mounted = useRef(true), epoch = useRef(0), pendingUpdate = useRef(null), activeTransition = useRef(null), finishedPromise = useRef(null);
  navRef.current = nav;
  demoRef.current = demoMode;
  renderedStage.current = stage;
  if (!pendingUpdate.current) intendedStage.current = stage;

  const clearScope = useCallback(() => {
    if (typeof document !== 'undefined') document.documentElement.classList.remove(TRANSITION_CLASS);
  }, []);

  const cancelStageTransition = useCallback(() => {
    epoch.current += 1;
    pendingUpdate.current = null;
    intendedStage.current = renderedStage.current;
    const transition = activeTransition.current?.transition;
    activeTransition.current = null;
    finishedPromise.current = null;
    try { transition?.skipTransition?.(); } catch { /* The native transition may already have finished. */ }
    clearScope();
  }, [clearScope]);

  const setStage = useCallback(change => {
    const previous = intendedStage.current;
    const next = typeof change === 'function' ? change(previous) : change;
    if (Object.is(previous, next)) return;
    cancelStageTransition();
    intendedStage.current = next;
    const reduceMotion = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const animate = navRef.current === 'writing' && renderedStage.current === 'projects' && next !== 'projects'
      && !demoRef.current && !reduceMotion && typeof document !== 'undefined' && typeof document.startViewTransition === 'function';
    if (!animate) { setStageState(next); return; }

    const token = epoch.current;
    pendingUpdate.current = { token, next };
    document.documentElement.classList.add(TRANSITION_CLASS);
    const finish = () => {
      if (activeTransition.current?.token !== token) return;
      activeTransition.current = null;
      finishedPromise.current = null;
      pendingUpdate.current = null;
      intendedStage.current = renderedStage.current;
      clearScope();
    };
    try {
      const transition = document.startViewTransition(() => {
        // skipTransition only stops the visual; an old update callback still needs this guard.
        if (!mounted.current || token !== epoch.current || navRef.current !== 'writing' || demoRef.current) return;
        pendingUpdate.current = null;
        flushSync(() => setStageState(next));
      });
      activeTransition.current = { token, transition };
      transition.ready?.catch(() => {});
      transition.updateCallbackDone?.catch(() => {});
      finishedPromise.current = Promise.resolve(transition.finished);
      finishedPromise.current.then(finish, finish);
    } catch {
      if (token !== epoch.current || !mounted.current) return;
      activeTransition.current = null;
      finishedPromise.current = null;
      pendingUpdate.current = null;
      clearScope();
      setStageState(next);
    }
  }, [cancelStageTransition, clearScope]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancelStageTransition(); };
  }, [cancelStageTransition]);
  useEffect(() => {
    if (nav !== 'writing' || demoMode) cancelStageTransition();
  }, [nav, demoMode, cancelStageTransition]);

  return [stage, setStage, cancelStageTransition];
}

export default useWritingPageStage;
