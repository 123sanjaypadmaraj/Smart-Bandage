/**
 * Electrochemical impedance (EIS) model + assay sequencer for the smart-bandage
 * digital twin. Pure TypeScript, no React. Physics follows the header of
 * ./types.ts exactly (modified Randles circuit with a CPE for the double layer,
 * Langmuir binding, linear dRct% calibration).
 *
 * Documented choices where types.ts leaves latitude:
 *  - CPE prefactor from the reported Cdl via the Brug relation (matches the Python
 *    backend, digital_twin/bandage/impedance.py):
 *      Q = Cdl^n * (1/Rs + 1/Rct)^(1-n),   Q = Cdl when n == 1.
 *  - Measurement noise: additive gaussian on Z' and Z'' with SD = noise*|Z| (as in the backend),
 *    seeded (mulberry32), so the spectra are deterministic.
 *  - Until the second spectrum is measured the sequencer reports the underlying model
 *    parameters. From the 'compare' step on it reports parameters FITTED (Levenberg-Marquardt)
 *    to the noisy spectra, and detection uses the fitted dRct%. noise = 0 skips the fit
 *    (fit of exact data == truth), which keeps dRct% exactly 0 at 0 CFU/mL in tests.
 *  - Calibration line = log-linear least-squares fit of the model's own noise-free dRct%
 *    over 1e2..1e5 CFU/mL (identical to Calibration.fit_to_model() in the backend).
 *    The sensor saturates (dRct% -> 160 %), so estimateCfu() is clamped to CAL_MAX_CFU.
 */
import { REF_FREQ_HZ } from './types.ts';
import type { AssayPhase, AssayState, EisParams, EisSpectrum, ImpedancePoint } from './types.ts';

/* ------------------------------------------------------------------ defaults */

export const DEFAULTS = {
  params: { rs: 120, rct: 2000, cdl: 2.0e-6, sigma: 150, n: 0.92 } as EisParams,
  kd: 1e4,          // CFU/mL
  aRct: 1.6,
  aCdl: 0.35,
  aRs: 0.04,
  aSigma: 0.5,
  fMin: 0.1,        // Hz
  fMax: 1e5,        // Hz
  ptsPerDecade: 10,
  amplitudeMv: 10,
  noise: 0.01,      // 1 % relative noise on the measured spectra
  seed: 20240607,
} as const;

/** Log-linear LS fit of the noise-free model dRct% over [cfuLo, cfuHi] (41 log-spaced points), as in the backend. */
function fitCalibrationLine(cfuLo = 1e2, cfuHi = 1e5): { slope: number; intercept: number } {
  const n = 41;
  const x0 = Math.log10(cfuLo);
  const x1 = Math.log10(cfuHi);
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) {
    const x = x0 + ((x1 - x0) * i) / (n - 1);
    const y = 100 * DEFAULTS.aRct * coverage(Math.pow(10, x), DEFAULTS.kd);
    sx += x; sy += y; sxx += x * x; sxy += x * y;
  }
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);
  return { slope, intercept: (sy - slope * sx) / n };
}

/** dRct% = slope * log10(CFU/mL) + intercept, fitted to the model over 1e2..1e5 CFU/mL; positive if dRct% > thresholdPct. */
export const CALIBRATION = (() => {
  const { slope, intercept } = fitCalibrationLine();
  return {
    slope,                // % per decade  (~54.8)
    intercept,            // %             (~-136)
    fitLoCfu: 1e2,
    fitHiCfu: 1e5,
    noiseSdPct: 1.5,      // baseline-noise SD of dRct%
    kSigma: 3,
    thresholdPct: 3 * 1.5 // 3 * SD = 4.5 %
  };
})();

export const CAL_MIN_CFU = 10;
export const CAL_MAX_CFU = 1e8;

/* ---------------------------------------------------------------- basic math */

/** Log-spaced frequency grid, inclusive of both ends (default 0.1 Hz..100 kHz, 61 pts). */
export function logSweep(
  fMin: number = DEFAULTS.fMin,
  fMax: number = DEFAULTS.fMax,
  ptsPerDecade: number = DEFAULTS.ptsPerDecade,
): number[] {
  const lo = Math.log10(fMin);
  const n = Math.round((Math.log10(fMax) - lo) * ptsPerDecade) + 1;
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(Math.pow(10, lo + i / ptsPerDecade));
  return out;
}

/** CPE coefficient Q (F s^(n-1)) from the reported Cdl (Brug relation; Q = Cdl when n = 1). */
export function qFromCdl(cdl: number, rs: number, rct: number, n: number): number {
  return n === 1 ? cdl : Math.pow(cdl, n) * Math.pow(1 / rs + 1 / rct, 1 - n);
}

