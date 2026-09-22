import { useEffect, useState } from 'react';
import { BACKEND_TOL, checkAgainstBackend } from './backendCheck.ts';
import type { BackendCheckResult } from './backendCheck.ts';

const fmtErr = (v: number): string => (v === 0 ? '0' : v.toExponential(1));

/** Button that compares the browser EIS model with the Python backend twin (GET /api/twin/eis) at the given CFU/mL. */
export function BackendCheck({ cfu, apiBase }: { cfu: number; apiBase?: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BackendCheckResult | null>(null);

  // a result belongs to one concentration: drop it when the sample changes
  useEffect(() => { setResult(null); }, [cfu]);

  const run = async () => {
    setBusy(true);
    try {
      setResult(await checkAgainstBackend(cfu, apiBase));
    } catch (e) {
      setResult({ ok: false, maxRelErr: NaN, cfu, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const unreachable = result !== null && result.backend === undefined;
  const rows: [string, number, number, number][] =
    result?.backend && result.browser && result.paramRelErr
      ? [
          ['Rs (ohm)', result.browser.paramsTrue.rs, result.backend.paramsTrue.rs, result.paramRelErr.rs],
          ['Rct (ohm)', result.browser.paramsTrue.rct, result.backend.paramsTrue.rct, result.paramRelErr.rct],
          ['Cdl (F)', result.browser.paramsTrue.cdl, result.backend.paramsTrue.cdl, result.paramRelErr.cdl],
          ['sigma (ohm s^-1/2)', result.browser.paramsTrue.sigma, result.backend.paramsTrue.sigma, result.paramRelErr.sigma],
        ]
      : [];

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 text-xs dark:border-white/10 dark:bg-white/[0.03]" aria-label="Backend cross-check">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={run}
          disabled={busy}
          className="rounded-lg border border-slate-300 px-3 py-1.5 font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-60 dark:border-white/15 dark:text-slate-100 dark:hover:bg-white/10"
        >
          {busy ? 'Checking...' : 'Verify against backend twin'}
        </button>
        <span className="text-slate-500 dark:text-slate-400">{cfu.toExponential(1)} CFU/mL, noise-free</span>
        {result && !unreachable && (
          <span className={result.ok ? 'font-semibold text-emerald-600 dark:text-emerald-400' : 'font-semibold text-amber-600 dark:text-amber-300'} role="status">
            {result.ok ? 'match' : 'mismatch'}: max rel. error {fmtErr(result.maxRelErr)} (tolerance {fmtErr(BACKEND_TOL)})
          </span>
        )}
      </div>
      {unreachable && result && (
        <p className="mt-2 text-slate-600 dark:text-slate-300" role="status">
          backend not reachable - browser model only
          {result.error ? <span className="text-slate-400"> ({result.error})</span> : null}
        </p>
      )}
      {rows.length > 0 && result?.backend && result.browser && (
        <table className="mt-3 w-full font-mono text-[11px] text-slate-700 dark:text-slate-200">
          <thead>
            <tr className="text-left text-slate-500 dark:text-slate-400"><th>parameter</th><th>browser</th><th>python</th><th>rel. delta</th></tr>
          </thead>
          <tbody>
            {rows.map(([name, b, p, e]) => (
              <tr key={name}><td>{name}</td><td>{b.toPrecision(6)}</td><td>{p.toPrecision(6)}</td><td>{fmtErr(e)}</td></tr>
            ))}
            <tr>
              <td>detected</td><td>{String(result.browser.detected)}</td><td>{String(result.backend.detected)}</td>
              <td>{result.browser.detected === result.backend.detected ? '-' : 'differs'}</td>
            </tr>
          </tbody>
        </table>
      )}
    </section>
  );
}
