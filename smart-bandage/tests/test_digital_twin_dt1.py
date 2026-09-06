"""
DT-1 tests -- the hidden state model (digital_twin/hidden_state.py).

Two things distinguish `DigitalTwinState` from a scenario's checkpoint
array (tests/test_simulator_phase2.py), and both get exercised here:
  1. state persists and accumulates across ticks instead of being indexed
     by read count -- see test_tick_accumulates_and_returned_state_is_a_stable_snapshot.
  2. the same channel evolved over a different dt covers a different
     amount of physiological time and produces a different trajectory --
     see test_finer_steps_track_closer_to_the_true_logistic_curve.

Module note: this deliverable originally lived at digital_twin/state.py;
it moved to digital_twin/hidden_state.py to free that path for DT-2's
unrelated WoundState model -- see hidden_state.py's module docstring.
"""
from __future__ import annotations

import math

import pytest

from digital_twin.hidden_state import (
    BacterialLoadParams,
    DeviceState,
    DigitalTwinState,
    infection_biomarker,
)


# ---- DeviceState / DigitalTwinState plumbing ----


def test_initial_state_defaults():
    state = DeviceState()
    assert 0.0 < state.bacterial_load < 1.0
    assert state.inflammation == 0.0
    assert state.healing_stage_progress == 0.0
    assert 0.0 <= state.moisture <= 1.0


def test_custom_initial_state_is_used_as_is():
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=0.2, moisture=0.8))
    assert twin.state.bacterial_load == 0.2
    assert twin.state.moisture == 0.8


def test_tick_rejects_negative_dt():
    twin = DigitalTwinState()
    with pytest.raises(ValueError):
        twin.tick(-1.0)


def test_tick_accumulates_and_returned_state_is_a_stable_snapshot():
    """State persists across ticks (not reset/reread each call), and the
    DeviceState handed back from an earlier tick doesn't change out from
    under the caller once later ticks advance twin.state."""
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=0.1))
    first = twin.tick(dt_seconds=1.0)
    second = twin.tick(dt_seconds=1.0)

    assert second.bacterial_load > first.bacterial_load
    # the snapshot returned by the first tick is untouched by the second
    assert first.bacterial_load < twin.state.bacterial_load


# ---- bacterial_load: the one channel DT-1 proves ----


def test_bacterial_load_matches_one_manual_euler_step():
    params = BacterialLoadParams(growth_rate=0.1, carrying_capacity=1.0)
    b0 = 0.1
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=b0), bacterial_load_params=params)

    dt = 2.0
    expected_derivative = params.growth_rate * b0 * (1.0 - b0 / params.carrying_capacity)
    expected = b0 + dt * expected_derivative

    result = twin.tick(dt_seconds=dt)
    assert result.bacterial_load == pytest.approx(expected)


def test_bacterial_load_grows_logistically_and_saturates_at_carrying_capacity():
    params = BacterialLoadParams(growth_rate=0.2, carrying_capacity=1.0)
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=0.05), bacterial_load_params=params)

    values = [twin.tick(dt_seconds=1.0).bacterial_load for _ in range(500)]

    # monotonically non-decreasing, and never exceeds the ceiling
    assert all(b2 >= b1 - 1e-12 for b1, b2 in zip(values, values[1:]))
    assert all(b <= params.carrying_capacity for b in values)
    assert values[-1] == pytest.approx(params.carrying_capacity, abs=1e-3)


def test_bacterial_load_stays_at_zero_if_it_starts_at_zero():
    """The logistic derivative vanishes at B=0 -- an uninfected device
    should never spontaneously develop an infection on its own."""
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=0.0))
    for _ in range(50):
        state = twin.tick(dt_seconds=1.0)
    assert state.bacterial_load == 0.0


def test_finer_steps_track_closer_to_the_true_logistic_curve():
    """Two ticks of dt/2 land closer to the analytic logistic solution than
    one tick of dt -- proof this is genuine Euler integration reacting to
    step size, not a checkpoint lookup that would be indifferent to it."""
    params = BacterialLoadParams(growth_rate=0.5, carrying_capacity=1.0)
    b0 = 0.1
    dt = 2.0

    def analytic(t: float) -> float:
        k = params.carrying_capacity
        r = params.growth_rate
        return k / (1.0 + (k / b0 - 1.0) * math.exp(-r * t))

    coarse = DigitalTwinState(initial=DeviceState(bacterial_load=b0), bacterial_load_params=params)
    coarse.tick(dt_seconds=dt)

    fine = DigitalTwinState(initial=DeviceState(bacterial_load=b0), bacterial_load_params=params)
    fine.tick(dt_seconds=dt / 2)
    fine.tick(dt_seconds=dt / 2)

    truth = analytic(dt)
    assert abs(fine.state.bacterial_load - truth) < abs(coarse.state.bacterial_load - truth)


# ---- the other three channels: proven present, not yet dynamic ----


def test_unwired_channels_hold_their_initial_value():
    twin = DigitalTwinState(initial=DeviceState(inflammation=0.3, healing_stage_progress=0.4, moisture=0.6))
    for _ in range(20):
        state = twin.tick(dt_seconds=1.0)

    assert state.inflammation == 0.3
    assert state.healing_stage_progress == 0.4
    assert state.moisture == 0.6


# ---- infection_biomarker: hidden state -> observable ----


def test_infection_biomarker_bounded_between_0_and_100():
    for load in (0.0, 0.1, 0.3, 0.5, 0.9, 1.0):
        value = infection_biomarker(load)
        assert 0.0 <= value <= 100.0


def test_infection_biomarker_monotonic_in_bacterial_load():
    loads = [i / 20 for i in range(21)]
    values = [infection_biomarker(load) for load in loads]
    assert all(v2 >= v1 for v1, v2 in zip(values, values[1:]))


def test_infection_biomarker_near_floor_and_ceiling_away_from_midpoint():
    assert infection_biomarker(0.0, midpoint=0.3, steepness=8.0) < 10.0
    assert infection_biomarker(1.0, midpoint=0.3, steepness=8.0) > 99.0