/** Inverse of qFromCdl. */
export function cdlFromQ(q: number, rs: number, rct: number, n: number): number {
  return n === 1 ? q : Math.pow(q, 1 / n) * Math.pow(1 / rs + 1 / rct, (n - 1) / n);
}

function zRandles(freq: number, rs: number, rct: number, q: number, sigma: number, n: number): { re: number; im: number } {
  const w = 2 * Math.PI * Math.max(freq, 1e-12);
  const a = sigma / Math.sqrt(w);                   // Zw = a - j a
  const zx = rct + a;                               // Rct + Zw = zx - j a
  const den = zx * zx + a * a;
  const ywRe = zx / den;                            // 1/(Rct+Zw) = (zx + j a)/den
  const ywIm = a / den;
  const qw = q * Math.pow(w, n);                    // CPE admittance Q (jw)^n
  const yRe = ywRe + qw * Math.cos((n * Math.PI) / 2);
  const yIm = ywIm + qw * Math.sin((n * Math.PI) / 2);
  const yDen = yRe * yRe + yIm * yIm;
  return { re: rs + yRe / yDen, im: -yIm / yDen };
}

/** Complex impedance of the modified Randles cell at `freq` Hz. `im` is the physical Z'' (negative = capacitive). */
export function impedance(freq: number, p: EisParams): { re: number; im: number } {
  return zRandles(freq, p.rs, p.rct, qFromCdl(p.cdl, p.rs, p.rct, p.n), p.sigma, p.n);
}

/** mulberry32 seeded PRNG in [0,1). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rand: () => number): number {
  const u = Math.max(rand(), 1e-12);
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function makePoint(freq: number, re: number, im: number): ImpedancePoint {
  return { freq, re, im, mag: Math.hypot(re, im), phase: (Math.atan2(im, re) * 180) / Math.PI };
}

/** Spectrum over `freqs`. noise = SD as a fraction of |Z| (additive on Z' and Z''; 0 = ideal); deterministic for a given seed. */
export function spectrum(
  p: EisParams,
  freqs: number[] = logSweep(),
  noise = 0,
  seed: number = DEFAULTS.seed,
): EisSpectrum {
  const rand = mulberry32(seed);
  return freqs.map((f) => {
    const z = impedance(f, p);
    if (noise <= 0) return makePoint(f, z.re, z.im);
    const sd = noise * Math.hypot(z.re, z.im);
    return makePoint(f, z.re + sd * gauss(rand), z.im + sd * gauss(rand));
  });
}

/** |Z| and phase (deg) at one frequency, noise-free. */
export function readout(p: EisParams, freq: number = REF_FREQ_HZ): { mag: number; phase: number } {
  const z = impedance(freq, p);
  const pt = makePoint(freq, z.re, z.im);
  return { mag: pt.mag, phase: pt.phase };
}

/* ------------------------------------------------------------ binding / calib */

/** Langmuir fractional occupancy. */
export function coverage(cfu: number, kd: number = DEFAULTS.kd): number {
  const c = Math.max(cfu, 0);
  return c / (kd + c);
}

/** Sensor parameters at aptamer occupancy theta (0..1), from the clean-electrode `base`. */
export function paramsAtCoverage(base: EisParams, theta: number): EisParams {
  const t = Math.min(Math.max(theta, 0), 1);
  return {
    rs: base.rs * (1 + DEFAULTS.aRs * t),
    rct: base.rct * (1 + DEFAULTS.aRct * t),
    cdl: base.cdl * (1 - DEFAULTS.aCdl * t),
    sigma: base.sigma * (1 + DEFAULTS.aSigma * t),
    n: base.n,
  };
}

/** (post - base)/base*100; null when base is 0. */
export function deltaPct(post: number, base: number): number | null {
  return base === 0 ? null : ((post - base) / base) * 100;
}

/** Calibration-line prediction of dRct% for a concentration. */
export function calibratedDeltaRctPct(cfu: number): number {
  return CALIBRATION.slope * Math.log10(Math.max(cfu, 1e-9)) + CALIBRATION.intercept;
}

/** Concentration at which the calibration line crosses the detection threshold (calibration LOD). */
export function lodCfu(): number {
  return Math.pow(10, (CALIBRATION.thresholdPct - CALIBRATION.intercept) / CALIBRATION.slope);
}

/** Concentration at which the (noise-free) Langmuir sensor response reaches the threshold (physical LOD). */
export function physicalLodCfu(): number {
  const theta = CALIBRATION.thresholdPct / (100 * DEFAULTS.aRct);
  return (theta * DEFAULTS.kd) / (1 - theta);
}

