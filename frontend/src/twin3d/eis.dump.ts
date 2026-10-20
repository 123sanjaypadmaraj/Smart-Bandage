/**
 * Dumps the browser EIS model as JSON for the Python parity test
 * (tests/test_eis_ts_parity.py). Run with:
 *   node --experimental-strip-types src/twin3d/eis.dump.ts
 * Noise is 0 everywhere, so the output is fully deterministic.
 */
import {
  AssayController, CALIBRATION, DEFAULTS, coverage, effectiveLodCfu, estimateCfu, lodCfu,
  logSweep, paramsAtCoverage, physicalLodCfu, spectrum,
} from './eis.ts';

const CFUS = [0, 1e2, 1e3, 1e4, 1e5, 1e6, 1e8];
const freqs = logSweep();
const base = DEFAULTS.params;

const cases = CFUS.map((cfu) => {
  const theta = coverage(cfu);
  const params = paramsAtCoverage(base, theta);
  const ctrl = new AssayController({ noise: 0 });
  ctrl.start(cfu);
  ctrl.skipTo('result');
  ctrl.tick(100);
  const st = ctrl.getState();
  return {
    cfu,
    theta,
    params,
    spectrum: spectrum(params, freqs, 0),
    dRctPct: st.deltaRctPct,
    dCdlPct: st.deltaCdlPct,
    dZMagPct: st.deltaZMagPct,
    zMag: st.zMag,
    zPhase: st.zPhase,
    detected: st.detected,
    estimatedCfu: st.estimatedCfuPerMl,
    estimateFromDrct: estimateCfu(st.deltaRctPct),
  };
});

console.log(JSON.stringify({
  freqs,
  base,
  baselineSpectrum: spectrum(base, freqs, 0),
  calibration: { ...CALIBRATION },
  lodCfu: lodCfu(),
  physicalLodCfu: physicalLodCfu(),
  effectiveLodCfu: effectiveLodCfu(),
  cases,
}));
