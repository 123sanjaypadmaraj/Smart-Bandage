"""Tests for digital_twin/bandage/impedance.py (EIS biosensor model), eis_adapter.py and GET /api/twin/eis."""
import json
import math
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///" + (Path(tempfile.mkdtemp(prefix="sb_eis_")) / "t.db").as_posix())
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi.testclient import TestClient  # noqa: E402

from digital_twin.bandage import (  # noqa: E402
    BandageTwin,
    BindingModel,
    Calibration,
    EisAssay,
    EisParams,
    binding_kinetics,
    coverage,
    eis_from_twin,
    fit_randles,
    impedance,
    log_sweep,
    params_at_coverage,
    spectrum,
)

BASE = EisParams()
PARAM_KEYS = ("rs", "rct", "cdl", "sigma", "n")


# ---------------------------------------------------------------- circuit physics
def test_high_frequency_limit_is_rs():
    z = impedance(1e9, BASE)
    assert z.real == pytest.approx(BASE.rs, rel=1e-3)
    assert abs(z.imag) < 0.01 * BASE.rs


def test_dc_limit_finite_only_without_warburg():
    p = EisParams(sigma=0.0)
    assert impedance(1e-7, p).real == pytest.approx(p.rs + p.rct, rel=1e-4)
    # with diffusion the real part keeps rising as f falls
    assert impedance(1e-3, BASE).real > impedance(1e-1, BASE).real > impedance(10, BASE).real


def test_low_frequency_real_part_rises_with_rct_and_rs():
    lo = impedance(0.1, BASE).real
    assert lo > BASE.rs + 0.3 * BASE.rct
    assert impedance(0.1, EisParams(rct=4000)).real > lo
    assert impedance(0.1, EisParams(rs=300)).real > lo


def test_magnitude_monotone_in_rct():
    mags = [abs(impedance(10.0, EisParams(rct=r))) for r in (500, 1000, 2000, 4000, 8000)]
    assert mags == sorted(mags) and len(set(mags)) == 5


def test_nyquist_semicircle_diameter_is_rct_for_small_sigma():
    p = EisParams(sigma=0.5, n=1.0)
    freqs = log_sweep(1e-2, 1e7, 40)
    re = [impedance(f, p).real for f in freqs]
    assert min(re) == pytest.approx(p.rs, rel=0.01)
    assert max(re) - p.rs == pytest.approx(p.rct, rel=0.05)


def test_phase_bounds_and_sign_convention():
    for pt in spectrum(log_sweep(), BASE):
        assert -90.0 <= pt["phase_deg"] <= 0.0
        assert pt["im"] <= 0.0
        assert pt["mag"] == pytest.approx(math.hypot(pt["re"], pt["im"]))


def test_cdl_q_roundtrip_and_n_one():
    from digital_twin.bandage.impedance import cdl_from_q

    assert EisParams(n=1.0).q == pytest.approx(2.0e-6)
    assert cdl_from_q(BASE.q, BASE.rs, BASE.rct, BASE.n) == pytest.approx(BASE.cdl, rel=1e-12)


def test_log_sweep_grid():
    f = log_sweep()
    assert len(f) == 61 and f[0] == pytest.approx(0.1) and f[-1] == pytest.approx(1e5)
    assert 100.0 in f


def test_param_validation():
    with pytest.raises(ValueError):
        EisParams(rct=-1)
    with pytest.raises(ValueError):
        EisParams(n=1.5)
    with pytest.raises(ValueError):
        coverage(-1)


# ---------------------------------------------------------------- binding
def test_coverage_monotone_and_zero_at_zero():
    assert coverage(0, 1e4) == 0.0
    vals = [coverage(c, 1e4) for c in (1, 1e2, 1e4, 1e6, 1e9)]
    assert vals == sorted(vals) and vals[2] == pytest.approx(0.5) and vals[-1] < 1.0


def test_params_at_coverage():
    m = BindingModel()
    assert params_at_coverage(BASE, 0.0, m) == BASE
    p = params_at_coverage(BASE, 1.0, m)
    assert p.rct == pytest.approx(2000 * 2.6) and p.cdl == pytest.approx(2e-6 * 0.65)
    assert p.rs == pytest.approx(120 * 1.04) and p.sigma == pytest.approx(225.0) and p.n == BASE.n


def test_binding_kinetics_closed_form():
    assert binding_kinetics(0, 1e6) == 0.0
    th = [binding_kinetics(t, 1e4) for t in (0, 1, 10, 100, 1e4)]
    assert th == sorted(th)
    assert th[-1] == pytest.approx(coverage(1e4, 1e4), rel=1e-6)  # k_off/k_on = default Kd
    theta, dt = 0.0, 1e-3  # exact vs fine Euler integration
    for _ in range(20000):
        theta += dt * (1e-6 * 1e4 * (1 - theta) - 1e-2 * theta)
    assert binding_kinetics(20.0, 1e4) == pytest.approx(theta, rel=1e-3)


# ---------------------------------------------------------------- calibration & assay
def test_calibration_lod():
    c = Calibration()
    assert c.threshold_pct == pytest.approx(4.5)
    assert c.lod_cfu() == pytest.approx(10 ** ((4.5 + c.intercept * -1) / c.slope))
    assert c.estimate_cfu(4.5) is None
    assert c.estimate_cfu(c.slope * 3 + c.intercept) == pytest.approx(1e3)