export function isDetected(dRctPct: number | null): boolean {
  return dRctPct !== null && dRctPct > CALIBRATION.thresholdPct;
}

/** Inverse calibration: CFU/mL from dRct%, or null if below the detection threshold. */
export function estimateCfu(dRctPct: number | null): number | null {
  if (!isDetected(dRctPct)) return null;
  const c = Math.pow(10, ((dRctPct as number) - CALIBRATION.intercept) / CALIBRATION.slope);
  return Math.min(Math.max(c, CAL_MIN_CFU), CAL_MAX_CFU);
}


/** Concentration below which a sample cannot be called positive: the larger of the calibration LOD and the physical LOD. */
export function effectiveLodCfu(): number {
  return Math.max(lodCfu(), physicalLodCfu());
}

/* -------------------------------------------------------------------- fitting */

const LOG_LO = [1e-3, 1e-3, 1e-15, 1e-6].map(Math.log);
const LOG_HI = [1e9, 1e9, 1e-1, 1e9].map(Math.log);

interface FitVec { rs: number; rct: number; q: number; sigma: number; n: number }

function unpack(x: number[]): FitVec {
  const e = [0, 1, 2, 3].map((i) => Math.exp(Math.min(Math.max(x[i], LOG_LO[i]), LOG_HI[i])));
  return { rs: e[0], rct: e[1], q: e[2], sigma: e[3], n: Math.min(Math.max(x[4], 0.3), 1) };
}

function solveLinear(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(m[r][c]) > Math.abs(m[piv][c])) piv = r;
    if (Math.abs(m[piv][c]) < 1e-300) return null;
    [m[c], m[piv]] = [m[piv], m[c]];
    for (let r = c + 1; r < n; r++) {
      const f = m[r][c] / m[c][c];
      for (let k = c; k <= n; k++) m[r][k] -= f * m[c][k];
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let sum = m[r][n];
    for (let k = r + 1; k < n; k++) sum -= m[r][k] * x[k];
    x[r] = sum / m[r][r];
  }
  return x;
}

function levenbergMarquardt(x0: number[], resid: (x: number[]) => number[], iters = 80): { x: number[]; cost: number } {
  let x = x0.slice();
  let r = resid(x);
  let cost = r.reduce((s, v) => s + v * v, 0);
  let lam = 1e-2;
  const np = x.length;
  for (let it = 0; it < iters; it++) {
    const jac: number[][] = r.map(() => new Array<number>(np).fill(0));
    for (let k = 0; k < np; k++) {
      const h = 1e-6 * Math.max(1, Math.abs(x[k]));
      const xp = x.slice(); const xm = x.slice();
      xp[k] += h; xm[k] -= h;
      const rp = resid(xp); const rm = resid(xm);
      for (let i = 0; i < r.length; i++) jac[i][k] = (rp[i] - rm[i]) / (2 * h);
    }
    const a: number[][] = Array.from({ length: np }, () => new Array<number>(np).fill(0));
    const g = new Array<number>(np).fill(0);
    for (let i = 0; i < r.length; i++) {
      for (let p = 0; p < np; p++) {
        g[p] += jac[i][p] * r[i];
        for (let q = 0; q < np; q++) a[p][q] += jac[i][p] * jac[i][q];
      }
    }
    let improved = false;
    let converged = false;
    for (let attempt = 0; attempt < 12; attempt++) {
      const damped = a.map((row, i) => row.map((v, j) => (i === j ? v + lam * (v + 1e-12) : v)));
      const step = solveLinear(damped, g.map((v) => -v));
      if (!step) { lam *= 10; continue; }
      const xn = x.map((v, i) => v + step[i]);
      const rn = resid(xn);
      const cn = rn.reduce((s, v) => s + v * v, 0);
      if (cn < cost) {
        converged = cost - cn <= 1e-14 * Math.max(cost, 1e-30);
        x = xn; r = rn; cost = cn; lam = Math.max(lam / 5, 1e-9);
        improved = true;
        break;
      }
      lam *= 5;
    }
    if (!improved || converged) break;
  }
  return { x, cost };
}

/**
 * Least-squares fit of the modified Randles circuit to a (noisy) spectrum. Port of
 * fit_randles() in the backend: LM on (ln Rs, ln Rct, ln Q, ln sigma, n) minimising the
 * relative complex residual, from several starting points. Returns params (Cdl via Brug)
 * and the rms relative residual.
 */
