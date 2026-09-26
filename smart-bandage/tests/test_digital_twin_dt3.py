"""
DT-3 tests -- the twin engine (digital_twin/engine.py), its SensorInterface
adapter (digital_twin/adapter.py), and MultiChannelSensor's opt-in wiring
to both (simulator/sensors/multi_channel_sensor.py).

Consolidation note: this originally exercised DigitalTwinEngine wrapping
the old Phase-2 scripted scenarios (simulator/scenarios.py) on a seeded
tick counter -- reproducible, but not actually twin-driven. It's rewritten
here against the real DigitalTwinEngine, backed by a single-channel
DigitalTwinDevice (digital_twin/observation.py) and a
digital_twin/profiles.py clinical profile instead of a scripted scenario.
The three properties that made DT-3 worth having still get their own test
below, same as before:
  1. ticks are decoupled from the wall clock -- a large trajectory doesn't
     cost wall-clock time to generate.
  2. the same seed reproduces the exact same trajectory -- now including
     the profile's perturbations firing at the same simulated day -- and a
     different seed (or none) doesn't.
  3. it's an opt-in SensorInterface backend, not a replacement -- a plain
     MultiChannelSensor(...) still gets ScenarioSensor.
Plus a fourth, new to the consolidated engine: it's genuinely state-driven
-- a profile's perturbations visibly move the trajectory.
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
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", seed=7)
    started = time.monotonic()
    readings = engine.run(86_400)
    wall_elapsed = time.monotonic() - started

    assert len(readings) == 86_400
    assert all(isinstance(r, RawMeasurement) for r in readings)
    assert engine.elapsed_seconds == 86_400.0
    assert wall_elapsed < 5.0  # generous headroom; real cost is milliseconds


def test_same_seed_reproduces_the_same_trajectory_bit_for_bit():
    a = DigitalTwinEngine("SB-001", "CH-01", scenario="complicated_infection", seed=42).run(50)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario="complicated_infection", seed=42).run(50)

    assert [r.raw_signal for r in a] == [r.raw_signal for r in b]
    assert [r.temperature for r in a] == [r.temperature for r in b]
    assert [r.battery for r in a] == [r.battery for r in b]
    assert [r.timestamp for r in a] == [r.timestamp for r in b]


def test_different_seeds_diverge():
    a = DigitalTwinEngine("SB-001", "CH-01", scenario="complicated_infection", seed=1).run(50)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario="complicated_infection", seed=2).run(50)
    assert [r.raw_signal for r in a] != [r.raw_signal for r in b]


def test_reset_with_same_seed_replays_identically():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", seed=99)
    first = engine.run(20)
    engine.reset(seed=99)
    second = engine.run(20)
    assert [r.raw_signal for r in first] == [r.raw_signal for r in second]


def test_tick_paced_manually_matches_run_bulk_generated():
    # Same seed, same scenario -- ticking one at a time (the "demo, paced
    # by whoever calls tick()" path) must trace identically to run()'s
    # bulk path, since neither reads a clock.
    paced = DigitalTwinEngine("SB-001", "CH-01", scenario="chronic_wound", seed=5)
    paced.start()
    paced_values = [paced.tick().raw_signal for _ in range(10)]

    bulk = DigitalTwinEngine("SB-001", "CH-01", scenario="chronic_wound", seed=5)
    bulk_values = [r.raw_signal for r in bulk.run(10)]

    assert paced_values == bulk_values


def test_tick_before_start_raises():
    engine = DigitalTwinEngine("SB-001", "CH-01")
    with pytest.raises(RuntimeError):
        engine.tick()


def test_unknown_scenario_rejected():
    with pytest.raises(KeyError):
        DigitalTwinEngine("SB-001", "CH-01", scenario="not_a_real_profile")


def test_dt_seconds_controls_how_much_sim_time_each_tick_covers():
    fast = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", dt_seconds=10.0)
    fast.start()
    fast.tick()
    assert fast.elapsed_seconds == 10.0


def test_invalid_dt_seconds_rejected():
    with pytest.raises(ValueError):
        DigitalTwinEngine("SB-001", "CH-01", dt_seconds=0)


def test_engine_is_actually_state_driven_by_its_profile():
    """The point of the consolidation: a profile's perturbations move the
    trajectory, not a checkpoint array -- InfectionOnset in
    "complicated_infection" (onset_day=2, ramps in over 1.5 days) should
    read noticeably higher a week in than on day 0."""
    engine = DigitalTwinEngine(
        "SB-001", "CH-01", scenario="complicated_infection", seed=3, dt_seconds=3600.0
    )
    readings = engine.run(24 * 9)  # 9 simulated days, hourly ticks
    early = [r.raw_signal for r in readings[:24] if isinstance(r, RawMeasurement)]
    late = [r.raw_signal for r in readings[-24:] if isinstance(r, RawMeasurement)]
    assert sum(late) / len(late) > sum(early) / len(early) + 10


def test_engine_exposes_ground_truth_off_to_the_side():
    """true_signal is readable but not part of any RawMeasurement --
    digital_twin/state.py's WoundState docstring is explicit that this must
    never leak onto a real device's contract."""
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="complicated_infection", seed=1)
    engine.start()
    reading = engine.tick()
    assert not hasattr(reading, "true_signal")
    assert isinstance(engine.true_signal, float)