def test_control_drct_zero_without_noise():
    r = EisAssay().run(0.0, seed=1, noise_pct=0.0)
    assert r.theta == 0.0
    assert r.d_rct_pct == pytest.approx(0.0, abs=1e-9)
    assert r.d_z_ref_pct == pytest.approx(0.0, abs=1e-9)
    assert not r.detected and r.estimated_cfu is None


def test_drct_positive_and_monotone_in_cfu():
    a = EisAssay()
    d = [a.run(c, seed=3, noise_pct=0.0).d_rct_pct for c in (1e2, 1e3, 1e4, 1e5, 1e6)]
    assert all(x > 0 for x in d) and d == sorted(d)
    assert d[2] == pytest.approx(80.0, rel=0.02)  # theta = 0.5 -> 1.6 * 0.5
    r = a.run(1e5, seed=3, noise_pct=0.0)
    assert r.d_cdl_pct < 0 and r.d_rs_pct > 0 and r.d_z_ref_pct > 0


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_detection_control_vs_high(seed):
    a = EisAssay()
    assert a.run(0.0, seed=seed, noise_pct=1.0).detected is False
    assert a.run(1e6, seed=seed, noise_pct=1.0).detected is True


def test_fit_recovers_parameters_from_1pct_noise():
    r = EisAssay().run(1e5, seed=7, noise_pct=1.0)
    for fit, true in ((r.baseline_fit, r.baseline_true), (r.post_fit, r.post_true)):
        for k in PARAM_KEYS:
            assert getattr(fit, k) == pytest.approx(getattr(true, k), rel=0.05), k


def test_fit_noise_free_is_exact():
    f = log_sweep()
    fit, rms = fit_randles(f, [impedance(x, BASE) for x in f])
    assert rms < 1e-6
    for k in PARAM_KEYS:
        assert getattr(fit, k) == pytest.approx(getattr(BASE, k), rel=1e-3), k


def test_determinism_and_seed_sensitivity():
    d1 = EisAssay().run(1e4, seed=42, noise_pct=1.5).to_dict()
    d2 = EisAssay().run(1e4, seed=42, noise_pct=1.5).to_dict()
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert d1 != EisAssay().run(1e4, seed=43, noise_pct=1.5).to_dict()
    a = EisAssay()
    assert a.run(1e4, seed=1, noise_pct=0.0).to_dict()["post"] == a.run(1e4, seed=2, noise_pct=0.0).to_dict()["post"]


def test_result_dict_shape_and_json_safe():
    d = EisAssay().run(1e3, seed=0, noise_pct=1.0).to_dict()
    json.dumps(d, allow_nan=False)
    assert [t["phase"] for t in d["timeline"]] == [
        "idle", "baseline", "exposure", "binding", "measure2", "compare", "result",
    ]
    assert len(d["baseline"]["spectrum"]) == len(d["freqs"]) == 61
    assert set(d["baseline"]["spectrum"][0]) == {"freq", "re", "im", "mag", "phase_deg"}
    assert {"detected", "threshold_pct", "estimated_cfu", "lod_cfu"} <= set(d["detection"])


def test_incubation_kinetics_lowers_coverage():
    a = EisAssay()
    short = a.run(1e3, seed=0, noise_pct=0.0, incubation_s=5.0)
    eq = a.run(1e3, seed=0, noise_pct=0.0)
    assert 0 < short.theta < eq.theta


# ---------------------------------------------------------------- twin adapter
def test_eis_from_twin():
    twin = BandageTwin(seed=5)
    twin.advance(60.0)
    r = eis_from_twin(twin, "CH-01")
    assert 0.0 <= r.theta <= 1.0 and r.params.rct >= BASE.rct
    assert r.d_rct_pct == pytest.approx(100 * 1.6 * r.theta, rel=1e-9)
    json.dumps(r.to_dict(), allow_nan=False)
    assert eis_from_twin(twin, "CH-01").to_dict() == r.to_dict()
    with pytest.raises(ValueError):
        eis_from_twin(twin, "CH-02")
    with pytest.raises(KeyError):
        eis_from_twin(twin, "nope")


# ---------------------------------------------------------------- API
@pytest.fixture()
def client():
    from backend.app.main import app

    return TestClient(app)  # no lifespan: the route touches no DB


def test_api_eis_shape(client):
    q = {"cfu": 1e5, "seed": 3, "noise_pct": 1.0}
    resp = client.get("/api/twin/eis", params=q)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    for key in ("baseline", "post", "delta", "detection", "calibration", "freqs", "timeline", "theta"):
        assert key in d
    assert len(d["post"]["spectrum"]) == len(d["freqs"])
    assert d["detection"]["detected"] is True and d["delta"]["rct_pct"] > 4.5
    assert client.get("/api/twin/eis", params=q).json() == d


def test_api_eis_defaults_control(client):
    d = client.get("/api/twin/eis").json()
    assert d["cfu"] == 0.0 and d["detection"]["detected"] is False


@pytest.mark.parametrize("q", [{"cfu": -1}, {"cfu": 2e9}, {"noise_pct": -1}, {"seed": -5}, {"cfu": "abc"}])
def test_api_eis_validation_422(client, q):
    assert client.get("/api/twin/eis", params=q).status_code == 422