export function fitRandles(spec: EisSpectrum): { params: EisParams; rms: number } {
  const pts = spec.slice().sort((a, b) => a.freq - b.freq);
  const scale = pts.map((p) => 1 / p.mag);
  const resid = (x: number[]): number[] => {
    const v = unpack(x);
    const out = new Array<number>(pts.length * 2);
    for (let i = 0; i < pts.length; i++) {
      const z = zRandles(pts[i].freq, v.rs, v.rct, v.q, v.sigma, v.n);
      out[i] = (z.re - pts[i].re) * scale[i];
      out[pts.length + i] = (z.im - pts[i].im) * scale[i];
    }
    return out;
  };
  const rs0 = Math.max(pts[pts.length - 1].re, 1e-2);
  const rct0 = Math.max(0.6 * (pts[0].re - rs0), 1e-2 * rs0 + 1);
  let k = 0;
  for (let i = 1; i < pts.length; i++) if (-pts[i].im > -pts[k].im) k = i;
  const wPk = 2 * Math.PI * pts[k].freq;
  let best: { x: number[]; cost: number } | null = null;
  for (const n0 of [0.9, 0.75]) {
    for (const sig0 of [0.05 * rct0, 0.2 * rct0]) {
      for (const qs of [1, 0.3, 3]) {
        const q0 = qs / (rct0 * Math.pow(wPk, n0));
        const res = levenbergMarquardt([Math.log(rs0), Math.log(rct0), Math.log(q0), Math.log(sig0), n0], resid);
        if (!best || res.cost < best.cost) best = res;
      }
    }
  }
  const v = unpack((best as { x: number[] }).x);
  return {
    params: { rs: v.rs, rct: v.rct, cdl: cdlFromQ(v.q, v.rs, v.rct, v.n), sigma: v.sigma, n: v.n },
    rms: Math.sqrt((best as { cost: number }).cost / (2 * pts.length)),
  };
}

/* ------------------------------------------------------------------ sequencer */

export type TimedPhase = Exclude<AssayPhase, 'idle'>;
const TIMED: TimedPhase[] = ['baseline', 'exposure', 'binding', 'measure2', 'compare', 'result'];

export interface AssayConfig {
  /** seconds spent in each phase (result is the terminal hold; its duration only scales its progress bar) */
  durations: Record<TimedPhase, number>;
  /** binding time constant, seconds (theta(t) = theta_eq * (1 - exp(-t/tau)) / (1 - exp(-T_binding/tau)), reaching theta_eq at the end of the step) */
  tau: number;
  /** relative SD of measurement noise on displayed spectra (0 for tests) */
  noise: number;
  seed: number;
  kd: number;
  params: EisParams;
}

export const DEFAULT_ASSAY_CONFIG: AssayConfig = {
  durations: { baseline: 2.5, exposure: 3, binding: 4, measure2: 3, compare: 2, result: 2 },
  tau: 0.8,
  noise: DEFAULTS.noise,
  seed: DEFAULTS.seed,
  kd: DEFAULTS.kd,
  params: DEFAULTS.params,
};

export class AssayController {
  private cfg: AssayConfig;
  private freqs = logSweep();
  private cfu = 0;
  private t = 0;
  private started = false;
  private paused = false;
  // caches so unchanged spectra keep object identity between ticks
  private baseSpec: EisSpectrum;
  private idleSpec: EisSpectrum;
  private curKey = '';
  private curSpec: EisSpectrum;
  private baseFit: EisParams | null = null;
  private postFitKey = '';
  private postFit: EisParams | null = null;

  constructor(config: Partial<AssayConfig> = {}) {
    this.cfg = {
      ...DEFAULT_ASSAY_CONFIG,
      ...config,
      durations: { ...DEFAULT_ASSAY_CONFIG.durations, ...(config.durations ?? {}) },
    };
    this.baseSpec = spectrum(this.cfg.params, this.freqs, this.cfg.noise, this.cfg.seed);
    this.idleSpec = this.baseSpec;
    this.curSpec = this.idleSpec;
  }

  /** Start (or restart) the sequence from the baseline step. Optionally sets the sample concentration. */
  start(cfu?: number): void {
    if (cfu !== undefined) this.cfu = Math.max(0, cfu);
    this.started = true;
    this.paused = false;
    this.t = 0;
  }

  reset(): void {
    this.started = false;
    this.paused = false;
    this.t = 0;
  }

  setCfu(cfu: number): void {
    this.cfu = Math.max(0, cfu);
  }

  pause(): void { this.paused = true; }
  resume(): void { this.paused = false; }
  get isPaused(): boolean { return this.paused; }
  get isRunning(): boolean { return this.started && !this.paused && !this.finished; }
  get finished(): boolean { return this.started && this.t >= this.phaseStart('result') + this.cfg.durations.result; }

