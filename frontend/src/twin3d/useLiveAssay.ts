/**
 * Live counterpart of useAssay(): derives the 3D twin's AssayState from the
 * backend bandage twin's latest `twin_eis` push (useDeviceSocket's
 * lastEis / eisByChannel) instead of stepping a local AssayController.
 *
 * There is deliberately no clock here. The backend's BandageTwin already
 * integrates binding kinetics, fouling and the EIS sweep; re-simulating any
 * of it client-side would let the two drift. This hook only renames the
 * Python wire fields into ./types.ts's camelCase contract and fills the
 * handful of AssayState fields the wire does not carry:
 *
 *  - phase: a live sensor has no discrete assay steps -- it is continuously
 *    "baseline vs now", i.e. permanently at the comparison stage. So 'idle'
 *    until the first reading, then always 'result' (progress 1), which is
 *    also what makes EisPanel draw both spectra and the detection card.
 *  - baselineSpectrum: the wire only sends baseline_params, not a baseline
 *    sweep, so it is recomputed here with eis.ts's noise-free spectrum() on
 *    the live sweep's own frequency grid. eis.ts is parity-tested against
 *    the Python impedance model (tests/test_eis_ts_parity.py), so this is the
 *    curve the backend would have produced minus its measurement noise.
 *  - spectrum rename: Python's `phase_deg` -> ImpedancePoint.phase (both
 *    degrees), the same rename eis.ts's makePoint / backendCheck.ts apply.
 *  - stale: true until the first reading, whenever the socket is down, and
 *    whenever no reading has arrived for STALE_AFTER_MS -- "connected" alone
 *    is not enough because a socket can stay open after the simulation
 *    behind it stops, and a frozen scene is the honest rendering of that.
 *  - live: the sensorgram is the readings themselves, kept in arrival order
 *    against the backend's simulated clock (t_s). Nothing is interpolated.
 *    The wire carries finished sweeps, so there is no sweep-in-progress
 *    frequency to report.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { TwinEisReading, TwinEisSpectrumPoint } from '../types';
import { DEFAULTS, TRACE_MAX, deltaPct, logSweep, readout, spectrum } from './eis';
import { REF_FREQ_HZ } from './types';
import type { AssayState, EisParams, EisSpectrum, ImpedancePoint, TracePoint } from './types';

/** No twin_eis for this long -> stale. The backend ticks about once a
 * second, so a few missed ticks of slack before the scene freezes. */
export const STALE_AFTER_MS = 5000;

const GRID = logSweep();

const fin = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : fallback;

function toParams(p: Partial<EisParams> | undefined, fallback: EisParams): EisParams {
  return {
    rs: fin(p?.rs, fallback.rs),
    rct: fin(p?.rct, fallback.rct),
    cdl: fin(p?.cdl, fallback.cdl),
    sigma: fin(p?.sigma, fallback.sigma),
    n: fin(p?.n, fallback.n),
  };
}

function toPoint(p: TwinEisSpectrumPoint): ImpedancePoint {
  return { freq: p.freq, re: p.re, im: p.im, mag: p.mag, phase: p.phase_deg };
}

/**
 * The backend's sweep, reordered onto eis.ts's default grid. EisPanel's Bode
 * plot indexes the post spectrum positionally against logSweep(), so a
 * spectrum on any other grid (or out of order) would silently plot each
 * magnitude at the wrong frequency. If the wire grid does not cover the
 * default grid, the curve is recomputed from the fitted params instead --
 * losing the backend's measurement noise, but never mis-plotted.
 */
function alignSpectrum(wire: TwinEisSpectrumPoint[], params: EisParams): EisSpectrum {
  const pts = wire.filter((p) => [p.freq, p.re, p.im, p.mag, p.phase_deg].every((v) => Number.isFinite(v)));
  const aligned: ImpedancePoint[] = [];
  for (const f of GRID) {
    const hit = pts.find((p) => p.freq > 0 && Math.abs(Math.log10(p.freq / f)) < 1e-6);
    if (!hit) return spectrum(params, GRID, 0);
    aligned.push(toPoint(hit));
  }
  return aligned;
}

let idleState: AssayState | null = null;
/** Clean-electrode placeholder shown before the first reading, mirroring AssayController's idle frame. */
function makeIdleState(): AssayState {
  if (idleState) return idleState;
  const p = DEFAULTS.params;
  const ro = readout(p);
  idleState = {
    phase: 'idle',
    phaseProgress: 0,
    cfuPerMl: 0,
    coverage: 0,
    baseline: null,
    current: p,
    baselineSpectrum: null,
    currentSpectrum: spectrum(p, GRID, 0),
    zMag: ro.mag,
    zPhase: ro.phase,
    deltaZMagPct: null,
    deltaRctPct: null,
    deltaCdlPct: null,
    detected: null,
    estimatedCfuPerMl: null,
    stale: true,
  };
  return idleState;
}

