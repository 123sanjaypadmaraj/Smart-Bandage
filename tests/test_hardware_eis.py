"""Tests for the E. coli EIS dataset (digital_twin/bandage/eis_dataset.py) and the
emulated hardware analyzer that replays it (backend/app/hardware_eis.py, /api/hardware/eis, /ws/hardware/eis)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///" + (Path(tempfile.mkdtemp(prefix="sb_hweis_")) / "t.db").as_posix())

import pytest
from fastapi.testclient import TestClient

from backend.app import hardware_eis
from backend.app.main import app
from digital_twin.bandage.eis_dataset import Spec, generate, load


def _runs(**match):
    return [r for r in load().runs.values() if all(r[k] == v for k, v in match.items())]


def test_dataset_covers_controls_and_concentrations():
    classes = {r["sample_class"] for r in load().runs.values()}
    assert classes == {"target", "nonspecific", "blank"}
    assert {r["cfu_per_ml"] for r in _runs(sample_class="target")} == {1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7}
    assert {r["split"] for r in load().runs.values()} == {"train", "test"}


def test_dataset_is_physically_consistent():
    for r in load().runs.values():
        assert len(load().sweep(r["run_id"], "baseline")) == 61
        assert len(load().sweep(r["run_id"], "post")) == 61
    mean = lambda rs, k: sum(r[k] for r in rs) / len(rs)  # noqa: E731
    lo, hi = _runs(sample_class="target", cfu_per_ml=1e3), _runs(sample_class="target", cfu_per_ml=1e5)
    assert mean(hi, "d_rct_pct") > mean(lo, "d_rct_pct") > 5.0
    assert mean(hi, "d_cdl_pct") < 0  # bacteria thicken the dielectric layer
    assert abs(mean(_runs(sample_class="blank"), "d_rct_pct")) < 3.0
    assert not any(r["detected"] for r in _runs(sample_class="blank"))
    assert not any(r["detected"] for r in _runs(sample_class="nonspecific"))


def test_generate_is_deterministic(tmp_path):
    spec = Spec(target_replicates=1, blank_replicates=1, nonspecific_replicates=1, per_decade=3)
    generate(tmp_path / "a", spec)
    generate(tmp_path / "b", spec)
    for name in ("runs.csv", "spectra.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_capture_follows_assay_protocol_and_decides_from_counts():
    run = _runs(sample_class="target", cfu_per_ml=1e5)[0]
    frames = hardware_eis.capture(run["run_id"])
    phases = [f["phase"] for f in frames if f["type"] == "phase"]
    assert phases == ["idle", "baseline", "exposure", "binding", "measure2", "compare"]
    assert [f["type"] for f in frames].count("point") == 122 and frames[-1]["type"] == "result"
    pt = next(f for f in frames if f["type"] == "point")
    assert isinstance(pt["raw_real"], int) and abs(pt["re_ohm"] - pt["raw_real"] * hardware_eis.OHM_PER_COUNT) < 1e-9
    ts = [f["t_s"] for f in frames]
    assert ts == sorted(ts)
    res = frames[-1]
    assert res["detection"]["detected"] and res["truth"]["correct"]
    # the call comes from the fit of the received counts, close to the dataset's own fit
    assert res["delta"]["rct_pct"] == pytest.approx(run["d_rct_pct"], abs=3.0)
    assert res["detection"]["estimated_cfu"] == pytest.approx(1e5, rel=1.0)


def test_capture_blank_is_not_detected():
    res = hardware_eis.capture(_runs(sample_class="blank")[0]["run_id"])[-1]
    assert not res["detection"]["detected"] and res["detection"]["estimated_cfu"] is None
    assert res["truth"]["correct"]


def test_capture_synthesised_run_scales_with_concentration():
    lo = hardware_eis.capture(sample_class="target", cfu=1e3, seed=5)[-1]["delta"]["rct_pct"]
    hi = hardware_eis.capture(sample_class="target", cfu=1e5, seed=5)[-1]["delta"]["rct_pct"]
    assert hi > lo + 50.0


def test_rest_endpoints():
    with TestClient(app) as c:
        assert len(c.get("/api/hardware/eis/samples").json()) == 84
        assert {s["sample_class"] for s in c.get("/api/hardware/eis/samples?sample_class=blank").json()} == {"blank"}
        assert c.get("/api/hardware/eis/samples?sample_class=bogus").status_code == 422
        assert c.get("/api/hardware/eis/capture?run_id=nope").status_code == 404
        frames = c.get("/api/hardware/eis/capture?sample_class=target&cfu=10000&seed=1").json()["frames"]
        assert frames[-1]["type"] == "result"


def test_websocket_streams_frames_in_order():
    run_id = _runs(sample_class="target", cfu_per_ml=1e4)[0]["run_id"]
    with TestClient(app) as c, c.websocket_connect(f"/ws/hardware/eis?run_id={run_id}&point_interval_s=0") as ws:
        got = []
        while True:
            try:
                got.append(ws.receive_json())
            except Exception:
                break
    assert got[0] == {"type": "phase", "phase": "idle", "t_s": 0.0}
    assert got[-1]["type"] == "result" and got[-1]["truth"]["run_id"] == run_id
