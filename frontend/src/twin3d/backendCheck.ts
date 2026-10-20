/**
 * Cross-check of the browser EIS model against the Python backend twin
 * (GET /api/twin/eis, public route). Compares the noise-free post-binding sensor
 * parameters and spectrum. Never throws: failures come back as { ok: false, error }.
 */
import { API_BASE } from '../api';
import { DEFAULTS, coverage, estimateCfu, logSweep, paramsAtCoverage, spectrum } from './eis.ts';
import type { EisParams } from './types.ts';

export interface BackendSpectrumPoint { freq: number; re: number; im: number; mag: number; phase_deg: number }

export interface BackendEisFields {
  theta: number;
  paramsTrue: EisParams;
  spectrum: BackendSpectrumPoint[];
  dRctPct: number;
  dCdlPct: number;
  dZRefPct: number;
  detected: boolean;
  estimatedCfu: number | null;
  lodCfu: number;
}

export interface BackendCheckResult {
  ok: boolean;
  /** largest relative difference over params (Rs, Rct, Cdl, sigma, n) and spectrum (Z', Z'', |Z|) */
  maxRelErr: number;
  cfu: number;
  backend?: BackendEisFields;
  /** browser-model values for the same inputs */
  browser?: { paramsTrue: EisParams; theta: number; detected: boolean; estimatedCfu: number | null };
  /** per-parameter relative deltas (browser vs backend) */
  paramRelErr?: Record<'rs' | 'rct' | 'cdl' | 'sigma' | 'n', number>;
  error?: string;
}

/** ok when the two models agree to this relative error. */
export const BACKEND_TOL = 1e-6;

const rel = (a: number, b: number): number => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), 1e-300);
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export async function checkAgainstBackend(cfu: number, apiBase: string = API_BASE): Promise<BackendCheckResult> {
  const c = Math.min(Math.max(Number.isFinite(cfu) ? cfu : 0, 0), 1e9);
  const fail = (error: string): BackendCheckResult => ({ ok: false, maxRelErr: NaN, cfu: c, error });
  try {
    const url = `${apiBase.replace(/\/+$/, '')}/api/twin/eis?cfu=${encodeURIComponent(String(c))}&seed=1&noise_pct=0`;
    const res = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!res.ok) return fail(`backend returned HTTP ${res.status}`);
    const j = await res.json();
    const pp = j?.post?.params_true;
    const sp: BackendSpectrumPoint[] | undefined = j?.post?.spectrum;
    if (!pp || !Array.isArray(sp) || !j.delta || !j.detection) return fail('unexpected backend response shape');

    const backend: BackendEisFields = {
      theta: j.theta,
      paramsTrue: { rs: pp.rs, rct: pp.rct, cdl: pp.cdl, sigma: pp.sigma, n: pp.n },
      spectrum: sp,
      dRctPct: j.delta.rct_pct,
      dCdlPct: j.delta.cdl_pct,
      dZRefPct: j.delta.z_ref_pct,
      detected: !!j.detection.detected,
      estimatedCfu: j.detection.estimated_cfu ?? null,
      lodCfu: j.detection.lod_cfu,
    };
    if (![backend.theta, ...Object.values(backend.paramsTrue)].every(finite)) return fail('backend returned non-numeric parameters');

    const theta = coverage(c);
    const bp = paramsAtCoverage(DEFAULTS.params, theta);
    const paramRelErr = {
      rs: rel(bp.rs, backend.paramsTrue.rs),
      rct: rel(bp.rct, backend.paramsTrue.rct),
      cdl: rel(bp.cdl, backend.paramsTrue.cdl),
      sigma: rel(bp.sigma, backend.paramsTrue.sigma),
      n: rel(bp.n, backend.paramsTrue.n),
    };
    let maxRelErr = Math.max(...Object.values(paramRelErr), rel(theta, backend.theta));
    const freqs = sp.map((p) => p.freq);
    const local = spectrum(bp, freqs.length ? freqs : logSweep(), 0);
    if (local.length !== sp.length) return fail('spectrum length mismatch');
    for (let i = 0; i < sp.length; i++) {
      maxRelErr = Math.max(maxRelErr, rel(local[i].re, sp[i].re), rel(local[i].im, sp[i].im), rel(local[i].mag, sp[i].mag));
    }
    const dRct = ((bp.rct - DEFAULTS.params.rct) / DEFAULTS.params.rct) * 100;
    const est = estimateCfu(dRct);
    const browser = { paramsTrue: bp, theta, detected: est !== null, estimatedCfu: est };
    return { ok: maxRelErr <= BACKEND_TOL, maxRelErr, cfu: c, backend, browser, paramRelErr };
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}
