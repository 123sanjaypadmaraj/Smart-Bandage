import { useEffect, useMemo, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import {
  CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import { InfoHint } from '../components/InfoHint';
import type { AssayControls } from './useAssay';
import {
  CALIBRATION, DEFAULTS, calibratedDeltaRctPct, coverage, deltaPct, effectiveLodCfu, impedance, logSweep, readout,
} from './eis';
import { REFERENCE_LABEL, REFERENCE_SOURCES, REFERENCE_SPECTRUM } from './referenceData';
import { ASSAY_PHASES, PHASE_LABEL, REF_FREQ_HZ } from './types';
import type { AssayPhase, AssayState, EisParams, EisSpectrum, PartKey } from './types';

export interface EisPanelProps {
  state: AssayState;
  /** null on the Live path (useLiveAssay): the backend owns the assay, so the
   * manual stepper, run/pause and sample-concentration inputs would only
   * pretend to control it -- the workflow card is omitted instead. */
  controls: AssayControls | null;
  selected: PartKey | null;
  onSelect: (p: PartKey | null) => void;
}

/* ------------------------------------------------------------------ helpers */

const CARD = 'rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl';
const H = 'flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400';
const FOCUS = 'outline-none focus-visible:ring-2 focus-visible:ring-amber-500/70 focus-visible:ring-offset-1 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-950';
const BTN = `rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-slate-200 dark:hover:bg-white/[0.06] ${FOCUS}`;
const BTN_PRIMARY = `rounded-md bg-gradient-to-r from-amber-400 to-amber-500 px-3 py-1.5 text-xs font-semibold text-[#0a0a0d] transition hover:brightness-105 disabled:opacity-40 ${FOCUS}`;
const BTN_ON = 'border-amber-500/70 bg-amber-400/15 text-amber-800 dark:border-amber-400/60 dark:text-amber-200';

const SUPERSCRIPT: Record<string, string> = { '-': '⁻', '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹' };

function sci(v: number): string {
  if (v <= 0) return '0';
  if (v < 1000) return v < 10 ? v.toFixed(1) : Math.round(v).toString();
  const e = Math.floor(Math.log10(v));
  const m = v / Math.pow(10, e);
  return `${m.toFixed(1)}×10${String(e).split('').map((c) => SUPERSCRIPT[c] ?? c).join('')}`;
}

function fmtHz(f: number): string {
  if (f >= 1000) return `${f / 1000}k`;
  return String(f);
}

function fmtNum(v: number, digits = 3): string {
  if (!Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  return v.toFixed(Math.max(0, digits - 1));
}

function fmtPct(v: number | null, digits = 1): string {
  if (v === null) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)} %`;
}

function arrow(v: number | null): string {
  if (v === null) return '';
  if (Math.abs(v) < 0.05) return '–';
  return v > 0 ? '▲' : '▼';
}

function useIsDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
  useEffect(() => {
    const el = document.documentElement;
    const update = () => setDark(el.classList.contains('dark'));
    update();
    const mo = new MutationObserver(update);
    mo.observe(el, { attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, []);
  return dark;
}

interface Palette { base: string; post: string; ref: string; axis: string; grid: string; thr: string; tipBg: string; tipBorder: string }
function palette(dark: boolean): Palette {
  return {
    base: dark ? '#818cf8' : '#4338ca',
    post: dark ? '#fb923c' : '#c2410c',
    ref: dark ? '#2dd4bf' : '#0f766e',
    axis: dark ? '#a1a1aa' : '#52525b',
    grid: 'rgba(113,113,122,0.2)',
    thr: dark ? '#fda4af' : '#be123c',
    tipBg: dark ? 'rgba(10,10,12,0.94)' : 'rgba(255,255,255,0.97)',
    tipBorder: dark ? '1px solid rgba(161,161,170,0.2)' : '1px solid rgba(113,113,122,0.25)',
  };
}

function tooltipStyle(c: Palette) {
  return { background: c.tipBg, border: c.tipBorder, borderRadius: 8, fontSize: 11, color: c.axis } as const;
}

function niceCeil(v: number): number {
  const steps = [100, 200, 250, 500, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000, 10000, 15000, 20000];
  return steps.find((s) => s >= v) ?? Math.ceil(v / 5000) * 5000;
}

function phaseIndex(p: AssayPhase): number {
  return ASSAY_PHASES.indexOf(p);
}

function Legend({ items }: { items: { label: string; color: string; dashed?: boolean; shape: 'circle' | 'diamond' | 'line' }[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600 dark:text-slate-400">
      {items.map((it) => (
        <li key={it.label} className="flex items-center gap-1.5">
          <svg width="26" height="10" aria-hidden="true">
            <line x1="1" y1="5" x2="25" y2="5" stroke={it.color} strokeWidth="2" strokeDasharray={it.dashed ? '5 3' : undefined} />
            {it.shape === 'circle' && <circle cx="13" cy="5" r="3" fill={it.color} />}
            {it.shape === 'diamond' && <path d="M13 1.5 L16.5 5 L13 8.5 L9.5 5 Z" fill={it.color} />}
          </svg>
          {it.label}
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------- workflow + inputs */

function Workflow({ state, controls }: { state: AssayState; controls: AssayControls }) {
  const cur = phaseIndex(state.phase);
  const { cfuPerMl } = state;
  const logC = cfuPerMl > 0 ? Math.min(8, Math.max(1, Math.log10(cfuPerMl))) : 1;
  const idle = state.phase === 'idle';

  return (
    <section className={CARD} aria-label="Assay workflow">
      <h2 className={H}>
        Assay workflow
        <InfoHint align="left" text="The seven steps of an impedance-based bacterial assay. Click a step to jump to it." />
      </h2>

      <ol className="mt-3 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-1 @2xl:grid-cols-2">
        {ASSAY_PHASES.map((p, i) => {
          const done = i < cur;
          const active = i === cur;
          const fill = done ? 1 : active ? state.phaseProgress : 0;
          return (
            <li key={p}>
              <button
                type="button"
                onClick={() => controls.skipTo(p)}
                aria-current={active ? 'step' : undefined}
                className={`relative flex w-full items-center gap-2 overflow-hidden rounded-lg border px-2 py-1.5 text-left text-xs transition ${FOCUS} ${
                  active
                    ? 'border-amber-500/70 bg-amber-400/10 font-semibold text-slate-900 dark:border-amber-400/60 dark:text-white'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/[0.04]'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold ${
                    done ? 'border-emerald-600 bg-emerald-600 text-white dark:border-emerald-400 dark:bg-emerald-400 dark:text-slate-950'
                      : active ? 'border-amber-600 text-amber-700 dark:border-amber-300 dark:text-amber-300' : 'border-slate-300 text-slate-500 dark:border-white/20'
                  }`}
                >
                  {done ? '✓' : i}
                </span>
                <span className="min-w-0 flex-1 leading-tight">
                  {PHASE_LABEL[p]}
                  <span className="sr-only">{done ? ' (complete)' : active ? ' (current)' : ' (pending)'}</span>
                </span>
                <span
                  aria-hidden="true"
                  className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-500 dark:bg-amber-400"
                  style={{ transform: `scaleX(${fill})`, transformOrigin: 'left' }}
                />
              </button>
            </li>
          );
        })}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className={BTN_PRIMARY} onClick={() => controls.run()}>
          {idle ? '▶ Run assay' : '↻ Restart'}
        </button>
        {controls.paused ? (
          <button type="button" className={BTN} onClick={controls.resume} disabled={idle}>Resume</button>
        ) : (
          <button type="button" className={BTN} onClick={controls.pause} disabled={idle}>Pause</button>
        )}
        <button type="button" className={BTN} onClick={controls.reset} disabled={idle}>Reset</button>
        <div role="group" aria-label="Simulation speed" className="ml-auto flex items-center gap-1 text-xs text-slate-500">
          Speed
          {[0.5, 1, 2, 4].map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={controls.speed === s}
              onClick={() => controls.setSpeed(s)}
              className={`${BTN} px-2 ${controls.speed === s ? BTN_ON : ''}`}
            >
              {controls.speed === s ? '● ' : ''}{s}×
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={controls.auto} onChange={(e) => controls.setAuto(e.target.checked)} className="accent-amber-500" />
          Loop
        </label>
      </div>

      <div className="mt-3 border-t border-slate-200 pt-3 dark:border-white/10">
        <div role="group" aria-label="Sample type" className="flex flex-wrap gap-2">
          <button type="button" aria-pressed={cfuPerMl === 0} onClick={() => controls.setCfu(0)} className={`${BTN} ${cfuPerMl === 0 ? BTN_ON : ''}`}>
            {cfuPerMl === 0 ? '● ' : ''}Clean control (0 CFU/mL)
          </button>
          <button
            type="button"
            aria-pressed={cfuPerMl > 0}
            onClick={() => controls.setCfu(cfuPerMl > 0 ? cfuPerMl : 1e5)}
            className={`${BTN} ${cfuPerMl > 0 ? BTN_ON : ''}`}
          >
            {cfuPerMl > 0 ? '● ' : ''}E. coli sample
          </button>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <label htmlFor="cfu-slider" className="shrink-0 text-xs font-medium text-slate-600 dark:text-slate-300">E. coli</label>
          <input
            id="cfu-slider"
            type="range"
            min={1}
            max={8}
            step={0.05}
            value={logC}
            onChange={(e) => controls.setCfu(Math.pow(10, Number(e.target.value)))}
            aria-valuetext={cfuPerMl > 0 ? `${sci(cfuPerMl)} CFU per millilitre` : '0 CFU per millilitre, clean control'}
            className={`min-w-0 flex-1 accent-amber-500 ${FOCUS} ${cfuPerMl === 0 ? 'opacity-50' : ''}`}
          />
          <output htmlFor="cfu-slider" className="w-28 shrink-0 text-right font-mono text-xs text-slate-800 dark:text-slate-100">
            {cfuPerMl > 0 ? sci(cfuPerMl) : '0'} <span className="text-slate-500">CFU/mL</span>
          </output>
        </div>
        <div className="mt-0.5 flex justify-between pl-14 pr-28 font-mono text-[10px] text-slate-400" aria-hidden="true">
          <span>10¹</span><span>10⁴</span><span>10⁸</span>
        </div>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------- charts */

interface ChartData {
  nyqBase: { x: number; y: number }[];
  nyqPost: { x: number; y: number }[];
  bode: { f: number; magBase: number | null; magPost: number | null; phBase: number | null; phPost: number | null; magRef: number | null; phRef: number | null }[];
  span: number;
  magDomain: [number, number];
  magTicks: number[];
  hasBase: boolean;
  hasPost: boolean;
}

function buildChartData(state: AssayState, showRef: boolean): ChartData {
  const idx = phaseIndex(state.phase);
  let baseSpec: EisSpectrum | null = state.baselineSpectrum;
  // during the baseline measurement the sweep is revealed high -> low frequency
  if (!baseSpec && state.phase === 'baseline') {
    const n = state.currentSpectrum.length;
    const k = Math.max(1, Math.ceil(state.phaseProgress * n));
    baseSpec = state.currentSpectrum.slice(n - k);
  }
  const postSpec: EisSpectrum | null = idx >= phaseIndex('binding') ? state.currentSpectrum : null;

  const nyq = (s: EisSpectrum | null) => (s ? s.map((p) => ({ x: p.re, y: -p.im })) : []);
  const freqs = logSweep();
  const bode = freqs.map((f, i) => {
    const b = baseSpec ? baseSpec.find((p) => Math.abs(Math.log10(p.freq / f)) < 1e-6) : undefined;
    const q = postSpec ? postSpec[i] : undefined;
    // the published spectrum is stored on this same grid (as far up as it goes)
    const r = showRef ? REFERENCE_SPECTRUM[i] : undefined;
    return { f, magBase: b ? b.mag : null, magPost: q ? q.mag : null, phBase: b ? b.phase : null, phPost: q ? q.phase : null, magRef: r ? r.mag : null, phRef: r ? r.phase : null };
  });

  const all = [...(baseSpec ?? []), ...(postSpec ?? [])];
  const maxRe = all.reduce((m, p) => Math.max(m, p.re), 0);
  const maxIm = all.reduce((m, p) => Math.max(m, -p.im), 0);
  const span = niceCeil(Math.max(maxRe, maxIm, 500) * 1.05);
  const mags = [...all, ...(showRef ? REFERENCE_SPECTRUM : [])].map((p) => p.mag);
  const lo = mags.length ? Math.pow(10, Math.floor(Math.log10(Math.min(...mags) * 0.95))) : 100;
  const hi = mags.length ? Math.pow(10, Math.ceil(Math.log10(Math.max(...mags) * 1.02))) : 10000;
  const magTicks: number[] = [];
  for (let v = lo; v <= hi * 1.001; v *= 10) magTicks.push(v);
  return { nyqBase: nyq(baseSpec), nyqPost: nyq(postSpec), bode, span, magDomain: [lo, hi], magTicks, hasBase: !!baseSpec, hasPost: !!postSpec };
}

function Charts({ state, dark, showRef }: { state: AssayState; dark: boolean; showRef: boolean }) {
  const c = palette(dark);
  const d = useMemo(() => buildChartData(state, showRef), [state.baselineSpectrum, state.currentSpectrum, state.phase, state.phaseProgress, showRef]); // eslint-disable-line react-hooks/exhaustive-deps
  const tick = { fontSize: 10, fill: c.axis };
  const label = (value: string, angle?: number) => ({ value, fill: c.axis, fontSize: 10, angle, position: angle ? ('insideLeft' as const) : ('insideBottom' as const), offset: angle ? 10 : -2 });
  const empty = !d.hasBase && !d.hasPost && !showRef;
  const fRefTicks = [0.1, 1, 10, 100, 1e3, 1e4, 1e5];
  const refBase = state.baseline ? impedance(REF_FREQ_HZ, state.baseline) : null;
  const refPost = phaseIndex(state.phase) >= phaseIndex('binding') ? impedance(REF_FREQ_HZ, state.current) : null;
  // the point the analyser is measuring right now (sweeps run high -> low frequency)
  const sweepF = state.stale ? null : state.live?.sweepFreqHz ?? null;
  const sweepPt = sweepF === null ? undefined : state.currentSpectrum.find((p) => Math.abs(Math.log10(p.freq / sweepF)) < 1e-6);
  const sweepColor = state.phase === 'baseline' ? c.base : c.post;

  const legend = (
    <Legend
      items={[
        { label: 'Baseline (clean electrode)', color: c.base, shape: 'circle' },
        { label: 'Post-exposure', color: c.post, shape: 'diamond', dashed: true },
        { label: `● ${REF_FREQ_HZ} Hz readout`, color: c.axis, shape: 'line' },
        ...(showRef ? [{ label: `${REFERENCE_LABEL}, Bode only`, color: c.ref, shape: 'line' as const }] : []),
      ]}
    />
  );

  return (
    <section className={CARD} aria-label="Impedance spectra">
      <h2 className={H}>
        Impedance spectra
        <InfoHint align="left" text="A ~10 mV AC signal is swept from 0.1 Hz to 100 kHz. Nyquist plots Z′ against −Z″; Bode plots |Z| and phase against frequency." />
      </h2>
      {empty ? (
        <div className="mt-3 flex h-56 items-center justify-center rounded-lg border border-dashed border-slate-200 px-4 text-center text-sm text-slate-500 dark:border-white/10">
          Press “Run assay” to measure the clean-electrode baseline.
        </div>
      ) : (
        <div className="mt-2 grid gap-4 @2xl:grid-cols-2">
          <figure className="min-w-0" aria-label="Nyquist plot">
            <figcaption className="mb-1 text-[11px] font-medium text-slate-600 dark:text-slate-300">Nyquist (equal axis scale)</figcaption>
            <div role="img" aria-label="Nyquist plot of Z prime versus negative Z double prime, baseline and post-exposure">
              <ResponsiveContainer width="100%" aspect={1} maxHeight={340}>
                <ScatterChart margin={{ top: 8, right: 12, bottom: 18, left: 4 }}>
                  <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
                  <XAxis type="number" dataKey="x" name="Z′" domain={[0, d.span]} allowDataOverflow tick={tick} label={label('Z′ (Ω)')} tickFormatter={(v: number) => String(Math.round(v))} />
                  <YAxis type="number" dataKey="y" name="−Z″" domain={[0, d.span]} allowDataOverflow tick={tick} width={44} label={label('−Z″ (Ω)', -90)} tickFormatter={(v: number) => String(Math.round(v))} />
                  <Tooltip
                    cursor={{ strokeDasharray: '3 3' }}
                    contentStyle={tooltipStyle(c)}
                    formatter={(v) => (typeof v === 'number' ? `${v.toFixed(0)} Ω` : String(v))}
                  />
                  <Scatter name="Baseline" data={d.nyqBase} fill={c.base} shape="circle" line={{ stroke: c.base, strokeWidth: 2 }} lineType="joint" isAnimationActive={false} />
                  <Scatter name="Post-exposure" data={d.nyqPost} fill={c.post} shape="diamond" line={{ stroke: c.post, strokeWidth: 2, strokeDasharray: '6 3' }} lineType="joint" isAnimationActive={false} />
                  {refBase && <ReferenceDot x={refBase.re} y={-refBase.im} r={6} fill="none" stroke={c.base} strokeWidth={2.5} ifOverflow="hidden" />}
                  {refPost && <ReferenceDot x={refPost.re} y={-refPost.im} r={6} fill="none" stroke={c.post} strokeWidth={2.5} ifOverflow="hidden" />}
                  {sweepPt && <ReferenceDot x={sweepPt.re} y={-sweepPt.im} r={4.5} fill={sweepColor} stroke={dark ? '#0a0a0d' : '#ffffff'} strokeWidth={1.5} ifOverflow="hidden" />}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </figure>

          <div className="min-w-0 space-y-3">
            {(['mag', 'phase'] as const).map((kind) => (
              <figure key={kind} className="min-w-0" aria-label={kind === 'mag' ? 'Bode magnitude plot' : 'Bode phase plot'}>
                <figcaption className="mb-1 text-[11px] font-medium text-slate-600 dark:text-slate-300">
                  {kind === 'mag' ? 'Bode: |Z| (Ω)' : 'Bode: phase (°)'}
                </figcaption>
                <div role="img" aria-label={kind === 'mag' ? 'Impedance magnitude versus frequency' : 'Phase angle versus frequency'}>
                  <ResponsiveContainer width="100%" height={150}>
                    <LineChart data={d.bode} margin={{ top: 6, right: 12, bottom: 14, left: 4 }}>
                      <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
                      <XAxis type="number" dataKey="f" scale="log" domain={[0.1, 1e5]} ticks={fRefTicks} tickFormatter={fmtHz} tick={tick} label={label('Frequency (Hz)')} allowDataOverflow />
                      {kind === 'mag' ? (
                        <YAxis scale="log" domain={d.magDomain} ticks={d.magTicks} tick={tick} width={44} allowDataOverflow tickFormatter={(v: number) => (v >= 1000 ? `${v / 1000}k` : String(v))} />
                      ) : (
                        <YAxis domain={[-90, 0]} ticks={[-90, -60, -30, 0]} tick={tick} width={44} allowDataOverflow />
                      )}
                      <Tooltip
                        contentStyle={tooltipStyle(c)}
                        labelFormatter={(f) => `${typeof f === 'number' ? f.toPrecision(3) : f} Hz`}
                        formatter={(v) => (typeof v === 'number' ? v.toFixed(kind === 'mag' ? 0 : 1) : String(v))}
                      />
                      <ReferenceLine x={REF_FREQ_HZ} stroke={c.axis} strokeDasharray="2 3" label={{ value: '100 Hz', fill: c.axis, fontSize: 9, position: 'insideTopRight' }} />
                      {sweepF !== null && <ReferenceLine x={sweepF} stroke={sweepColor} strokeWidth={1.5} strokeOpacity={0.7} />}
                      {showRef && <Line type="monotone" dataKey={kind === 'mag' ? 'magRef' : 'phRef'} name="Published" stroke={c.ref} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />}
                      <Line type="monotone" dataKey={kind === 'mag' ? 'magBase' : 'phBase'} name="Baseline" stroke={c.base} strokeWidth={2} dot={{ r: 1.5, fill: c.base }} isAnimationActive={false} connectNulls={false} />
                      <Line type="monotone" dataKey={kind === 'mag' ? 'magPost' : 'phPost'} name="Post-exposure" stroke={c.post} strokeWidth={2} strokeDasharray="6 3" dot={false} isAnimationActive={false} connectNulls={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </figure>
            ))}
          </div>
        </div>
      )}
      {legend}
    </section>
  );
}

/* ------------------------------------------------------------ parameter table */

interface Row {
  key: PartKey;
  name: string;
  unit: string;
  get: (p: EisParams) => number;
  fmt: (v: number) => string;
  why: string;
  hint: string;
  nominal: string;
}

const ROWS: Row[] = [
  { key: 'rs', name: 'Rs', unit: 'Ω', get: (p) => p.rs, fmt: (v) => fmtNum(v), hint: 'Solution (electrolyte) resistance between the working and counter electrodes — the high-frequency x-intercept of the Nyquist semicircle.',
    why: 'Barely moves: the bulk electrolyte composition is unchanged by binding, so Rs acts as an internal control (typically tens to a few hundred Ω for a small-volume bandage cell).',
    nominal: 'Tens to a few hundred Ω, essentially flat before and after exposure — used as an internal control.' },
  { key: 'rct', name: 'Rct', unit: 'Ω', get: (p) => p.rct, fmt: (v) => fmtNum(v), hint: 'Charge-transfer (interfacial) resistance: how hard it is for a redox probe to exchange electrons with the graphene surface — the Nyquist semicircle diameter.',
    why: 'Interfacial resistance rises because bound bacteria sterically block the redox probe and insulate the surface. Published graphene/aptamer E. coli biosensors report baseline Rct of ~10²–10³ Ω, growing several-fold on binding — this is the primary detection signal.',
    nominal: '~10²–10³ Ω clean baseline; a rise beyond the detection threshold flags binding.' },
  { key: 'cdl', name: 'Cdl', unit: 'µF', get: (p) => p.cdl * 1e6, fmt: (v) => v.toFixed(3), hint: 'Double-layer capacitance at the electrode/electrolyte interface (reported from the constant-phase-element prefactor Q).',
    why: 'Falls: a thicker, low-permittivity bacterial layer displaces the charged double layer away from the conductive surface, lowering its capacitance — typically a few µF for functionalized graphene electrodes.',
    nominal: 'A few µF clean baseline; falls as bacterial fouling thickens the interface.' },
  { key: 'warburg', name: 'Warburg σ', unit: 'Ω·s⁻½', get: (p) => p.sigma, fmt: (v) => fmtNum(v), hint: 'Warburg coefficient: diffusion-limited transport of the redox probe to the surface — the ~45° low-frequency tail of the Nyquist plot.',
    why: 'Rises: bound cells hinder how freely the probe diffuses through the boundary layer to reach the electrode, a secondary (diffusion-related) contributor alongside Rct.',
    nominal: 'Rises modestly alongside Rct as diffusion becomes hindered; no fixed clean-baseline range.' },
  { key: 'zmag', name: '|Z| @ 100 Hz', unit: 'Ω', get: (p) => readout(p).mag, fmt: (v) => fmtNum(v), hint: 'Total impedance magnitude at the reference frequency: a single-number readout combining all circuit elements.',
    why: 'Total opposition to the AC current grows as Rct grows: the easiest single number to track without fitting the full spectrum.',
    nominal: 'Tracks Rct — rises with binding; flagged once the change exceeds the detection threshold.' },
  { key: 'phase', name: 'Phase @ 100 Hz', unit: '°', get: (p) => readout(p).phase, fmt: (v) => v.toFixed(1), hint: 'Phase lag between the applied voltage and the resulting current at the reference frequency.',
    why: 'The balance between resistive (Rct) and capacitive (Cdl) behaviour shifts, so the phase angle moves as bacteria accumulate.',
    nominal: 'Shifts a few degrees as the resistive/capacitive balance changes with binding.' },
];

type Tone = 'ok' | 'bad' | 'neutral';

const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-emerald-600 dark:text-emerald-300',
  bad: 'text-rose-600 dark:text-rose-300 font-bold',
  neutral: 'text-slate-800 dark:text-slate-100',
};

const TONE_GLOW: Record<Tone, string> = {
  ok: 'drop-shadow-[0_0_5px_rgba(16,185,129,0.55)]',
  bad: 'drop-shadow-[0_0_5px_rgba(244,63,94,0.6)]',
  neutral: '',
};

function deltaTone(dl: number | null, flag: boolean): Tone {
  if (dl === null) return 'neutral';
  return flag ? 'bad' : 'ok';
}

function ParamTable({ state, selected, onSelect }: { state: AssayState; selected: PartKey | null; onSelect: (p: PartKey | null) => void }) {
  const showPost = phaseIndex(state.phase) >= phaseIndex('binding');
  return (
    <section className={CARD} aria-label="Sensor parameters">
      <h2 className={H}>
        Sensor parameters
        <InfoHint align="left" text="Values from fitting the equivalent circuit to each spectrum. Click a row to highlight the matching part in the 3D view." />
      </h2>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-xs">
          <caption className="sr-only">Equivalent-circuit parameters, baseline versus post-exposure</caption>
          <thead>
            <tr className="border-b border-slate-200 text-[11px] uppercase tracking-wide text-slate-500 dark:border-white/10">
              <th scope="col" className="py-1.5 pr-2 font-semibold">Parameter</th>
              <th scope="col" className="px-2 py-1.5 text-right font-semibold">Baseline</th>
              <th scope="col" className="px-2 py-1.5 text-right font-semibold">Post-exposure</th>
              <th scope="col" className="py-1.5 pl-2 text-right font-semibold">Δ %</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r) => {
              const b = state.baseline ? r.get(state.baseline) : null;
              const p = showPost ? r.get(state.current) : null;
              const dl = b !== null && p !== null && b !== 0 ? ((p - b) / Math.abs(b)) * 100 : null;
              const flag = dl !== null && Math.abs(dl) > CALIBRATION.thresholdPct;
              const active = selected === r.key;
              return (
                <tr key={r.key} className={`border-b border-slate-100 align-top dark:border-white/5 ${active ? 'bg-amber-400/10' : ''}`}>
                  <th scope="row" className="py-2 pr-2 font-normal">
                    <div className="flex items-start gap-1">
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => onSelect(active ? null : r.key)}
                        className={`min-w-0 flex-1 rounded text-left ${FOCUS}`}
                      >
                        <span className="flex items-center gap-1 font-semibold text-slate-800 dark:text-slate-100">
                          {active ? '● ' : ''}{r.name} <span className="font-normal text-slate-500">({r.unit})</span>
                        </span>
                        <span className="mt-0.5 block text-[11px] leading-snug text-slate-500 dark:text-slate-400">{r.why}</span>
                      </button>
                      <InfoHint align="left" text={r.hint} nominal={r.nominal} className="mt-0.5 shrink-0" />
                    </div>
                  </th>
                  <td className="px-2 py-2 text-right font-mono tabular-nums text-slate-800 dark:text-slate-100">{b === null ? '—' : r.fmt(b)}</td>
                  <td className={`px-2 py-2 text-right font-mono tabular-nums ${TONE_TEXT[deltaTone(showPost ? dl : null, flag)]} ${flag ? TONE_GLOW.bad : ''}`}>
                    {p === null ? '—' : r.fmt(p)}
                  </td>
                  <td className={`whitespace-nowrap py-2 pl-2 text-right font-mono tabular-nums ${TONE_TEXT[deltaTone(dl, flag)]}`}>
                    {dl === null ? '—' : <>{arrow(dl)} {fmtPct(dl)}{flag && <span title={`Beyond the ${CALIBRATION.thresholdPct} % detection threshold`}> ⚑</span>}</>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        ▲ increase · ▼ decrease · ⚑ change beyond the {CALIBRATION.thresholdPct} % detection threshold (3 × {CALIBRATION.noiseSdPct} % baseline noise).
      </p>
      <p className="mt-1 text-[11px] text-slate-500">
        Baseline magnitudes are consistent with published graphene/aptamer EIS biosensors for <em>E. coli</em>: Rct on the order of 10²–10³ Ω before exposure, a few µF of double-layer capacitance, and dRct-based limits of detection in the 1–100 CFU/mL range with linear response extending to ~10⁷–10⁸ CFU/mL.
      </p>
    </section>
  );
}

/* --------------------------------------------------------- equivalent circuit */

const CIRCUIT_TONE_FILL: Record<Tone, string> = {
  ok: 'fill-emerald-600 dark:fill-emerald-300',
  bad: 'fill-rose-600 dark:fill-rose-300',
  neutral: 'fill-slate-800 dark:fill-slate-100',
};

function CircuitElement({
  part, selected, onSelect, x, y, w, h, name, value, delta, children,
}: {
  part: PartKey; selected: PartKey | null; onSelect: (p: PartKey | null) => void;
  x: number; y: number; w: number; h: number; name: string; value: string; delta: number | null; children: ReactNode;
}) {
  const active = selected === part;
  const toggle = () => onSelect(active ? null : part);
  const onKey = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  };
  const flag = delta !== null && Math.abs(delta) > CALIBRATION.thresholdPct;
  const tone = deltaTone(delta, flag);
  return (
    <g role="button" tabIndex={0} aria-pressed={active} aria-label={`${name}: ${value}`} onClick={toggle} onKeyDown={onKey} className="cursor-pointer outline-none [&:focus-visible>rect]:stroke-amber-500 [&:focus-visible>rect]:stroke-2">
      <rect x={x} y={y} width={w} height={h} rx={8} className={active ? 'fill-amber-400/20 stroke-amber-500' : 'fill-transparent stroke-transparent hover:fill-slate-500/10'} strokeWidth={active ? 2 : 1} strokeDasharray={active ? undefined : undefined} />
      {children}
      <text x={x + w / 2} y={y + h - 6} textAnchor="middle" className={`font-mono ${CIRCUIT_TONE_FILL[tone]}`} fontSize="11" fontWeight={active || tone !== 'neutral' ? 700 : 500}>
        {active ? '● ' : ''}{name} {value}{delta !== null && Math.abs(delta) >= 0.05 ? ` ${arrow(delta)}` : ''}
      </text>
    </g>
  );
}

function Circuit({ state, selected, onSelect }: { state: AssayState; selected: PartKey | null; onSelect: (p: PartKey | null) => void }) {
  const p = state.current;
  const b = state.baseline;
  const post = phaseIndex(state.phase) >= phaseIndex('binding');
  const d = (cur: number, base: number | undefined) => (post && base !== undefined ? deltaPct(cur, base) : null);
  const wire = 'stroke-slate-500 dark:stroke-slate-400';
  return (
    <section className={CARD} aria-label="Equivalent circuit">
      <h2 className={H}>
        Equivalent circuit (modified Randles)
        <InfoHint align="left" text="Rs in series with [Cdl in parallel with (Rct + Warburg)]. Click an element to highlight the corresponding region in the 3D view." />
      </h2>
      <svg viewBox="0 0 500 220" className="mt-2 h-auto w-full max-w-xl" role="group" aria-label="Equivalent circuit diagram: Rs in series with the parallel combination of Cdl and Rct plus Warburg">
        <g fill="none" strokeWidth="2" className={wire} strokeLinecap="round" strokeLinejoin="round">
          {/* terminals + main wire */}
          <circle cx="14" cy="110" r="4" className="fill-slate-500 dark:fill-slate-400" />
          <circle cx="486" cy="110" r="4" className="fill-slate-500 dark:fill-slate-400" />
          <path d="M18 110 H24 M116 110 H150 M150 60 V160 M430 60 V160 M430 110 H482" />
          {/* Rs zigzag */}
          <path d="M24 110 l8 0 l6 -12 l12 24 l12 -24 l12 24 l6 -12 l8 0 H116" className="stroke-slate-700 dark:stroke-slate-200" />
          {/* top branch: Cdl */}
          <path d="M150 60 H262 M278 60 H430" />
          <path d="M262 42 V78 M278 42 V78" className="stroke-slate-700 dark:stroke-slate-200" />
          {/* bottom branch: Rct + Warburg */}
          <path d="M150 160 H168 l6 0 l6 -12 l12 24 l12 -24 l12 24 l6 -12 l6 0 H262 M346 160 H430" />
          <path d="M168 160 l6 0 l6 -12 l12 24 l12 -24 l12 24 l6 -12 l6 0" className="stroke-slate-700 dark:stroke-slate-200" />
          <path d="M262 160 H274 M334 160 H346" />
          <path d="M274 145 H334 V175 H274 Z M281 168 L292 152 L300 168 L308 152 L318 168 L327 152" className="stroke-slate-700 dark:stroke-slate-200" />
        </g>
        <CircuitElement part="rs" selected={selected} onSelect={onSelect} x={24} y={68} w={92} h={100} name="Rs" value={`${fmtNum(p.rs)} Ω`} delta={d(p.rs, b?.rs)}>
          <title>Solution resistance</title>
        </CircuitElement>
        <CircuitElement part="cdl" selected={selected} onSelect={onSelect} x={236} y={10} w={68} h={82} name="Cdl" value={`${(p.cdl * 1e6).toFixed(2)} µF`} delta={d(p.cdl, b?.cdl)}>
          <title>Double-layer capacitance</title>
        </CircuitElement>
        <CircuitElement part="rct" selected={selected} onSelect={onSelect} x={160} y={122} w={100} h={78} name="Rct" value={`${fmtNum(p.rct)} Ω`} delta={d(p.rct, b?.rct)}>
          <title>Charge-transfer resistance</title>
        </CircuitElement>
        <CircuitElement part="warburg" selected={selected} onSelect={onSelect} x={266} y={132} w={88} h={80} name="W σ" value={`${fmtNum(p.sigma)}`} delta={d(p.sigma, b?.sigma)}>
          <title>Warburg diffusion element</title>
        </CircuitElement>
      </svg>
      <p className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[11px] text-slate-500">
        {(['rs', 'cdl', 'rct', 'warburg'] as const).map((key, i) => {
          const r = ROWS.find((row) => row.key === key)!;
          const short = key === 'rs' ? 'Rs: electrolyte' : key === 'cdl' ? 'Cdl: double layer' : key === 'rct' ? 'Rct: electron-transfer barrier' : 'W: probe diffusion';
          return (
            <span key={key} className="inline-flex items-center gap-0.5">
              {short}
              <InfoHint text={r.hint} nominal={r.nominal} align={i === 0 ? 'left' : 'center'} />
              {i < 3 ? <span className="ml-1">·</span> : null}
            </span>
          );
        })}
        <span>Live values follow the binding; ▲/▼ show change from baseline; green/red text flags whether that change is within or beyond the detection threshold.</span>
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ detection */

function Detection({ state, dark }: { state: AssayState; dark: boolean }) {
  const c = palette(dark);
  const isResult = state.phase === 'result';
  const idle = state.phase === 'idle';
  const post = phaseIndex(state.phase) >= phaseIndex('binding');
  const dZ = state.baseline && post ? readout(state.current).mag - readout(state.baseline).mag : null;

  const badge = idle
    ? { icon: '○', text: 'READY', cls: 'border-slate-300 text-slate-600 dark:border-white/20 dark:text-slate-300' }
    : !isResult
      ? { icon: '⏳', text: 'MEASURING…', cls: 'border-slate-400 text-slate-700 dark:border-white/30 dark:text-slate-200' }
      : state.detected
        ? { icon: '✔', text: 'DETECTED', cls: 'border-emerald-600 bg-emerald-600/10 text-emerald-800 dark:border-emerald-400 dark:text-emerald-300' }
        : { icon: '✖', text: 'NOT DETECTED', cls: 'border-slate-500 bg-slate-500/10 text-slate-800 dark:border-slate-400 dark:text-slate-200' };

  const curve = useMemo(() => {
    const pts: { x: number; cal: number | null; model: number }[] = [];
    for (let x = 1; x <= 8.001; x += 0.25) {
      const inFit = x >= Math.log10(CALIBRATION.fitLoCfu) - 1e-9 && x <= Math.log10(CALIBRATION.fitHiCfu) + 1e-9;
      pts.push({ x, cal: inFit ? calibratedDeltaRctPct(Math.pow(10, x)) : null, model: 100 * DEFAULTS.aRct * coverage(Math.pow(10, x), DEFAULTS.kd) });
    }
    return pts;
  }, []);
  const tick = { fontSize: 10, fill: c.axis };
  const showPoint = state.deltaRctPct !== null && state.cfuPerMl > 0 && post;

  return (
    <section className={CARD} aria-label="Detection result">
      <h2 className={H}>
        Detection
        <InfoHint align="left" text="The sample is called positive when ΔRct exceeds 3 × the baseline noise. Concentration is then read off the calibration line." />
      </h2>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div role="status" aria-live="polite" className={`inline-flex items-center gap-2 rounded-lg border-2 px-3 py-1.5 text-sm font-bold tracking-wide ${badge.cls}`}>
          <span aria-hidden="true">{badge.icon}</span>
          <span>{badge.text}</span>
          <span className="sr-only">
            {isResult
              ? state.detected
                ? `E. coli detected. Estimated ${state.estimatedCfuPerMl ? sci(state.estimatedCfuPerMl) : 'unknown'} CFU per millilitre.`
                : 'No significant impedance change. E. coli not detected.'
              : ''}
          </span>
        </div>
        <div className="text-xs text-slate-700 dark:text-slate-200">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Estimated concentration</div>
          <div className="font-mono text-sm font-semibold">
            {isResult
              ? state.estimatedCfuPerMl !== null ? `≈ ${sci(state.estimatedCfuPerMl)} CFU/mL` : `< LOD (~${Math.round(effectiveLodCfu())} CFU/mL)`
              : '—'}
          </div>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="flex items-center gap-1 text-[11px] text-slate-500">
            ΔZ @ 100 Hz
            <InfoHint text="Change in total impedance magnitude at 100 Hz versus the clean-electrode baseline." nominal="Near 0 Ω for a clean/negative sample; a positive shift tracks Rct rising with binding." align="left" />
          </dt>
          <dd className={`font-mono font-semibold ${TONE_TEXT[deltaTone(post ? dZ : null, dZ !== null && state.deltaZMagPct !== null && Math.abs(state.deltaZMagPct) > CALIBRATION.thresholdPct)]}`}>
            {dZ === null ? '—' : `${dZ > 0 ? '+' : ''}${fmtNum(dZ)} Ω`}
          </dd>
          <dd className="font-mono text-[11px] text-slate-500">{state.deltaZMagPct === null || !post ? '' : `${arrow(state.deltaZMagPct)} ${fmtPct(state.deltaZMagPct)}`}</dd>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="flex items-center gap-1 text-[11px] text-slate-500">
            ΔRct
            <InfoHint text="Percent change in charge-transfer resistance versus baseline -- the primary detection signal." nominal="Below the threshold for a clean sample; above it once E. coli binding is significant." align="left" />
          </dt>
          <dd className={`font-mono font-semibold ${TONE_TEXT[deltaTone(post ? state.deltaRctPct : null, state.deltaRctPct !== null && Math.abs(state.deltaRctPct) > CALIBRATION.thresholdPct)]}`}>
            {post ? `${arrow(state.deltaRctPct)} ${fmtPct(state.deltaRctPct)}` : '—'}
          </dd>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="flex items-center gap-1 text-[11px] text-slate-500">
            Threshold
            <InfoHint text="The ΔRct magnitude a sample must exceed to be called positive: 3x the baseline noise standard deviation." nominal={`Fixed at ${CALIBRATION.thresholdPct} % (3 × ${CALIBRATION.noiseSdPct} % baseline noise).`} align="right" />
          </dt>
          <dd className="font-mono font-semibold text-slate-800 dark:text-slate-100">&gt; {CALIBRATION.thresholdPct} %</dd>
          <dd className="text-[11px] text-slate-500">3 × {CALIBRATION.noiseSdPct} %</dd>
        </div>
      </dl>

      <h3 className="mt-4 text-[11px] font-medium text-slate-600 dark:text-slate-300">Calibration: ΔRct % vs log₁₀ CFU/mL</h3>
      <div role="img" aria-label="Calibration curve of percent change in charge-transfer resistance against log concentration, with the current sample marked">
        <ResponsiveContainer width="100%" height={190}>
          <LineChart data={curve} margin={{ top: 8, right: 12, bottom: 16, left: 4 }}>
            <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
            <XAxis type="number" dataKey="x" domain={[1, 8]} ticks={[1, 2, 3, 4, 5, 6, 7, 8]} tick={tick} label={{ value: 'log₁₀ CFU/mL', fill: c.axis, fontSize: 10, position: 'insideBottom', offset: -4 }} />
            <YAxis domain={[-30, 170]} ticks={[0, 50, 100, 150]} tick={tick} width={40} allowDataOverflow tickFormatter={(v: number) => `${v}%`} />
            <Tooltip contentStyle={tooltipStyle(c)} labelFormatter={(x) => `10^${typeof x === 'number' ? x.toFixed(2) : x} CFU/mL`} formatter={(v) => (typeof v === 'number' ? `${v.toFixed(1)} %` : String(v))} />
            <ReferenceLine y={CALIBRATION.thresholdPct} stroke={c.thr} strokeDasharray="4 3" label={{ value: `threshold ${CALIBRATION.thresholdPct}%`, fill: c.thr, fontSize: 9, position: 'insideTopLeft' }} />
            <Line type="linear" dataKey="cal" name="Calibration fit" stroke={c.base} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="model" name="Sensor response (saturates)" stroke={c.axis} strokeWidth={1.5} strokeDasharray="2 3" dot={false} isAnimationActive={false} />
            {showPoint && (
              <ReferenceDot x={Math.log10(state.cfuPerMl)} y={state.deltaRctPct as number} r={6} fill={c.post} stroke={dark ? '#0a0a0d' : '#ffffff'} strokeWidth={1.5} ifOverflow="visible"
                label={{ value: 'this sample', fill: c.post, fontSize: 10, position: 'top' }} />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <Legend items={[
        { label: `Calibration fit (${CALIBRATION.slope.toFixed(0)} %/decade, 10²–10⁵)`, color: c.base, shape: 'line' },
        { label: 'Sensor response (saturates)', color: c.axis, shape: 'line', dashed: true },
        { label: '● this sample', color: c.post, shape: 'diamond' },
      ]} />
      {state.cfuPerMl === 0 && post && <p className="mt-1 text-[11px] text-slate-500">Clean control: ΔRct = {fmtPct(state.deltaRctPct)}, so no point on the concentration axis.</p>}
    </section>
  );
}

/* ---------------------------------------------------------------- how it works */

const STEPS: [string, string][] = [
  ['Baseline characterization', 'Measure the clean graphene-PBASE-aptamer electrode first. PBASE (1-pyrenebutanoic acid succinimidyl ester) anchors to graphene by π–π stacking of its pyrene ring, while its NHS-ester end covalently couples the aptamer’s amine group — giving a stable, oriented aptamer layer. This baseline spectrum is the reference every later change is judged against.'],
  ['Target interaction', 'The sample, possibly containing E. coli, contacts the sensing surface. Bacteria that match the aptamer bind to it (coverage θ = C / (Kd + C)), changing the electrical properties at the electrode–electrolyte interface.'],
  ['Impedance measurement', 'A very small AC voltage (about 10 mV) is swept from 0.1 Hz to 100 kHz and the resulting current is measured, so the surface is probed without being damaged.'],
  ['What changes', 'Bacterial attachment can alter: interfacial resistance (Rct up — electron transfer is blocked), charge-transfer behaviour, double-layer capacitance (Cdl down — a thicker dielectric layer forms), and diffusion of the redox probe (Warburg σ up) — shifting the overall impedance magnitude and phase.'],
  ['Data interpretation', 'The post-exposure spectrum is compared with the baseline. A significant change relative to a clean control (beyond 3 × the baseline noise) indicates binding.'],
  ['Equivalent-circuit modelling', 'Fitting Rs — [Cdl ‖ (Rct + W)] — solution resistance, charge-transfer resistance, double-layer capacitance and a diffusion-related Warburg element — turns the spectrum into physical parameters. Rct is the most informative.'],
  ['Detection output', 'The difference between baseline and post-exposure (ΔRct %) is mapped to concentration through calibration data, or reported as below the limit of detection.'],
];

function HowItWorks() {
  return (
    <details className={CARD}>
      <summary className={`cursor-pointer rounded ${H} ${FOCUS}`}>How it works</summary>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-xs leading-relaxed text-slate-700 dark:text-slate-300">
        {STEPS.map(([t, body]) => (
          <li key={t}><span className="font-semibold text-slate-900 dark:text-slate-100">{t}.</span> {body}</li>
        ))}
      </ol>
      <p className="mt-3 border-t border-slate-200 pt-2 text-[11px] text-slate-500 dark:border-white/10">
        Model basis: this simulation follows the standard graphene-aptamer EIS assay reported in the literature — a modified Randles equivalent circuit (Rs, Rct, a constant-phase double layer, Warburg diffusion) fitted to Nyquist/Bode spectra, with Rct as the dominant, most-cited binding signal.
      </p>
    </details>
  );
}

/* --------------------------------------------------------------- live readout */

function fmtClock(s: number): string {
  const t = Math.max(0, Math.floor(s));
  const pad = (v: number) => String(v).padStart(2, '0');
  return `${pad(Math.floor(t / 3600))}:${pad(Math.floor((t % 3600) / 60))}:${pad(t % 60)}`;
}

function fmtFreq(f: number): string {
  if (f >= 1000) return `${(f / 1000).toPrecision(3)} kHz`;
  return `${f.toPrecision(3)} Hz`;
}

function LiveReadout({ state, dark, interactive }: { state: AssayState; dark: boolean; interactive: boolean }) {
  const c = palette(dark);
  const live = state.live;
  const trace = live?.trace ?? [];
  const data = useMemo(() => trace.map((p) => ({ m: p.t / 60, d: p.dRctPct })), [trace]);
  const last = trace.length ? trace[trace.length - 1] : null;
  const idle = state.phase === 'idle';
  const acquiring = !idle && !state.stale;
  const sweepF = live?.sweepFreqHz ?? null;

  const status = state.stale && !idle
    ? 'Feed stalled'
    : idle
      ? 'Standby'
      : sweepF !== null
        ? `${state.phase === 'baseline' ? 'Baseline sweep' : `Sweep ${(live?.sweepIndex ?? 0) + 1}`} · ${fmtFreq(sweepF)}`
        : state.phase === 'exposure' ? 'Loading sample' : `Reading ${(live?.sweepIndex ?? 0) + 1}`;

  const lo = data.reduce((m, p) => Math.min(m, p.d), 0);
  const hi = data.reduce((m, p) => Math.max(m, p.d), CALIBRATION.thresholdPct);
  const yLo = Math.floor((lo - 4) / 10) * 10;
  const yHi = Math.ceil((hi + 6) / 10) * 10;
  const tick = { fontSize: 10, fill: c.axis };
  const flag = last !== null && last.dRctPct > CALIBRATION.thresholdPct;

  return (
    <section className={CARD} aria-label="Live readout">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className={H}>
          Live readout
          <InfoHint align="left" text="What the analyser reports while it runs: the frequency being measured, the running clock, and the detection signal as a time series (sensorgram). The readout keeps going after the last step, so the sensor can be watched responding to a change of sample." />
        </h2>
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-slate-700 dark:text-slate-200">
          <span className="relative flex h-2 w-2" aria-hidden="true">
            {acquiring && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${acquiring ? 'bg-emerald-500' : 'bg-slate-400 dark:bg-slate-600'}`} />
          </span>
          {status}
        </span>
        <span className="ml-auto font-mono text-[11px] text-slate-500" title="Bench time this run stands for; the browser assay is time-compressed">
          T+ {fmtClock(live?.labTimeS ?? 0)}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="text-[11px] text-slate-500">|Z| @ {REF_FREQ_HZ} Hz</dt>
          <dd className="font-mono font-semibold tabular-nums text-slate-800 dark:text-slate-100">{last ? `${fmtNum(last.zMag)} Ω` : '—'}</dd>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="text-[11px] text-slate-500">ΔRct now</dt>
          <dd className={`font-mono font-semibold tabular-nums ${TONE_TEXT[deltaTone(last ? last.dRctPct : null, flag)]}`}>{last ? fmtPct(last.dRctPct) : '—'}</dd>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-white/10">
          <dt className="text-[11px] text-slate-500">Aptamer occupancy</dt>
          <dd className="font-mono font-semibold tabular-nums text-slate-800 dark:text-slate-100">{last ? `${(last.theta * 100).toFixed(1)} %` : '—'}</dd>
        </div>
      </dl>

      {data.length < 2 ? (
        <div className="mt-3 flex h-40 items-center justify-center rounded-lg border border-dashed border-slate-200 px-4 text-center text-sm text-slate-500 dark:border-white/10">
          {interactive ? 'Press “Run assay” to start acquiring.' : 'Waiting for readings.'}
        </div>
      ) : (
        <figure className="mt-3 min-w-0">
          <figcaption className="mb-1 text-[11px] font-medium text-slate-600 dark:text-slate-300">Sensorgram: ΔRct % against bench time</figcaption>
          <div role="img" aria-label="Time series of the percent change in charge-transfer resistance, with the detection threshold marked">
            <ResponsiveContainer width="100%" height={170}>
              <LineChart data={data} margin={{ top: 6, right: 12, bottom: 14, left: 4 }}>
                <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
                <XAxis type="number" dataKey="m" domain={['dataMin', 'dataMax']} tick={tick} tickFormatter={(v: number) => v.toFixed(v < 10 ? 1 : 0)} label={{ value: 'Bench time (min)', fill: c.axis, fontSize: 10, position: 'insideBottom', offset: -2 }} />
                <YAxis domain={[yLo, yHi]} tick={tick} width={44} allowDataOverflow tickFormatter={(v: number) => `${v}%`} />
                <Tooltip contentStyle={tooltipStyle(c)} labelFormatter={(m) => `${typeof m === 'number' ? m.toFixed(1) : m} min`} formatter={(v) => (typeof v === 'number' ? `${v.toFixed(1)} %` : String(v))} />
                <ReferenceLine y={CALIBRATION.thresholdPct} stroke={c.thr} strokeDasharray="4 3" label={{ value: `threshold ${CALIBRATION.thresholdPct}%`, fill: c.thr, fontSize: 9, position: 'insideTopLeft' }} />
                <Line type="linear" dataKey="d" name="ΔRct" stroke={c.post} strokeWidth={1.75} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </figure>
      )}
      {interactive && live?.monitoring && (
        <p className="mt-2 text-[11px] text-slate-500">
          Steps complete; the sensor is still being read. Move the <em>E. coli</em> slider or switch to the clean control to watch the surface bind or release in real time.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------ reference data */

function ReferenceData({ showRef, onShowRef }: { showRef: boolean; onShowRef: (v: boolean) => void }) {
  const p = DEFAULTS.params;
  return (
    <section className={CARD} aria-label="Published reference data">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className={H}>
          Published reference data
          <InfoHint align="left" text="Bench measurements of comparable E. coli biosensors, for judging whether the simulated magnitudes are realistic. They are shown beside the model and do not feed it." />
        </h2>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={showRef} onChange={(e) => onShowRef(e.target.checked)} className="accent-amber-500" />
          Overlay the open spectrum on the Bode plots
        </label>
      </div>
      <ul className="mt-3 grid gap-3 @2xl:grid-cols-2">
        {REFERENCE_SOURCES.map((s) => (
          <li key={s.id} className="rounded-lg border border-slate-200 p-3 text-xs dark:border-white/10">
            <p className="font-semibold text-slate-800 dark:text-slate-100">{s.sensor}</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11px] leading-snug text-slate-700 dark:text-slate-300">
              {s.facts.map((f) => <li key={f}>{f}</li>)}
            </ul>
            <p className="mt-1.5 text-[11px] leading-snug text-slate-500">{s.caveat}</p>
            <a href={s.url} target="_blank" rel="noreferrer" className={`mt-1.5 inline-block rounded text-[11px] text-amber-700 underline decoration-dotted underline-offset-2 dark:text-amber-300 ${FOCUS}`}>
              {s.citation}
            </a>
          </li>
        ))}
      </ul>
      <h3 className="mt-4 text-[11px] font-medium text-slate-600 dark:text-slate-300">How the twin compares</h3>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] leading-snug text-slate-600 dark:text-slate-400">
        <li>Sweep ({DEFAULTS.fMin} Hz–{DEFAULTS.fMax / 1000} kHz, {DEFAULTS.amplitudeMv} mV) and the modified Randles circuit match the published faradaic protocols.</li>
        <li>Rs {p.rs} Ω sits within a factor of three of the measured 297 Ω; Rct {p.rct} Ω is about ten times the 10² Ω reported on macro-electrodes.</li>
        <li>The twin’s saturated response (ΔRct +{(DEFAULTS.aRct * 100).toFixed(0)} %) is several times stronger than the +28 % reported at 6×10⁵ CFU/mL, so its detection margins are optimistic.</li>
        <li>Its {(DEFAULTS.noise * 100).toFixed(0)} % point noise is at the quiet end of the 1–5 % scatter in the open spectra.</li>
        <li>The binding step stands for 30 min of contact; the cited assays incubate for 60 min.</li>
      </ul>
    </section>
  );
}

/* ---------------------------------------------------------------------- panel */

export function EisPanel({ state, controls, selected, onSelect }: EisPanelProps) {
  const dark = useIsDark();
  const [showRef, setShowRef] = useState(false);
  return (
    <div className="@container space-y-4">
      {controls && <Workflow state={state} controls={controls} />}
      <LiveReadout state={state} dark={dark} interactive={controls !== null} />
      <Charts state={state} dark={dark} showRef={showRef} />
      <div className="grid gap-4 @2xl:grid-cols-2">
        <Circuit state={state} selected={selected} onSelect={onSelect} />
        <Detection state={state} dark={dark} />
      </div>
      <div className="grid gap-4 @3xl:grid-cols-2 @3xl:items-start">
        <ParamTable state={state} selected={selected} onSelect={onSelect} />
        <HowItWorks />
      </div>
      <ReferenceData showRef={showRef} onShowRef={setShowRef} />
    </div>
  );
}
