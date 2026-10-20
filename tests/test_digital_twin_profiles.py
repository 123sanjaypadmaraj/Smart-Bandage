"""
DT-4 tests -- the perturbation & profile library (digital_twin/
perturbations.py, digital_twin/profiles.py), now built against DT-2's real
WoundState (digital_twin/state.py) instead of a second, unrelated state
model of its own.

Consolidation note: this originally exercised a standalone, day-paced
WoundState (digital_twin/profile_state.py, since deleted) with its own
`step()`/`to_observables()`. It's rewritten here against the real
DigitalTwinDevice pipeline (digital_twin/observation.py) that
`digital_twin/profiles.py.simulate()` now drives -- a profile's readings
are real RawMeasurements, not a standalone Observables summary, and
`get_profile`/`list_profiles` now serve six profiles (three static risk
levels plus three scripted-onset scenarios), not three.
"""
from __future__ import annotations

import pytest

from common.schemas.measurement import RawMeasurement
from digital_twin.perturbations import DressingDisturbance, InfectionOnset
from digital_twin.profiles import get_profile, list_profiles, simulate
from digital_twin.state import WoundState


def test_six_named_presets_registered():
    assert list_profiles() == sorted(
        [
            "healthy_baseline",
            "diabetic_slow_healing",
            "immunocompromised_high_risk",
            "normal_healing",
            "complicated_infection",
            "chronic_wound",
        ]
    )
    for name in list_profiles():
        assert get_profile(name).description


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        get_profile("not_a_real_profile")


def test_initial_state_is_a_fresh_copy_each_time():
    """Two calls must not hand back the same mutable WoundState instance --
    otherwise starting two simulations from the same profile would let one
    mutate the other's state."""
    a = get_profile("healthy_baseline").initial_state()
    b = get_profile("healthy_baseline").initial_state()
    assert a is not b
    a.bacterial_load = 0.9
    assert b.bacterial_load != 0.9


def test_static_profiles_mean_revert_to_their_own_baseline_not_defaults():
    """diabetic_slow_healing's *_target fields match its initial values
    (see profiles.py), so stepping it forward should hold near its own
    elevated baseline instead of drifting back toward WoundState()'s
    defaults."""
    state = get_profile("diabetic_slow_healing").initial_state()
    for _ in range(200):
        state.step(2.0)
    assert state.bacterial_load > 0.25  # nowhere near WoundState()'s 0.05 default
    assert state.inflammation > 0.2


# ---- perturbations, directly ----


def test_infection_onset_only_raises_the_floor():
    perturbation = InfectionOnset(onset_day=1.0, ramp_days=1.0, severity=0.8)
    state = WoundState(bacterial_load_target=0.05, inflammation_target=0.05)

    perturbation.apply(state, elapsed_days=0.5)  # before onset: untouched
    assert state.bacterial_load_target == 0.05

    perturbation.apply(state, elapsed_days=1.5)  # half-ramped
    half_ramped = state.bacterial_load_target
    assert half_ramped > 0.05

    perturbation.apply(state, elapsed_days=1.4)  # earlier point, re-applied out of order
    assert state.bacterial_load_target == half_ramped  # never lowered back down

    perturbation.apply(state, elapsed_days=5.0)  # well past onset+ramp
    assert state.bacterial_load_target == pytest.approx(0.8, abs=1e-6)


def test_infection_onset_ramp_days_controls_suddenness():
    sudden = InfectionOnset(onset_day=1.0, ramp_days=0.01, severity=0.8)
    gradual = InfectionOnset(onset_day=1.0, ramp_days=3.0, severity=0.8)

    state_sudden = WoundState()
    sudden.apply(state_sudden, elapsed_days=1.1)
    state_gradual = WoundState()
    gradual.apply(state_gradual, elapsed_days=1.1)

    assert state_sudden.bacterial_load_target > state_gradual.bacterial_load_target


def test_dressing_disturbance_raises_moisture_target_then_relaxes():
    perturbation = DressingDisturbance(event_days=(1.0,), severity=0.4, window_days=0.25, baseline_moisture_target=0.5)
    state = WoundState(moisture_target=0.5)

    perturbation.apply(state, elapsed_days=0.5)  # before the event
    assert state.moisture_target == pytest.approx(0.5)

    perturbation.apply(state, elapsed_days=1.1)  # mid-event
    assert state.moisture_target > 0.8

    perturbation.apply(state, elapsed_days=2.0)  # well past the event: relaxed back
    assert state.moisture_target == pytest.approx(0.5)


# ---- simulate(): the real DigitalTwinDevice pipeline, profile-driven ----


def test_simulate_yields_one_state_and_reading_per_tick():
    profile = get_profile("healthy_baseline")
    ticks = list(simulate(profile, duration_days=2, dt_days=1 / 24, seed=1))
    assert len(ticks) == 2 * 24
    for state, reading in ticks:
        assert isinstance(state, WoundState)
        assert isinstance(reading, RawMeasurement)


def test_simulate_normal_healing_profile_stays_quiet():
    profile = get_profile("normal_healing")
    ticks = list(simulate(profile, duration_days=5, dt_days=1 / 24, seed=2))
    signals = [reading.raw_signal for _, reading in ticks]
    # low, stable baseline -- no perturbation ever fires, so the whole run
    # should sit close to pathogen_channel_1's baseline=100 (read noise
    # alone, not a directed climb the way complicated_infection shows).
    early_mean = sum(signals[:24]) / 24
    late_mean = sum(signals[-24:]) / 24
    assert abs(early_mean - 100.0) < 10
    assert abs(late_mean - early_mean) < 10


def test_simulate_complicated_infection_profile_spikes_after_onset():
    profile = get_profile("complicated_infection")
    ticks = list(simulate(profile, duration_days=6, dt_days=1 / 24, seed=3))

    before_onset = [reading.raw_signal for state, reading in ticks if state.bacterial_load_target < 0.1]
    after_onset = [reading.raw_signal for state, reading in ticks if state.bacterial_load_target > 0.7]

    assert before_onset and after_onset
    assert sum(after_onset) / len(after_onset) > sum(before_onset) / len(before_onset) + 10


def test_simulate_chronic_wound_profile_starts_and_stays_elevated():
    profile = get_profile("chronic_wound")
    ticks = list(simulate(profile, duration_days=10, dt_days=1 / 24, seed=4))
    early = [reading.raw_signal for _, reading in ticks[:24]]
    late = [reading.raw_signal for _, reading in ticks[-24:]]
    healthy_baseline_signal = 100.0  # ChannelProfile.baseline for pathogen_channel_1
    assert sum(early) / len(early) > healthy_baseline_signal + 5
    assert sum(late) / len(late) > healthy_baseline_signal + 5


def test_simulate_same_seed_reproduces_the_same_trajectory():
    profile = get_profile("complicated_infection")
    a = [reading.raw_signal for _, reading in simulate(profile, duration_days=3, dt_days=1 / 24, seed=99)]
    b = [reading.raw_signal for _, reading in simulate(profile, duration_days=3, dt_days=1 / 24, seed=99)]
    assert a == b


def test_simulate_rejects_bad_dt():
    with pytest.raises(ValueError):
        list(simulate(get_profile("healthy_baseline"), duration_days=1, dt_days=0))


def test_simulate_rejects_negative_duration():
    with pytest.raises(ValueError):
        list(simulate(get_profile("healthy_baseline"), duration_days=-1))
