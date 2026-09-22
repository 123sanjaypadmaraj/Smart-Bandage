"""Parity: the browser EIS model (frontend/src/twin3d/eis.ts) vs digital_twin/bandage/impedance.py.

Runs frontend/src/twin3d/eis.dump.ts under Node (type stripping, no build step)
and compares noise-free spectra, sensor parameters, calibration and detection.
Skipped with a reason if Node is missing or too old to strip types.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from digital_twin.bandage.impedance import (
    REF_FREQ_HZ,
    BindingModel,
    Calibration,
    EisAssay,
    EisParams,
    coverage,
    impedance,
    log_sweep,
    params_at_coverage,
    spectrum,
)

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
DUMP = FRONTEND / "src" / "twin3d" / "eis.dump.ts"
REL = 1e-6
CFUS = [0, 1e2, 1e3, 1e4, 1e5, 1e6, 1e8]


def _node_or_skip() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH: cannot run the TypeScript EIS model")
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    m = re.match(r"v(\d+)\.(\d+)", out)
    major, minor = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if (major, minor) < (22, 6):
        pytest.skip(f"node {out or '?'} too old for --experimental-strip-types (need >= 22.6)")
    return node


@pytest.fixture(scope="module")
def ts():
    node = _node_or_skip()
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", str(DUMP)],
        capture_output=True, text=True, cwd=FRONTEND, timeout=120,
    )
    assert proc.returncode == 0, f"eis.dump.ts failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def close(a: float, b: float, rel: float = REL, abs_: float = 0.0) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + abs_


def test_frequency_grid(ts):
    py = log_sweep()
    assert len(ts["freqs"]) == len(py) == 61
    for a, b in zip(ts["freqs"], py):
        assert close(a, b, 1e-12)


def test_baseline_params_and_spectrum(ts):
    base = EisParams()
    for k, v in base.to_dict().items():
        assert close(ts["base"][k], v, 1e-12), k
    _compare_spectrum(ts["baselineSpectrum"], spectrum(log_sweep(), base), "baseline")


def _compare_spectrum(tsp, pyp, label):
    assert len(tsp) == len(pyp)
    for a, b in zip(tsp, pyp):
        for tk, pk in (("re", "re"), ("im", "im"), ("mag", "mag")):
            assert close(a[tk], b[pk]), f"{label} f={b['freq']:.4g} {tk}: ts={a[tk]!r} py={b[pk]!r}"
        assert close(a["phase"], b["phase_deg"], REL, 1e-9), f"{label} f={b['freq']:.4g} phase"


@pytest.mark.parametrize("idx", range(len(CFUS)))
def test_params_and_spectrum_per_cfu(ts, idx):
    case = ts["cases"][idx]
    cfu = CFUS[idx]
    assert case["cfu"] == cfu
    theta = coverage(cfu)
    assert close(case["theta"], theta, 1e-12, 1e-15)
    p = params_at_coverage(EisParams(), theta, BindingModel())
    for k, v in p.to_dict().items():
        assert close(case["params"][k], v, 1e-12), f"cfu={cfu} {k}"
    _compare_spectrum(case["spectrum"], spectrum(log_sweep(), p), f"cfu={cfu}")


@pytest.mark.parametrize("idx", range(len(CFUS)))
def test_reference_readout_and_deltas(ts, idx):
    case = ts["cases"][idx]
    base, theta = EisParams(), coverage(CFUS[idx])
    p = params_at_coverage(base, theta)
    z0, z1 = impedance(REF_FREQ_HZ, base), impedance(REF_FREQ_HZ, p)
    assert close(case["zMag"], abs(z1))
    assert close(case["dZMagPct"], (abs(z1) - abs(z0)) / abs(z0) * 100.0, REL, 1e-9)
    assert close(case["dRctPct"], (p.rct - base.rct) / base.rct * 100.0, REL, 1e-9)
    assert close(case["dCdlPct"], (p.cdl - base.cdl) / base.cdl * 100.0, REL, 1e-9)


def test_calibration_and_lod(ts):
    """TS calibrates against the model itself, i.e. Calibration.fit_to_model()."""
    cal = Calibration.fit_to_model()
    c = ts["calibration"]
    assert close(c["slope"], cal.slope, REL)
    assert close(c["intercept"], cal.intercept, REL)
    assert close(c["noiseSdPct"], cal.noise_sd_pct, 1e-12)
    assert close(c["kSigma"], cal.k_sigma, 1e-12)
    assert close(c["thresholdPct"], cal.threshold_pct, 1e-12)
    assert close(ts["lodCfu"], cal.lod_cfu(), REL)
    # physical LOD: Langmuir response reaches the threshold
    theta = cal.threshold_pct / (100.0 * BindingModel().a_rct)
    assert close(ts["physicalLodCfu"], theta * BindingModel().kd / (1.0 - theta), REL)
    assert close(ts["effectiveLodCfu"], max(cal.lod_cfu(), ts["physicalLodCfu"]), REL)


@pytest.mark.parametrize("idx", range(len(CFUS)))
def test_detection_matches_python_assay(ts, idx):
    """Noise-free full assay in Python (with the fitted calibration) vs the TS sequencer."""
    cfu = CFUS[idx]
    case = ts["cases"][idx]
    cal = Calibration.fit_to_model()
    res = EisAssay(calibration=cal).run(cfu, seed=1, noise_pct=0.0)
    # Python d_rct comes from an LM fit of exact data: agrees with truth to fit tolerance
    assert case["dRctPct"] == pytest.approx(res.d_rct_pct, abs=1e-3, rel=1e-6)
    assert case["detected"] is res.detected
    assert case["detected"] is (case["dRctPct"] > cal.threshold_pct)
    py_est = cal.estimate_cfu(case["dRctPct"])
    if py_est is None:
        assert case["estimatedCfu"] is None
    else:
        # TS clamps to the calibrated range 10..1e8 CFU/mL
        assert case["estimatedCfu"] == pytest.approx(min(max(py_est, 10.0), 1e8), rel=1e-6)
