"""
Phase 2 tests — the 7 scenarios, fault injection, and the multi-channel
sensor, all exercised through the same SensorInterface contract Phase 1
locked down.
"""
from __future__ import annotations

import pytest

from common.schemas.measurement import RawMeasurement
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError
from simulator.scenarios.scenarios import get_scenario, list_scenarios
from simulator.sensors.multi_channel_sensor import MultiChannelSensor
from simulator.sensors.scenario_sensor import ScenarioSensor


def test_all_seven_scenarios_registered():
    names = list_scenarios()
    assert names == sorted(
        [
            "normal",
            "rising_concentration",
            "sudden_abnormal",
            "electrode_degradation",
            "high_noise",
            "sensor_disconnect",
            "comms_failure",
        ]
    )
    for name in names:
        assert get_scenario(name).description


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        get_scenario("not_a_real_scenario")


def test_normal_scenario_produces_stable_readings():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="normal")
    sensor.initialize()
    sensor.start_measurement()
    readings = [sensor.read_measurement() for _ in range(10)]
    values = [r.raw_signal for r in readings]
    assert all(isinstance(r, RawMeasurement) for r in readings)
    assert 80 < min(values) and max(values) < 120
    status = sensor.get_status()
    assert status.connected is True
    assert status.last_error is None


def test_rising_concentration_trends_upward():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="rising_concentration")
    sensor.initialize()
    sensor.start_measurement()
    values = [sensor.read_measurement().raw_signal for _ in range(6)]
    assert values[0] < values[-1]
    assert values[-1] > 150  # heading toward the 170 checkpoint


def test_sudden_abnormal_spikes_after_stable_period():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="sudden_abnormal")
    sensor.initialize()
    sensor.start_measurement()
    values = [sensor.read_measurement().raw_signal for _ in range(6)]
    stable = values[:3]
    spiked = values[3:]
    assert max(stable) < 120
    assert min(spiked) > 140


def test_electrode_degradation_lowers_reported_quality_over_time():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="electrode_degradation")
    sensor.initialize()
    sensor.start_measurement()
    qualities = []
    for _ in range(6):
        sensor.read_measurement()
        qualities.append(sensor.get_status().signal_quality)
    assert qualities[0] > qualities[-1]
    assert qualities[-1] < 0.7


def test_high_noise_widens_signal_spread():
    normal = ScenarioSensor("SB-001", "CH-01", scenario="normal")
    noisy = ScenarioSensor("SB-001", "CH-02", scenario="high_noise")
    for s in (normal, noisy):
        s.initialize()
        s.start_measurement()
    normal_vals = [normal.read_measurement().raw_signal for _ in range(40)]
    noisy_vals = [noisy.read_measurement().raw_signal for _ in range(40)]

    def spread(vals):
        return max(vals) - min(vals)

    assert spread(noisy_vals) > spread(normal_vals)
    assert noisy.get_status().signal_quality < 0.9


def test_sensor_disconnect_raises_then_reports_disconnected_status():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="sensor_disconnect")
    sensor.initialize()
    sensor.start_measurement()

    for _ in range(3):
        sensor.read_measurement()  # valid reads before the trigger
    assert sensor.get_status().connected is True

    with pytest.raises(SensorDisconnectedError):
        sensor.read_measurement()
    status = sensor.get_status()
    assert status.connected is False
    assert status.last_error is not None


def test_comms_failure_cycles_between_drops_and_recovery():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="comms_failure")
    sensor.initialize()
    sensor.start_measurement()

    outcomes = []
    for _ in range(9):
        try:
            sensor.read_measurement()
            outcomes.append("ok")
        except CommsTimeoutError:
            outcomes.append("drop")

    assert outcomes == ["ok", "ok", "ok", "drop", "drop", "drop", "ok", "ok", "ok"]
    # comms resumed -> connected again by the end
    assert sensor.get_status().connected is True


def test_set_scenario_switches_behavior_mid_run():
    sensor = ScenarioSensor("SB-001", "CH-01", scenario="normal")
    sensor.initialize()
    sensor.start_measurement()
    sensor.read_measurement()

    sensor.set_scenario("rising_concentration")
    assert sensor.scenario_name == "rising_concentration"
    values = [sensor.read_measurement().raw_signal for _ in range(6)]
    assert values[0] < values[-1]


def test_multi_channel_sensor_reads_every_channel():
    device = MultiChannelSensor("SB-001", channel_ids=["CH-01", "CH-02", "CH-03"])
    device.initialize()
    device.start_measurement()

    results = device.read_all()
    assert set(results) == {"CH-01", "CH-02", "CH-03"}
    for channel_id, result in results.items():
        assert isinstance(result, RawMeasurement)
        assert result.channel_id == channel_id
        assert result.device_id == "SB-001"

    device.stop_measurement()


def test_multi_channel_sensor_isolates_a_failing_channel():
    device = MultiChannelSensor("SB-001", channel_ids=["CH-01", "CH-02"])
    device.initialize()
    device.start_measurement()
    device.set_scenario("CH-02", "sensor_disconnect")

    for _ in range(3):
        device.read_all()  # run CH-02 past its disconnect trigger

    results = device.read_all()
    assert isinstance(results["CH-01"], RawMeasurement)
    assert isinstance(results["CH-02"], SensorDisconnectedError)

    statuses = device.get_status()
    assert statuses["CH-01"].connected is True
    assert statuses["CH-02"].connected is False
