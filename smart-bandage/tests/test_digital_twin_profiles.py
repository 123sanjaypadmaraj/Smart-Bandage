"""
DT-4 tests -- the perturbation & profile library. Independent of
tests/test_simulator_phase2.py, which stays pinned to the old scripted
scenarios; this exercises the new, parallel digital_twin state model
(digital_twin/profile_state.py -- not digital_twin/state.py's unrelated
DT-2 WoundState; see profile_state.py's module docstring).
"""
from __future__ import annotations

import pytest

from digital_twin.perturbations import DressingDisturbance, ElectrodeFouling, InfectionOnset
from digital_twin.profile_state import HealingRates, WoundState, step, to_observables
from digital_twin.profiles import get_profile, list_profiles, simulate


def test_three_named_presets_registered():
    assert list_profiles() == sorted(
        ["normal_healing", "complicated_infection", "chronic_wound"]
    )
    for name in list_profiles():
        assert get_profile(name).description


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        get_profile("not_a_real_profile")


def test_step_with_no_perturbations_is_normal_healing():
    state = WoundState(bacterial_load=0.05, inflammation=0.05)
    for _ in range(24 * 5):  # 5 simulated days, hourly ticks
        state = step(state, 1 / 24)
    assert state.healing_progress > 0
    assert state.bacterial_load < 0.05  # clears on its own with no infection
    assert state.dressing_integrity == 1.0
    assert state.electrode_fouling > 0  # slow baseline drift still accrues


def test_infection_onset_ramps_in_then_holds_severity():
    perturbation = InfectionOnset(onset_day=1.0, ramp_days=1.0, severity=0.8)
    state = WoundState(bacterial_load=0.05)

    # before onset: untouched
    early = step(state, 0.5)
    early = perturbation.apply(early)
    assert early.bacterial_load < 0.1

    # well past onset + ramp: at/near severity (state.step's own clearance
    # pulls slightly below the floor between ticks, so allow a little slack)
    state = WoundState(bacterial_load=0.05)
    for _ in range(24 * 5):
        state = step(state, 1 / 24)
        state = perturbation.apply(state)
    assert state.bacterial_load > 0.7


def test_infection_onset_ramp_days_controls_suddenness():
    sudden = InfectionOnset(onset_day=1.0, ramp_days=0.01, severity=0.8)
    gradual = InfectionOnset(onset_day=1.0, ramp_days=3.0, severity=0.8)

    def bacterial_load_at_day(perturbation, day):
        state = WoundState(bacterial_load=0.05)
        n_ticks = round(day * 24)
        for _ in range(n_ticks):
            state = step(state, 1 / 24)
            state = perturbation.apply(state)
        return state.bacterial_load

    just_after_onset = 1.1
    assert bacterial_load_at_day(sudden, just_after_onset) > bacterial_load_at_day(
        gradual, just_after_onset
    )


def test_dressing_disturbance_dips_then_recovers():
    perturbation = DressingDisturbance(event_days=(1.0,), severity=0.7, window_days=0.25)
    state = WoundState()

    for _ in range(24):  # 1 day: before the event
        state = step(state, 1 / 24)
        state = perturbation.apply(state)
    assert state.dressing_integrity == pytest.approx(1.0, abs=0.01)

    mid_event = WoundState(day=1.1, dressing_integrity=1.0)
    disturbed = perturbation.apply(mid_event)
    assert disturbed.dressing_integrity < 0.7

    for _ in range(24 * 10):  # 10 more days: well past the event, recovered
        state = step(state, 1 / 24)
        state = perturbation.apply(state)
    assert state.dressing_integrity > 0.95


def test_electrode_fouling_rate_controls_decline_speed():
    slow = ElectrodeFouling(onset_day=0.0, extra_rate_per_day=0.01)
    fast = ElectrodeFouling(onset_day=0.0, extra_rate_per_day=0.2)

    def fouling_after(perturbation, days):
        state = WoundState()
        for _ in range(round(days * 24)):
            state = step(state, 1 / 24, HealingRates(fouling_per_day=0.0))
            state = perturbation.apply(state)
        return state.electrode_fouling

    assert fouling_after(fast, 3) > fouling_after(slow, 3)


def test_to_observables_reflects_infection_and_fouling():
    healthy = to_observables(WoundState(bacterial_load=0.0, inflammation=0.0))
    infected = to_observables(WoundState(bacterial_load=0.8, inflammation=0.6))
    assert infected.signal_baseline > healthy.signal_baseline

    fouled = to_observables(WoundState(electrode_fouling=0.9))
    assert fouled.signal_quality < healthy.signal_quality

    disturbed = to_observables(WoundState(dressing_integrity=0.2))
    assert disturbed.noise_std_multiplier > healthy.noise_std_multiplier
    assert disturbed.dressing_disturbed is True


def test_simulate_normal_healing_profile_stays_quiet():
    profile = get_profile("normal_healing")
    states_and_obs = list(simulate(profile, duration_days=5, dt_days=1 / 24))
    assert len(states_and_obs) == 5 * 24

    final_state, final_obs = states_and_obs[-1]
    assert final_state.healing_progress > 0
    assert final_obs.signal_quality > 0.9
    assert final_obs.dressing_disturbed is False


def test_simulate_complicated_infection_profile_spikes_after_onset():
    profile = get_profile("complicated_infection")
    states_and_obs = list(simulate(profile, duration_days=6, dt_days=1 / 24))

    before_onset = [obs for state, obs in states_and_obs if state.day < 2.0]
    after_onset = [obs for state, obs in states_and_obs if state.day > 4.0]

    assert max(o.signal_baseline for o in before_onset) < min(
        o.signal_baseline for o in after_onset
    )
    # the dressing disturbance a day and a half after onset should show up
    # as at least one disturbed tick somewhere in the run
    assert any(obs.dressing_disturbed for _, obs in states_and_obs)


def test_simulate_chronic_wound_profile_degrades_quality_over_a_long_run():
    profile = get_profile("chronic_wound")
    states_and_obs = list(simulate(profile, duration_days=30, dt_days=1 / 24))

    _, early_obs = states_and_obs[0]
    _, late_obs = states_and_obs[-1]
    assert late_obs.signal_quality < early_obs.signal_quality


def test_simulate_rejects_bad_dt():
    with pytest.raises(ValueError):
        list(simulate(get_profile("normal_healing"), duration_days=1, dt_days=0))
