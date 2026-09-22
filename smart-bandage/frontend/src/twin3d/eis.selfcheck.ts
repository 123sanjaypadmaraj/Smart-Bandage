/**
 * Self-check for eis.ts. Run with:  node --experimental-strip-types src/twin3d/eis.selfcheck.ts
 * (or `npx tsx src/twin3d/eis.selfcheck.ts`). Exits non-zero on failure.
 */
import { AssayController, CALIBRATION, DEFAULTS, coverage, effectiveLodCfu, estimateCfu, impedance, lodCfu, paramsAtCoverage, spectrum, logSweep } from './eis.ts';

let failures = 0;
function check(name: string, ok: boolean, extra = ''): void {
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  (' + extra + ')' : ''}`);
}

const p = DEFAULTS.params;

const zHi = impedance(1e6, p);
check('Z(1 MHz) ~ Rs', Math.abs(zHi.re - p.rs) / p.rs < 0.01 && Math.abs(zHi.im) < 2, `Z=${zHi.re.toFixed(2)}${zHi.im.toFixed(2)}j`);

const zDc = impedance(1e-9, { ...p, sigma: 0 });
check('sigma=0 dc limit ~ Rs+Rct', Math.abs(zDc.re - (p.rs + p.rct)) < 1 && Math.abs(zDc.im) < 5, `Zdc=${zDc.re.toFixed(2)}`);

check('logSweep has 61 points 0.1..1e5', logSweep().length === 61 && Math.abs(logSweep()[60] - 1e5) < 1e-6);
check('noise=0 spectrum is deterministic', JSON.stringify(spectrum(p)) === JSON.stringify(spectrum(p)));
check('noisy spectrum is seeded-deterministic', JSON.stringify(spectrum(p, logSweep(), 0.01, 7)) === JSON.stringify(spectrum(p, logSweep(), 0.01, 7)));

function finalState(cfu: number) {
  const c = new AssayController({ noise: 0 });
  c.start(cfu);
  c.skipTo('result');
  c.tick(100);
  return c.getState();
}

function finalNoisy(cfu: number) {
  const c = new AssayController();
  c.start(cfu);
  c.skipTo('result');
  c.tick(100);
  return c.getState();
}

const s0 = finalState(0);
check('dRct% is 0 at 0 CFU/mL', s0.deltaRctPct === 0, String(s0.deltaRctPct));
check('detection false at 0 CFU/mL', s0.detected === false);

let mono = true;
let prev = -1;
for (const cfu of [0, 10, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8]) {
  const d = finalState(cfu).deltaRctPct ?? -1;
  if (d < prev) mono = false;
  prev = d;
}
check('dRct% monotone in CFU/mL', mono);

const s6 = finalState(1e6);
check('detection true at 1e6 CFU/mL', s6.detected === true, `dRct=${s6.deltaRctPct?.toFixed(1)}%`);
check('estimate present when detected, null when not', s6.estimatedCfuPerMl !== null && s0.estimatedCfuPerMl === null);

const th = coverage(1e4);
check('Langmuir theta(Kd)=0.5', Math.abs(th - 0.5) < 1e-12);
const pp = paramsAtCoverage(p, 1);
check('params at theta=1', Math.abs(pp.rct - p.rct * 2.6) < 1e-9 && Math.abs(pp.cdl - p.cdl * 0.65) < 1e-15);
check('LOD calibration', lodCfu() > 100 && lodCfu() < 1000 && effectiveLodCfu() >= lodCfu(), `LOD=${lodCfu().toFixed(1)} CFU/mL`);

// Cross-check against the Python backend (digital_twin/bandage/impedance.py, noise-free, cfu=1e5 / 0)
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));
const p1e5 = paramsAtCoverage(p, coverage(1e5));
const refs: [string, { re: number; im: number }, number, number][] = [
  ['py Z(cfu=0, 100 Hz)', impedance(100, p), 383.16775091877395, -562.3909943258559],
  ['py Z(cfu=0, 0.1 Hz)', impedance(0.1, p), 2305.5548596504714, -200.98210753915336],
  ['py Z(cfu=1e5, 100 Hz)', impedance(100, p1e5), 413.3511716598844, -887.0152887042626],
  ['py Z(cfu=1e5, 0.1 Hz)', impedance(0.1, p1e5), 5297.251243758228, -321.8843887181298],
  ['py Z(cfu=1e5, 100 kHz)', impedance(1e5, p1e5), 124.5762932822484, -1.6787359951645016],
];
for (const [name, z, re, im] of refs) check(name, near(z.re, re, 1e-9) && near(z.im, im, 1e-9), `${z.re.toFixed(3)} ${z.im.toFixed(3)}j`);
check('calibration matches Calibration.fit_to_model()', near(CALIBRATION.slope, 54.8246883402954, 1e-9) && near(CALIBRATION.intercept, -136.12773025202046, 1e-9));
check('calibration estimate ~ truth in fit range', (() => {
  const e = estimateCfu(160 * coverage(1e4));
  return e !== null && e > 5e3 && e < 2e4;
})());

// Default (1 % noise) mode: fitted dRct% behaves
{
  const t0 = Date.now();
  const ctrl = finalNoisy(0);
  const pos = finalNoisy(1e6);
  console.log(`      noisy control dRct=${ctrl.deltaRctPct?.toFixed(2)}%  1e6 dRct=${pos.deltaRctPct?.toFixed(1)}%  est=${pos.estimatedCfuPerMl?.toExponential(2)}  (${Date.now() - t0} ms incl. 4 LM fits)`);
  check('noisy control not detected', ctrl.detected === false && Math.abs(ctrl.deltaRctPct ?? 99) < CALIBRATION.thresholdPct);
  check('noisy 1e6 CFU/mL detected', pos.detected === true);
  const mid = finalNoisy(1e4);
  check('noisy 1e4 CFU/mL detected with estimate within a decade', mid.detected === true && mid.estimatedCfuPerMl !== null && mid.estimatedCfuPerMl > 1e3 && mid.estimatedCfuPerMl < 1e5, `est=${mid.estimatedCfuPerMl?.toExponential(2)}`);
}
check('estimateCfu below threshold is null', estimateCfu(CALIBRATION.thresholdPct - 0.1) === null);

const c = new AssayController({ noise: 0 });
check('idle before start', c.getState().phase === 'idle');
c.start(1e4);
const seen: string[] = [];
for (let i = 0; i < 2000; i++) {
  c.tick(0.05);
  const ph = c.getState().phase;
  if (seen[seen.length - 1] !== ph) seen.push(ph);
}
check('phase order', seen.join('>') === 'baseline>exposure>binding>measure2>compare>result', seen.join('>'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  throw new Error(`${failures} self-check(s) failed`);
}
console.log('\nall checks passed');
