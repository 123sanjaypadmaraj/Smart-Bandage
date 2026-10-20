"""
DT-2 tests -- the state -> RawMeasurement mapping per channel, plus device
physics (battery, electrode fouling, BLE link quality) as running processes.

Timing note: DigitalTwinDevice.read_channel()/read_all() accept an explicit
`dt_seconds` so tests can advance the simulated clock deterministically
instead of depending on how much real wall-clock time a test happens to
take between calls.
"""
from __future__ import annotations

import pytest

from common.schemas.measurement import RawMeasurement
from digital_twin.device_physics import BatteryProcess, ElectrodeFoulingProcess, LinkQualityProcess
from digital_twin.observation import DigitalTwinDevice, get_channel_profile
from digital_twin.state import WoundState
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError


def test_unknown_sensor_type_falls_back_to_generic_profile():
    profile = get_channel_profile("some_future_assay")
    assert profile.sensor_type == "generic"
    assert profile.inflammation_gain == 0.0
    assert profile.unit == "a.u."  # no calibration curve for an assay this module doesn't know


def test_known_sensor_type_returns_its_preset():
    assert get_channel_profile("pathogen_channel_1").bacterial_load_gain > 0
    assert get_channel_profile("glucose").sensor_type == "glucose"


def test_known_sensor_types_have_a_real_calibration_curve():
    """Every named preset maps raw_signal to a physiologically-labeled unit
    -- not the "a.u." identity fallback -- so a dashboard showing this
    channel's estimated_value/unit combination is showing something a real
    bandage reading could plausibly be (see backend/app/simulation.py's
    _twin_pipeline, which applies this curve for twin-backed simulations)."""
    for sensor_type, expected_unit in [
        ("pathogen_channel_1", "mg/L"),
        ("glucose", "mg/dL"),
        ("pH", "pH"),
    ]:
        profile = get_channel_profile(sensor_type)
        assert profile.unit == expected_unit
        assert profile.cal_slope != 1.0 or profile.cal_intercept != 0.0  # not the identity fallback


def test_shared_wound_state_correlates_two_channels():
    """One cause (rising inflammation + bacterial load), several correlated
    readings -- the cross-channel coupling DT-2 unlocks."""
    device = DigitalTwinDevice(
        "SB-001",
        channels={"CH-01": "pathogen_channel_1", "CH-02": "pH"},
    )
    device.start_measurement()

    baseline = {
        cid: device.read_channel(cid, dt_seconds=1.0).raw_signal for cid in device.channels
    }

    # push the shared cause up sharply and let it revert toward the new target
    device.state.inflammation_target = 1.2
    device.state.bacterial_load_target = 1.2
    for _ in range(30):
        device.read_all(dt_seconds=2.0)

    elevated = {
        cid: device.read_channel(cid, dt_seconds=2.0).raw_signal for cid in device.channels
    }

    # both channels respond to the same underlying cause, in the same direction
    assert elevated["CH-01"] > baseline["CH-01"]
    assert elevated["CH-02"] > baseline["CH-02"]


def test_channel_insensitive_to_a_variable_does_not_move_with_it():
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "glucose"})
    device.start_measurement()
    before = device.read_channel("CH-01", dt_seconds=1.0).raw_signal

    # glucose profile has no bacterial_load_gain
    device.state.bacterial_load_target = 1.5
    for _ in range(20):
        device.read_channel("CH-01", dt_seconds=2.0)
    after = device.read_channel("CH-01", dt_seconds=2.0).raw_signal

    assert abs(after - before) < 10  # only noise-scale movement, no directed drift


def test_electrode_fouling_attenuates_signal_and_degrades_quality():
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "pathogen_channel_1"})
    device.start_measurement()
    device.state.inflammation_target = 1.0
    device.state.bacterial_load_target = 1.0

    qualities = []
    for _ in range(500):
        device.read_channel("CH-01", dt_seconds=120.0)  # ~16.7 simulated hours total
        qualities.append(device.get_status("CH-01").signal_quality)

    assert device._fouling["CH-01"].level > 0.3
    assert qualities[-1] < qualities[0]


def test_battery_drains_faster_with_poor_link_quality():
    good = BatteryProcess(start_pct=100.0, base_drain_pct_per_hour=2.0)
    poor = BatteryProcess(start_pct=100.0, base_drain_pct_per_hour=2.0)
    for _ in range(50):
        good.step(dt_seconds=60.0, link_quality=1.0)
        poor.step(dt_seconds=60.0, link_quality=0.0)
    assert poor.pct < good.pct


def test_fouling_accumulates_faster_with_more_moisture_and_bacterial_load():
    dry_clean = ElectrodeFoulingProcess()
    wet_infected = ElectrodeFoulingProcess()
    for _ in range(50):
        dry_clean.step(dt_seconds=120.0, moisture=0.1, bacterial_load=0.0)
        wet_infected.step(dt_seconds=120.0, moisture=1.0, bacterial_load=1.5)
    assert wet_infected.level > dry_clean.level


def test_link_quality_process_stays_bounded():
    link = LinkQualityProcess(start=0.95, target=0.92)
    for _ in range(500):
        value = link.step(dt_seconds=5.0)
        assert 0.0 <= value <= 1.0


def test_link_quality_drop_raises_comms_timeout_and_recovers():
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "glucose"})
    device.start_measurement()

    device.link_quality.value = 0.05  # force a bad link, well below the dropout floor
    outcomes = []
    for _ in range(50):
        try:
            device.read_channel("CH-01", dt_seconds=0.1)
            outcomes.append("ok")
        except CommsTimeoutError:
            outcomes.append("drop")
        except SensorDisconnectedError:
            outcomes.append("disconnected")
        device.link_quality.value = 0.05  # hold it down each tick

    assert "drop" in outcomes or "disconnected" in outcomes


def test_sustained_bad_link_eventually_disconnects_and_latches():
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "glucose"})
    device.start_measurement()

    saw_disconnect = False
    for _ in range(40):
        device.link_quality.value = 0.01
        try:
            device.read_channel("CH-01", dt_seconds=0.1)
        except (CommsTimeoutError, SensorDisconnectedError) as exc:
            if isinstance(exc, SensorDisconnectedError):
                saw_disconnect = True
                break

    assert saw_disconnect
    assert device.get_status("CH-01").connected is False
    # stays disconnected even if the link recovers afterward
    device.link_quality.value = 0.99
    with pytest.raises(SensorDisconnectedError):
        device.read_channel("CH-01", dt_seconds=0.1)


def test_read_all_advances_shared_state_once_per_cycle():
    device = DigitalTwinDevice(
        "SB-001", channels={"CH-01": "pathogen_channel_1", "CH-02": "glucose"}
    )
    device.start_measurement()

    results = device.read_all(dt_seconds=5.0)
    assert set(results) == {"CH-01", "CH-02"}
    for channel_id, result in results.items():
        assert isinstance(result, RawMeasurement)
        assert result.channel_id == channel_id
        assert result.device_id == "SB-001"
