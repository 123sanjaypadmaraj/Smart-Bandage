/**
 * Third AssayState source: the backend's emulated EIS analyzer
 * (WS /ws/hardware/eis, backend/app/hardware_eis.py).
 *
 * Like the instrument it imitates, the stream is raw: one frequency point at a
 * time, in assay order, with no interpretation until the closing `result`
 * frame. So this hook only assembles what has been "received" so far:
 *
 *  - spectra: the points of the sweep in progress (a prefix of eis.ts's
 *    logSweep grid, which the dataset shares), and the finished baseline sweep.
 *  - sensor parameters: fitted in the browser (eis.ts fitRandles, a port of
 *    the backend fit) when a sweep completes; replaced by the backend's own
 *    fit when `result` arrives.
 *  - coverage: the stream carries no binding kinetics, only the phase order, so
 *    it steps with the phase (0 -> half in `binding` -> full by `measure2`),
 *    from the concentration the caller asked for.
 *  - detection, dRct/dCdl/dZ: only from the `result` frame.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WS_BASE } from '../api';
import { DEFAULTS, coverage, fitRandles, logSweep, paramsAtCoverage, readout, spectrum } from './eis';
import { REF_FREQ_HZ } from './types';
import type { AssayPhase, AssayState, EisParams, EisSpectrum, ImpedancePoint } from './types';

const GRID = logSweep();

export interface HardwareRequest {
  /** replay a stored dataset run; otherwise synthesise from sampleClass/cfu/seed */
  runId?: string;
  sampleClass: 'target' | 'nonspecific' | 'blank';
  cfu: number;
  seed: number;
  /** wall-clock gap between sweep points, seconds */
  pointIntervalS: number;
}

interface PointFrame {
  type: 'point';
  phase: string;
  seq: number;
  t_s: number;
  freq_hz: number;
  re_ohm: number;
  im_ohm: number;
  mag_ohm: number;
  phase_deg: number;
}
interface PhaseFrame { type: 'phase'; phase: AssayPhase; t_s: number }
interface FitBlock {
  params_fit: { rs: number; rct: number; cdl: number; sigma: number; n: number };
  z_ref: { freq: number; mag: number; phase_deg: number };
}
interface ResultFrame {
  type: 'result';
  t_s: number;
  baseline: FitBlock;
  post: FitBlock;
  delta: { z_ref_pct: number; rct_pct: number; cdl_pct: number };
  detection: { detected: boolean; estimated_cfu: number | null };
  truth: { cfu_per_ml: number; sample_class: string; correct: boolean };
}
type Frame = PointFrame | PhaseFrame | ResultFrame;

export type HardwareLink = 'idle' | 'connecting' | 'streaming' | 'done' | 'error';

export interface HardwareStatus {
  link: HardwareLink;
  error: string | null;
  /** the closing frame's ground truth, for judging the call (null until result) */
  truth: ResultFrame['truth'] | null;
}

const toPoint = (f: PointFrame): ImpedancePoint => ({ freq: f.freq_hz, re: f.re_ohm, im: f.im_ohm, mag: f.mag_ohm, phase: f.phase_deg });
const toParams = (p: FitBlock['params_fit']): EisParams => ({ rs: p.rs, rct: p.rct, cdl: p.cdl, sigma: p.sigma, n: p.n });
const pct = (post: number, base: number) => ((post - base) / base) * 100;

interface Acc {
  phase: AssayPhase;
  baseSweep: EisSpectrum;
  postSweep: EisSpectrum;
  baseline: EisParams | null;
  post: EisParams | null;
  result: ResultFrame | null;
  t: number;
  sweepIndex: number;
  freq: number | null;
}

const fresh = (): Acc => ({ phase: 'idle', baseSweep: [], postSweep: [], baseline: null, post: null, result: null, t: 0, sweepIndex: 0, freq: null });

