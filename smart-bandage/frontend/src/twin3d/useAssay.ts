import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AssayController } from './eis';
import type { AssayPhase, AssayState } from './types';

export interface AssayControls {
  /** start (or restart) the assay; optional CFU/mL overrides the current sample concentration */
  run: (cfu?: number) => void;
  reset: () => void;
  setCfu: (cfu: number) => void;
  pause: () => void;
  resume: () => void;
  skipTo: (phase: AssayPhase) => void;
  speed: number;
  setSpeed: (s: number) => void;
  /** auto-restart the sequence a few seconds after it finishes */
  auto: boolean;
  setAuto: (a: boolean) => void;
  paused: boolean;
  running: boolean;
  finished: boolean;
}

const AUTO_RESTART_DELAY_S = 4;

/** Drives an AssayController from requestAnimationFrame; ticking stops while the tab is hidden. */
export function useAssay(): { state: AssayState; controls: AssayControls } {
  const ctrlRef = useRef<AssayController | null>(null);
  if (ctrlRef.current === null) ctrlRef.current = new AssayController();
  const ctrl = ctrlRef.current;

  const [state, setState] = useState<AssayState>(() => ctrl.getState());
  const [speed, setSpeedState] = useState(1);
  const [auto, setAutoState] = useState(false);
  const [flags, setFlags] = useState({ paused: false, running: false, finished: false });

  const speedRef = useRef(1);
  const autoRef = useRef(false);
  const dirty = useRef(false);
  const finishedFor = useRef(0);

  const sync = useCallback(() => {
    setState(ctrl.getState());
    setFlags((f) => {
      const n = { paused: ctrl.isPaused, running: ctrl.isRunning, finished: ctrl.finished };
      return f.paused === n.paused && f.running === n.running && f.finished === n.finished ? f : n;
    });
  }, [ctrl]);

  useEffect(() => {
    let raf = 0;
    let last: number | null = null;
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = last === null ? 0 : Math.min((now - last) / 1000, 0.1);
      last = now;
      const wasRunning = ctrl.isRunning;
      if (wasRunning) ctrl.tick(dt * speedRef.current);
      if (autoRef.current && ctrl.finished) {
        finishedFor.current += dt;
        if (finishedFor.current > AUTO_RESTART_DELAY_S) {
          finishedFor.current = 0;
          ctrl.start();
          dirty.current = true;
        }
      } else {
        finishedFor.current = 0;
      }
      if (wasRunning || dirty.current) {
        dirty.current = false;
        sync();
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(raf);
        raf = 0;
      } else if (raf === 0) {
        last = null;
        raf = requestAnimationFrame(frame);
      }
    };
    if (!document.hidden) raf = requestAnimationFrame(frame);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [ctrl, sync]);

  const controls = useMemo<AssayControls>(
    () => ({
      run: (cfu) => { ctrl.start(cfu); sync(); },
      reset: () => { ctrl.reset(); sync(); },
      setCfu: (cfu) => { ctrl.setCfu(cfu); sync(); },
      pause: () => { ctrl.pause(); sync(); },
      resume: () => { ctrl.resume(); sync(); },
      skipTo: (p) => { ctrl.skipTo(p); ctrl.resume(); sync(); },
      speed,
      setSpeed: (s) => { speedRef.current = s; setSpeedState(s); },
      auto,
      setAuto: (a) => { autoRef.current = a; setAutoState(a); },
      ...flags,
    }),
    [ctrl, sync, speed, auto, flags],
  );

  return { state, controls };
}