def test_link_quality_fault_still_propagates_through_the_engine():
    """DigitalTwinDevice's link-quality-driven faults (device_physics.py,
    simulator/faults/faults.py) surface through the engine exactly like any
    other DigitalTwinDevice caller -- forcing link_quality down (instead of
    scripting a read-index trigger, DT-3's old mechanism) is enough."""
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", seed=1)
    engine.start()
    engine._device.link_quality.value = 0.01
    outcomes = []
    for _ in range(40):
        engine._device.link_quality.value = 0.01
        try:
            engine.tick()
            outcomes.append("ok")
        except (CommsTimeoutError, SensorDisconnectedError) as exc:
            outcomes.append(type(exc).__name__)
    assert "SensorDisconnectedError" in outcomes or "CommsTimeoutError" in outcomes


def test_run_isolates_faults_instead_of_raising_by_default():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", seed=1)
    engine._device.link_quality.value = 0.01
    outcomes = engine.run(5)
    assert any(isinstance(o, (CommsTimeoutError, SensorDisconnectedError)) for o in outcomes)
    # a channel that faults doesn't stop the run -- every tick still produced *something*
    assert len(outcomes) == 5


def test_run_stop_on_fault_stops_the_run():
    engine = DigitalTwinEngine("SB-001", "CH-01", scenario="healthy_baseline", seed=1)
    engine._device.link_quality.value = 0.001
    outcomes = engine.run(50, stop_on_fault=True)
    assert isinstance(outcomes[-1], (SensorDisconnectedError, CommsTimeoutError))
    assert len(outcomes) < 50


def test_adapter_satisfies_sensor_interface_contract():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="healthy_baseline", seed=3)
    sensor.initialize()
    sensor.start_measurement()
    readings = [sensor.read_measurement() for _ in range(5)]
    assert all(isinstance(r, RawMeasurement) for r in readings)
    status = sensor.get_status()
    assert status.connected is True
    sensor.stop_measurement()


def test_adapter_set_scenario_switches_mid_run():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="healthy_baseline", seed=3)
    sensor.initialize()
    sensor.start_measurement()
    sensor.read_measurement()

    sensor.set_scenario("immunocompromised_high_risk")
    assert sensor.scenario_name == "immunocompromised_high_risk"
    values = [sensor.read_measurement().raw_signal for _ in range(6)]
    # immunocompromised_high_risk's baseline WoundState reads well above
    # healthy_baseline's -- switching profiles should show up immediately.
    assert all(v > 100.0 for v in values)


def test_adapter_exposes_the_underlying_engine_for_bulk_generation():
    sensor = DigitalTwinSensor("SB-001", "CH-01", scenario="healthy_baseline", seed=11)
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


def test_multi_channel_digital_twin_defaults_to_healthy_baseline_profile():
    device = MultiChannelSensor("SB-001", channel_ids=["CH-01"], engine="digital_twin", seed=1)
    assert device.engine_for("CH-01").scenario_name == "healthy_baseline"


def test_unknown_engine_name_rejected():
    with pytest.raises(ValueError):
        MultiChannelSensor("SB-001", channel_ids=["CH-01"], engine="not_a_real_engine")