function toState(a: Acc, cfu: number, stale: boolean): AssayState {
  const theta = coverage(cfu);
  const phaseCov: Partial<Record<AssayPhase, number>> = { binding: 0.5, measure2: 1, compare: 1, result: 1 };
  const cov = theta * (phaseCov[a.phase] ?? 0);
  const baseP = a.baseline ?? DEFAULTS.params;
  const cur = a.result ? a.post! : a.post ?? paramsAtCoverage(baseP, cov);
  const sweeping = a.phase === 'baseline' ? a.baseSweep : a.phase === 'measure2' ? a.postSweep : null;
  const currentSpectrum = sweeping ?? (a.postSweep.length ? a.postSweep : a.baseSweep.length ? a.baseSweep : spectrum(baseP, GRID, 0));
  const ro = readout(cur, REF_FREQ_HZ);
  const r = a.result;
  const sweepLen = sweeping ? sweeping.length : GRID.length;
  return {
    phase: a.phase,
    phaseProgress: sweeping ? sweepLen / GRID.length : a.phase === 'result' ? 1 : 0.5,
    cfuPerMl: cfu,
    coverage: cov,
    baseline: a.baseline,
    current: cur,
    baselineSpectrum: a.baseSweep.length === GRID.length ? a.baseSweep : null,
    currentSpectrum,
    zMag: r ? r.post.z_ref.mag : ro.mag,
    zPhase: r ? r.post.z_ref.phase_deg : ro.phase,
    deltaZMagPct: r ? r.delta.z_ref_pct : null,
    deltaRctPct: r ? r.delta.rct_pct : a.baseline && a.post ? pct(a.post.rct, a.baseline.rct) : null,
    deltaCdlPct: r ? r.delta.cdl_pct : a.baseline && a.post ? pct(a.post.cdl, a.baseline.cdl) : null,
    detected: r ? r.detection.detected : null,
    estimatedCfuPerMl: r && r.detection.detected ? r.detection.estimated_cfu : null,
    stale,
    live: {
      sweepIndex: a.sweepIndex,
      sweepProgress: sweeping ? sweepLen / GRID.length : 1,
      sweepFreqHz: sweeping ? a.freq : null,
      labTimeS: a.t,
      monitoring: false,
      trace: [],
    },
  };
}

export function useHardwareAssay(): { state: AssayState; status: HardwareStatus; start: (req: HardwareRequest) => void; stop: () => void } {
  const acc = useRef<Acc>(fresh());
  const cfuRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const [tick, setTick] = useState(0);
  const [status, setStatus] = useState<HardwareStatus>({ link: 'idle', error: null, truth: null });

  const stop = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onmessage = ws.onclose = ws.onerror = null;
      ws.close();
    }
    setStatus((s) => (s.link === 'streaming' || s.link === 'connecting' ? { ...s, link: 'idle' } : s));
  }, []);

  const start = useCallback((req: HardwareRequest) => {
    stop();
    acc.current = fresh();
    cfuRef.current = req.sampleClass === 'blank' ? 0 : req.cfu;
    setStatus({ link: 'connecting', error: null, truth: null });
    setTick((n) => n + 1);
    const q = new URLSearchParams(req.runId
      ? { run_id: req.runId, point_interval_s: String(req.pointIntervalS) }
      : { sample_class: req.sampleClass, cfu: String(req.cfu), seed: String(req.seed), point_interval_s: String(req.pointIntervalS) });
    const ws = new WebSocket(`${WS_BASE}/ws/hardware/eis?${q}`);
    wsRef.current = ws;
    ws.onopen = () => setStatus((s) => ({ ...s, link: 'streaming' }));
    ws.onerror = () => setStatus({ link: 'error', error: 'Could not reach the emulated analyzer (is the backend running?)', truth: null });
    ws.onclose = (e) => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      setStatus((s) => (s.link === 'error' ? s : e.code === 4404 ? { link: 'error', error: e.reason || 'unknown run', truth: null } : { ...s, link: acc.current.result ? 'done' : 'idle' }));
    };
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data) as Frame;
      const a = acc.current;
      a.t = f.t_s;
      if (f.type === 'phase') {
        a.phase = f.phase;
        if (f.phase === 'baseline' || f.phase === 'measure2') a.sweepIndex = f.phase === 'baseline' ? 0 : 1;
      } else if (f.type === 'point') {
        a.freq = f.freq_hz;
        const sweep = f.phase === 'baseline' ? a.baseSweep : a.postSweep;
        sweep.push(toPoint(f));
        if (sweep.length === GRID.length) {
          const fit = fitRandles(sweep).params;
          if (f.phase === 'baseline') a.baseline = fit;
          else a.post = fit;
        }
        // a new array each time: EisPanel memoises its charts on spectrum identity
        if (f.phase === 'baseline') a.baseSweep = [...a.baseSweep];
        else a.postSweep = [...a.postSweep];
      } else {
        a.phase = 'result';
        a.result = f;
        a.baseline = toParams(f.baseline.params_fit);
        a.post = toParams(f.post.params_fit);
        setStatus((s) => ({ ...s, truth: f.truth }));
      }
      setTick((n) => n + 1);
    };
  }, [stop]);

  useEffect(() => stop, [stop]);

  const stale = status.link !== 'streaming' && status.link !== 'done';
  const state = useMemo(() => toState(acc.current, cfuRef.current, stale), [tick, stale]); // eslint-disable-line react-hooks/exhaustive-deps
  return { state, status, start, stop };
}
