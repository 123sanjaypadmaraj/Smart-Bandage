"""
DT-3 tests -- the twin engine (digital_twin/engine.py), its SensorInterface
adapter (digital_twin/adapter.py), and MultiChannelSensor's opt-in wiring
to both (simulator/sensors/multi_channel_sensor.py).

Three things distinguish this from ScenarioSensor
(tests/test_simulator_phase2.py), and each gets its own test below:
  1. ticks are decoupled from the wall clock -- a large trajectory doesn't
     cost wall-clock time to generate.
  2. the same seed reproduces the exact same trajectory; a different seed
     (or none) doesn't.
  3. it's an opt-in SensorInterface backend, not a replacement -- a plain
     MultiChannelSensor(...) still gets ScenarioSensor.
"""
from __future__ import annotations

import time

import pytest

from common.schemas.measurement import RawMeasurement
from digital_twin.adapter import DigitalTwinSensor
from digital_twin.engine import DigitalTwinEngine
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError
from simulator.sensors.multi_channel_sensor import MultiChannelSensor
from simulator.sensors.scenario_sensor import ScenarioSensor


def test_run_generates_a_day_of_trajectory_fast():
    # 86400 one-second ticks == 24 hours of simulated time, produced
    # without a single sleep -- this is the "hours or days in seconds" bar.
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="rising_concentration", seed=7)
    started = time.monotonic()
    readings = engine.run(86_400)
    wall_elapsed = time.monotonic() - started

    assert len(readings) == 86_400
    assert all(isinstance(r, RawMeasurement) for r in readings)
    assert engine.elapsed_seconds == 86_400.0
    assert wall_elapsed < 5.0  # generous headroom; real cost is milliseconds


def test_same_seed_reproduces_the_same_trajectory_bit_for_bit():
    a = DigitalTwinEngine("SB-001", "CH-01", scenario="high_noise", seed=42).run(50)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario="high_noise", seed=42).run(50)

    assert [r.raw_signal for r in a] == [r.raw_signal for r in b]
    assert [r.temperature for r in a] == [r.temperature for r in b]
    assert [r.battery for r in a] == [r.battery for r in b]
    assert [r.timestamp for r in a] == [r.timestamp for r in b]


def test_different_seeds_diverge():
    a = DigitalTwinEngine("SB-001", "CH-01", scenario="high_noise", seed=1).run(50)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario="high_noise", seed=2).run(50)
    assert [r.raw_signal for r in a] != [r.raw_signal for r in b]


def test_reset_with_same_seed_replays_identically():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="normal", seed=99)
    first = engine.run(20)
    engine.reset(seed=99)
    second = engine.run(20)
    assert [r.raw_signal for r in first] == [r.raw_signal for r in second]


def test_tick_paced_manually_matches_run_bulk_generated():
    # Same seed, same scenario -- ticking one at a time (the "demo, paced
    # by whoever calls tick()" path) must trace identically to run()'s
    # bulk path, since neither reads a clock.
    paced = DigitalTwinEngine("SB-001", "CH-01", scenario="sudden_abnormal", seed=5)
    paced.start()
    paced_values = [paced.tick().raw_signal for _ in range(10)]

    bulk = DigitalTwinEngine("SB-001", "CH-01", scenario="sudden_abnormal", seed=5)
    bulk_values = [r.raw_signal for r in bulk.run(10)]

    assert paced_values == bulk_values


def test_tick_before_start_raises():
    engine = DigitalTwinEngine("SB-001", "CH-01")
    with pytest.raises(RuntimeError):
        engine.tick()


def test_disconnect_scenario_raises_then_reports_disconnected_status():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="sensor_disconnect", seed=1)
    engine.start()
    for _ in range(3):
        engine.tick()
    with pytest.raises(SensorDisconnectedError):
        engine.tick()
    status = engine.get_status()
    assert status.connected is False
    assert status.last_error is not None


