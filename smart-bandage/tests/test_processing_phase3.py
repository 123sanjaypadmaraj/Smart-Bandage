"""
Phase 3 tests — filtering, outlier rejection, quality scoring, calibration
lookup, and the full ChannelPipeline wired together against the Phase 2
simulator.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from common.schemas.calibration import CalibrationParameters
from common.schemas.measurement import RawMeasurement
from processing.biomarkers.pipeline import ChannelPipeline, validate_raw
from processing.calibration.calibration import CalibrationStore
from processing.filtering.filtering import (
    ExponentialSmoothingFilter,
    MovingAverageFilter,
    OutlierRejector,
)
from processing.quality.quality import estimate_signal_quality
from simulator.sensors.scenario_sensor import ScenarioSensor


def _raw(signal: float, temperature: float = 36.7, battery: int = 87) -> RawMeasurement:
    return RawMeasurement(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime.now(timezone.utc),
        raw_signal=signal,
        temperature=temperature,
        battery=battery,
    )


# ---- filtering ----


def test_moving_average_smooths_and_reports_readiness():
    f = MovingAverageFilter(window=3)
    assert f.ready is False
    assert f.update(10) == 10
    assert f.update(20) == 15
    assert f.ready is False
    assert f.update(30) == 20
    assert f.ready is True


def test_exponential_smoothing_converges_toward_input():
    f = ExponentialSmoothingFilter(alpha=0.5)
    v1 = f.update(100)
    v2 = f.update(200)
    assert v1 == 100
    assert 100 < v2 < 200


def test_outlier_rejector_flags_spike_without_polluting_baseline():
    rej = OutlierRejector(window=10, z_threshold=3.0, min_samples=5)
    for _ in range(6):
        assert rej.check(100 + (_ % 2)) is False  # settle in near 100/101
    assert rej.check(500) is True  # a wild spike is flagged
    # baseline stats should still reflect ~100, not have absorbed the 500
    assert rej.check(101) is False


# ---- quality ----


def test_quality_score_penalizes_outliers_and_low_device_quality():
    good = estimate_signal_quality(is_outlier=False, filter_ready=True)
    bad = estimate_signal_quality(is_outlier=True, filter_ready=True, device_signal_quality=0.5)
    assert good == 1.0
    assert bad < good


# ---- calibration store ----


def test_calibration_store_picks_active_curve():
    store = CalibrationStore()
    store.register(
        CalibrationParameters(
            sensor_type="pathogen_channel_1",
            version="1.0",
            model_type="linear",
            slope=1.0,
            intercept=0.0,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 30),
        )
    )
    store.register(
        CalibrationParameters(
            sensor_type="pathogen_channel_1",
            version="2.0",
            model_type="linear",
            slope=2.0,
            intercept=1.0,
            valid_from=date(2026, 7, 1),
        )
    )
    active = store.active("pathogen_channel_1", at=date(2026, 8, 30))
    assert active.version == "2.0"
    assert active.apply(10) == pytest.approx(21.0)


def test_calibration_store_raises_when_nothing_active():
    store = CalibrationStore()
    with pytest.raises(KeyError):
        store.active("unknown_sensor")


# ---- validation ----


def test_validate_raw_rejects_implausible_temperature():
    bad = _raw(100.0, temperature=90.0)
    is_valid, reason = validate_raw(bad)
    assert is_valid is False
    assert reason is not None


def test_validate_raw_accepts_normal_reading():
    ok = _raw(100.0)
    is_valid, _ = validate_raw(ok)
    assert is_valid is True


# ---- full pipeline ----


def test_pipeline_produces_valid_records_for_normal_scenario():
    calibration = CalibrationParameters(
        sensor_type="pathogen_channel_1",
        version="1.0",
        model_type="linear",
        slope=1.0,
        intercept=0.0,
        valid_from=date(2026, 1, 1),
    )
    pipeline = ChannelPipeline(calibration=calibration, unit="ng/mL")
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="normal")
    sensor.initialize()
    sensor.start_measurement()

    records = []
    for _ in range(15):
        raw = sensor.read_measurement()
        status = sensor.get_status()
        records.append(pipeline.process(raw, device_signal_quality=status.signal_quality))

    assert all(r.status in ("valid", "invalid") for r in records)
    assert all(r.estimated_value is not None for r in records)
    assert all(r.unit == "ng/mL" for r in records)
    # by the end the moving-average filter has enough history to be "ready"
    assert records[-1].signal_quality is not None and records[-1].signal_quality > 0.5


def test_pipeline_flags_sudden_abnormal_spike_as_invalid():
    pipeline = ChannelPipeline()
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="normal")
    sensor.initialize()
    sensor.start_measurement()

    # establish a stable baseline first, same as a real deployment would
    # have months of normal readings before a genuine spike occurs
    for _ in range(10):
        pipeline.process(sensor.read_measurement())

    sensor.set_scenario("sudden_abnormal")
    statuses = [pipeline.process(sensor.read_measurement()).status for _ in range(6)]

    assert "invalid" in statuses  # the spike gets caught by outlier rejection


def test_pipeline_marks_out_of_range_reading_as_error():
    pipeline = ChannelPipeline()
    bad = _raw(100.0, temperature=999.0)
    record = pipeline.process(bad)
    assert record.status == "error"
    assert record.processed_signal is None