function deriveState(reading: TwinEisReading): Omit<AssayState, 'stale' | 'live'> {
  const e = reading.eis;
  const baseline = toParams(e.baseline_params, DEFAULTS.params);
  const current = toParams(e.params, baseline);
  const currentSpectrum = alignSpectrum(e.spectrum ?? [], current);

  // z_ref is documented to be at REF_FREQ_HZ; if the backend ever reads out
  // elsewhere, recompute at REF_FREQ_HZ rather than show |Z| under a label
  // (EisPanel, scene HUD) naming the wrong frequency.
  const refMatches = fin(e.z_ref?.freq, NaN) === REF_FREQ_HZ;
  const ro = readout(current, REF_FREQ_HZ);
  const zMag = refMatches ? fin(e.z_ref.mag, ro.mag) : ro.mag;
  const zPhase = refMatches ? fin(e.z_ref.phase_deg, ro.phase) : ro.phase;
  const deltaZMagPct = refMatches && Number.isFinite(e.delta?.z_ref_pct)
    ? e.delta.z_ref_pct
    : deltaPct(ro.mag, readout(baseline, REF_FREQ_HZ).mag);

  const detected = !!e.detection?.detected;
  const est = e.detection?.estimated_cfu;

  return {
    phase: 'result',
    phaseProgress: 1,
    cfuPerMl: Math.max(0, fin(e.cfu_equivalent_per_ml, 0)),
    coverage: Math.min(Math.max(fin(e.theta, 0), 0), 1),
    baseline,
    current,
    baselineSpectrum: null, // filled in by the hook (memoised on baseline params)
    currentSpectrum,
    zMag,
    zPhase,
    deltaZMagPct,
    deltaRctPct: Number.isFinite(e.delta?.rct_pct) ? e.delta.rct_pct : deltaPct(current.rct, baseline.rct),
    deltaCdlPct: Number.isFinite(e.delta?.cdl_pct) ? e.delta.cdl_pct : deltaPct(current.cdl, baseline.cdl),
    detected,
    // types.ts: null when below LOD, so a not-detected reading never shows a number
    estimatedCfuPerMl: detected && typeof est === 'number' && Number.isFinite(est) ? est : null,
  };
}

/**
 * @param reading   latest twin_eis for the channel being shown (null = none yet)
 * @param connected useDeviceSocket's socket state
 */
export function useLiveAssay(reading: TwinEisReading | null, connected: boolean): AssayState {
  // Receipt time, not reading.timestamp: the latter is backend wall-clock
  // and would make staleness depend on client/server clock skew.
  const receivedAt = useRef(0);
  const [timedOut, setTimedOut] = useState(true);

  useEffect(() => {
    if (!reading) {
      setTimedOut(true);
      return;
    }
    receivedAt.current = Date.now();
    setTimedOut(false);
  }, [reading]);

  useEffect(() => {
    const id = window.setInterval(() => {
      const expired = Date.now() - receivedAt.current > STALE_AFTER_MS;
      setTimedOut((prev) => (prev === expired ? prev : expired));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const derived = useMemo(() => (reading ? deriveState(reading) : null), [reading]);

  // The baseline only moves when the backend re-baselines, so key the
  // recomputed curve on its values: EisPanel memoises its charts on
  // spectrum identity and would otherwise rebuild them every tick.
  const b = derived?.baseline;
  const baseKey = b ? `${b.rs}|${b.rct}|${b.cdl}|${b.sigma}|${b.n}` : '';
  const baselineSpectrum = useMemo(
    () => (b ? spectrum(b, GRID, 0) : null),
    [baseKey], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // One sensorgram sample per reading. A different device/channel, or a
  // simulated clock that went backwards (the simulation was restarted),
  // starts a new trace; a repeat of the same instant replaces the last sample.
  const traceRef = useRef<{ key: string; pts: TracePoint[]; count: number }>({ key: '', pts: [], count: 0 });
  const feed = useMemo(() => {
    const tr = traceRef.current;
    if (!reading || !derived) {
      traceRef.current = { key: '', pts: [], count: 0 };
      return traceRef.current;
    }
    const key = `${reading.device_id}|${reading.channel_id}`;
    const t = fin(reading.eis.t_s, tr.pts.length ? tr.pts[tr.pts.length - 1].t : 0);
    const pt: TracePoint = { t, dRctPct: derived.deltaRctPct ?? 0, zMag: derived.zMag, theta: derived.coverage };
    const lastT = tr.pts.length ? tr.pts[tr.pts.length - 1].t : -Infinity;
    if (tr.key !== key || t < lastT) traceRef.current = { key, pts: [pt], count: 1 };
    else if (t === lastT) traceRef.current = { key, pts: [...tr.pts.slice(0, -1), pt], count: tr.count };
    else traceRef.current = { key, pts: [...tr.pts.slice(-(TRACE_MAX - 1)), pt], count: tr.count + 1 };
    return traceRef.current;
  }, [reading, derived]);

  const stale = !connected || !reading || timedOut;
  return useMemo<AssayState>(() => {
    if (!derived || !reading) return makeIdleState();
    return {
      ...derived,
      baselineSpectrum,
      stale,
      live: {
        sweepIndex: Math.max(0, feed.count - 1),
        sweepProgress: 1,
        sweepFreqHz: null,
        labTimeS: fin(reading.eis.t_s, 0),
        monitoring: true,
        trace: feed.pts,
      },
    };
  }, [derived, reading, baselineSpectrum, stale, feed]);
}