def test_run_isolates_faults_instead_of_raising_by_default():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="comms_failure", seed=1)
    outcomes = engine.run(9)
    kinds = ["drop" if isinstance(o, CommsTimeoutError) else "ok" for o in outcomes]
    assert kinds == ["ok", "ok", "ok", "drop", "drop", "drop", "ok", "ok", "ok"]


def test_run_stop_on_fault_stops_the_run():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="sensor_disconnect", seed=1)
    outcomes = engine.run(20, stop_on_fault=True)
    assert len(outcomes) == 4  # 3 valid reads, then the disconnect
    assert isinstance(outcomes[-1], SensorDisconnectedError)


def test_dt_seconds_controls_how_much_sim_time_each_tick_covers():
    fast = DigitalTwinEngine("SB-001", "CH-01", scenario="rising_concentration", dt_seconds=10.0)
    fast.start()
    fast.tick()
    assert fast.elapsed_seconds == 10.0


def test_invalid_dt_seconds_rejected():
    with pytest.raises(ValueError):
        DigitalTwinEngine("SB-001", "CH-01", dt_seconds=0)


def test_adapter_satisfies_sensor_interface_contract():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="normal", seed=3)
    sensor.initialize()
    sensor.start_measurement()
    readings = [sensor.read_measurement() for _ in range(5)]
    assert all(isinstance(r, RawMeasurement) for r in readings)
    status = sensor.get_status()
    assert status.connected is True
    sensor.stop_measurement()


def test_adapter_set_scenario_switches_mid_run():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="normal", seed=3)
    sensor.initialize()
    sensor.start_measurement()
    sensor.read_measurement()

    sensor.set_scenario("rising_concentration")
    assert sensor.scenario_name == "rising_concentration"
    values = [sensor.read_measurement().raw_signal for _ in range(6)]
    assert values[0] < values[-1]


def test_adapter_exposes_the_underlying_engine_for_bulk_generation():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="normal", seed=11)
    readings = sensor.engine.run(1000)
    assert len(readings) == 1000


def test_multi_channel_sensor_defaults_to_scenario_engine():
    device = MultiChannelSensor("SB-001", channel_ids=["CH-01"])
    assert isinstance(device.channels["CH-01"], ScenarioSensor)
    assert device.engine_for("CH-01") is None


def test_multi_channel_sensor_opts_into_digital_twin():
    device = MultiChannelSensor(
        "SB-001", channel_ids=["CH-01", "CH-02"], engine="digital_twin", seed=123
    )
    assert all(isinstance(s, DigitalTwinSensor) for s in device.channels.values())

    device.initialize()
    device.start_measurement()
    results = device.read_all()
    assert set(results) == {"CH-01", "CH-02"}
    assert all(isinstance(r, RawMeasurement) for r in results.values())
    device.stop_measurement()


def test_multi_channel_digital_twin_channels_get_distinct_seeded_trajectories():
    device = MultiChannelSensor(
        "SB-001", channel_ids=["CH-01", "CH-02"], engine="digital_twin", seed=123
    )
    ch1 = device.engine_for("CH-01").run(30)
    ch2 = device.engine_for("CH-02").run(30)
    assert [r.raw_signal for r in ch1] != [r.raw_signal for r in ch2]


def test_multi_channel_digital_twin_reproducible_across_devices_with_same_base_seed():
    a = MultiChannelSensor("SB-001", channel_ids=["CH-01", "CH-02"], engine="digital_twin", seed=7)
    b = MultiChannelSensor("SB-002", channel_ids=["CH-01", "CH-02"], engine="digital_twin", seed=7)
    for cid in ("CH-01", "CH-02"):
        values_a = [r.raw_signal for r in a.engine_for(cid).run(20)]
        values_b = [r.raw_signal for r in b.engine_for(cid).run(20)]
        assert values_a == values_b


def test_unknown_engine_name_rejected():
    with pytest.raises(ValueError):
        MultiChannelSensor("SB-001", channel_ids=["CH-01"], engine="not_a_real_engine")