  /** Advance by dtSeconds of assay time (no-op when not started, paused or finished). */
  tick(dtSeconds: number): void {
    if (!this.started || this.paused) return;
    const end = this.phaseStart('result') + this.cfg.durations.result;
    this.t = Math.min(this.t + Math.max(dtSeconds, 0), end);
  }

  /** Jump to the beginning of a phase; 'idle' resets. */
  skipTo(phase: AssayPhase): void {
    if (phase === 'idle') { this.reset(); return; }
    this.started = true;
    this.t = this.phaseStart(phase);
  }

  private phaseStart(phase: AssayPhase): number {
    let s = 0;
    for (const p of TIMED) {
      if (p === phase) return s;
      s += this.cfg.durations[p];
    }
    return s;
  }

  getState(): AssayState {
    const cfg = this.cfg;
    let phase: AssayPhase = 'idle';
    let phaseProgress = 0;
    if (this.started) {
      let s = 0;
      phase = 'result';
      for (const p of TIMED) {
        const d = cfg.durations[p];
        if (this.t < s + d || p === 'result') {
          phase = p;
          phaseProgress = d > 0 ? Math.min(Math.max((this.t - s) / d, 0), 1) : 1;
          break;
        }
        s += d;
      }
    }

    const idx = ASSAY_ORDER.indexOf(phase);
    const bindStart = this.phaseStart('binding');
    const thetaEq = coverage(this.cfu, cfg.kd);
    // binding stops when the binding step ends (sample is then measured, so the spectrum is static)
    const tb = Math.min(Math.max(0, this.t - bindStart), cfg.durations.binding);
    // normalised so the binding step ends exactly at Langmuir equilibrium (matches the backend's equilibrium assay)
    const theta = this.started && this.t >= bindStart
      ? (thetaEq * (1 - Math.exp(-tb / cfg.tau))) / (1 - Math.exp(-cfg.durations.binding / cfg.tau))
      : 0;

    const clean = cfg.params;
    const trueCurrent = paramsAtCoverage(clean, theta);
    const baselineDone = idx >= ASSAY_ORDER.indexOf('exposure');
    const fitted = idx >= ASSAY_ORDER.indexOf('compare');
    let baselineP: EisParams = clean;
    let current: EisParams = trueCurrent;

    // live spectrum: clean pattern until binding starts, then follow the params
    let currentSpectrum = this.idleSpec;
    const postMeasured = idx >= ASSAY_ORDER.indexOf('binding');
    if (postMeasured) {
      const key = `${trueCurrent.rs}|${trueCurrent.rct}|${trueCurrent.cdl}|${trueCurrent.sigma}`;
      if (key !== this.curKey) {
        this.curKey = key;
        this.curSpec = spectrum(trueCurrent, this.freqs, cfg.noise, cfg.seed + 1);
      }
      currentSpectrum = this.curSpec;
    }

    // equivalent-circuit modelling: fit both measured spectra (noise = 0 -> exact data -> truth)
    if (fitted && cfg.noise > 0) {
      if (!this.baseFit) this.baseFit = fitRandles(this.baseSpec).params;
      baselineP = this.baseFit;
      if (postMeasured) {
        if (this.postFitKey !== this.curKey || !this.postFit) {
          this.postFitKey = this.curKey;
          this.postFit = fitRandles(this.curSpec).params;
        }
        current = this.postFit;
      } else {
        current = baselineP;
      }
    }
    const baseline = baselineDone ? baselineP : null;

    const ro = readout(current);
    const roBase = readout(baselineP);
    const haveBase = baseline !== null;
    const deltaRctPct = haveBase ? deltaPct(current.rct, baselineP.rct) : null;
    const isResult = phase === 'result';
    const detected = isResult ? isDetected(deltaRctPct) : null;

    return {
      phase,
      phaseProgress,
      cfuPerMl: this.cfu,
      coverage: theta,
      baseline,
      current,
      baselineSpectrum: baselineDone ? this.baseSpec : null,
      currentSpectrum,
      zMag: ro.mag,
      zPhase: ro.phase,
      deltaZMagPct: haveBase ? deltaPct(ro.mag, roBase.mag) : null,
      deltaRctPct,
      deltaCdlPct: haveBase ? deltaPct(current.cdl, baselineP.cdl) : null,
      detected,
      estimatedCfuPerMl: isResult && detected ? estimateCfu(deltaRctPct) : null,
      stale: false,
    };
  }
}

const ASSAY_ORDER: AssayPhase[] = ['idle', 'baseline', 'exposure', 'binding', 'measure2', 'compare', 'result'];
