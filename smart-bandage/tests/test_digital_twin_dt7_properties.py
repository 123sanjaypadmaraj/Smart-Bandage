"""
DT-7 deliverable: property-based tests and a seeded regression trajectory
for the digital twin's hidden state (digital_twin/state.py).

tests/test_digital_twin_dt1.py already proves the mechanics -- state
persists across ticks, Euler integration reacts to step size -- on a
handful of hand-picked examples. Hand-picked examples can't tell us the
invariants *always* hold: that bacterial_load never drifts outside
[0, carrying_capacity] for some growth_rate/dt/initial-load combination
nobody happened to try, or that the three not-yet-dynamic channels never
sneak outside their physiological [0, 1] bounds. Hypothesis searches that
input space instead, and shrinks any counterexample it finds down to the
smallest input that still fails. See conftest.py for the "default" (fast,
local) vs. "ci" (deeper search, no per-example deadline) profile split --
CI and scripts/run_checks.sh both run under HYPOTHESIS_PROFILE=ci.

A property test alone can't catch a *quiet* change to the dynamics -- e.g.
a rearranged logistic formula that still satisfies every bound but
produces a different curve. test_seeded_trajectory_matches_pinned_stats
below guards against that with a fixed, reproducible trajectory (stdlib
random.Random, not Hypothesis's own example generator -- Hypothesis makes
no promise that a given seed reproduces the same examples across library
versions) whose summary statistics are hardcoded as an expected-value
regression. A deliberate change to the dynamics needs a deliberate update
to those numbers (re-derive them by running the trajectory and copying the
new values in, and say why in the commit); an accidental change fails it.
"""
from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

from digital_twin.state import (
    BacterialLoadParams,
    DeviceState,
    DigitalTwinState,
    infection_biomarker,
)


# ---- shared strategies ----
#
# growth_rate/dt are capped well above any realistic read interval or decay
# constant, not to dodge the defensive clamp in DigitalTwinState.tick() --
# the whole point of these tests is that the clamp holds even when a step
# overshoots -- but so Hypothesis spends its search budget on the regime the
# model is meant for rather than float edge cases (inf/nan) unrelated to the
# invariants below.
_GROWTH_RATE = st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False)
_CARRYING_CAPACITY = st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False)
_UNIT_INTERVAL = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_DT = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
_DT_SEQUENCE = st.lists(_DT, min_size=1, max_size=50)


# ---- bacterial_load: bounds hold for any params/dt sequence ----


@given(
    growth_rate=_GROWTH_RATE,
    carrying_capacity=_CARRYING_CAPACITY,
    initial_load_fraction=_UNIT_INTERVAL,
    dts=_DT_SEQUENCE,
)
def test_bacterial_load_never_leaves_its_physiological_bounds(
    growth_rate, carrying_capacity, initial_load_fraction, dts
):
    """For any valid params and any sequence of non-negative dt steps,
    bacterial_load stays inside [0, carrying_capacity] after every tick --
    the defensive clamp in DigitalTwinState.tick() is supposed to guarantee
    this even when a step overshoots the analytic ceiling."""
    params = BacterialLoadParams(growth_rate=growth_rate, carrying_capacity=carrying_capacity)
    initial_load = initial_load_fraction * carrying_capacity
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=initial_load), bacterial_load_params=params)

    for dt in dts:
        state = twin.tick(dt_seconds=dt)
        assert 0.0 <= state.bacterial_load <= carrying_capacity


@given(
    growth_rate=_GROWTH_RATE,
    carrying_capacity=_CARRYING_CAPACITY,
    initial_load_fraction=_UNIT_INTERVAL,
    dts=_DT_SEQUENCE,
)
def test_bacterial_load_is_monotonically_non_decreasing(
    growth_rate, carrying_capacity, initial_load_fraction, dts
):
    """growth_rate >= 0 and the logistic derivative is >= 0 everywhere in
    [0, carrying_capacity], so bacterial_load can only grow or hold -- it
    should never dip between ticks regardless of dt or params chosen."""
    params = BacterialLoadParams(growth_rate=growth_rate, carrying_capacity=carrying_capacity)
    initial_load = initial_load_fraction * carrying_capacity
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=initial_load), bacterial_load_params=params)

    previous = initial_load
    for dt in dts:
        state = twin.tick(dt_seconds=dt)
        assert state.bacterial_load >= previous - 1e-9
        previous = state.bacterial_load


@given(
    growth_rate=_GROWTH_RATE,
    carrying_capacity=_CARRYING_CAPACITY,
    initial_load_fraction=_UNIT_INTERVAL,
)
def test_zero_dt_is_a_no_op(growth_rate, carrying_capacity, initial_load_fraction):
    params = BacterialLoadParams(growth_rate=growth_rate, carrying_capacity=carrying_capacity)
    initial_load = initial_load_fraction * carrying_capacity
    twin = DigitalTwinState(initial=DeviceState(bacterial_load=initial_load), bacterial_load_params=params)

    state = twin.tick(dt_seconds=0.0)
    assert state.bacterial_load == pytest.approx(initial_load)


@given(dt=st.floats(max_value=-1e-9, allow_nan=False, allow_infinity=False))
@settings(max_examples=50)
def test_tick_rejects_any_negative_dt(dt):
    twin = DigitalTwinState()
    with pytest.raises(ValueError):
        twin.tick(dt)


# ---- the three not-yet-dynamic channels: bounds hold under zero dynamics ----


@given(
    inflammation=_UNIT_INTERVAL,
    healing_stage_progress=_UNIT_INTERVAL,
    moisture=_UNIT_INTERVAL,
    dts=_DT_SEQUENCE,
)
def test_unwired_channels_never_leave_the_unit_interval(
    inflammation, healing_stage_progress, moisture, dts
):
    """DT-1 gives inflammation/healing_stage_progress/moisture zero
    dynamics, so for any in-bounds starting value they should hold exactly
    at that value -- and therefore stay in [0, 1] -- for any dt sequence.
    This is what lets later DT tickets wire real dynamics onto these fields
    without the vector's shape or bounds changing underneath them."""
    twin = DigitalTwinState(
        initial=DeviceState(
            inflammation=inflammation,
            healing_stage_progress=healing_stage_progress,
            moisture=moisture,
        )
    )

    for dt in dts:
        state = twin.tick(dt_seconds=dt)
        assert state.inflammation == inflammation
        assert state.healing_stage_progress == healing_stage_progress
        assert state.moisture == moisture
        assert 0.0 <= state.inflammation <= 1.0
        assert 0.0 <= state.healing_stage_progress <= 1.0
        assert 0.0 <= state.moisture <= 1.0


# ---- infection_biomarker: bounded for any bacterial_load/params ----


@given(
    bacterial_load=_UNIT_INTERVAL,
    midpoint=_UNIT_INTERVAL,
    steepness=st.floats(min_value=0.01, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_infection_biomarker_always_bounded_between_0_and_100(bacterial_load, midpoint, steepness):
    value = infection_biomarker(bacterial_load, midpoint=midpoint, steepness=steepness)
    assert 0.0 <= value <= 100.0


# ---- seeded regression: pin the trajectory itself, not just its bounds ----


def test_seeded_trajectory_matches_pinned_statistics():
    """A fixed, reproducible trajectory (see module docstring for why this
    is stdlib random.Random, not Hypothesis's own example generator) with
    its summary statistics hardcoded as a regression.

    To regenerate these numbers deliberately after a genuine change to the
    dynamics: run this same rng/params/dts sequence, print
    trajectory[-1] / mean(trajectory) / max(trajectory) / trajectory[0] /
    trajectory[99] and the same four over infection_biomarker(trajectory),
    and copy the new values in below.
    """
    rng = random.Random(20260906)  # arbitrary but fixed -- the date this test was written
    params = BacterialLoadParams(
        growth_rate=rng.uniform(0.01, 0.05),
        carrying_capacity=1.0,
    )
    twin = DigitalTwinState(
        initial=DeviceState(bacterial_load=rng.uniform(0.02, 0.08)),
        bacterial_load_params=params,
    )
    dts = [rng.uniform(0.5, 2.0) for _ in range(200)]

    trajectory = [twin.tick(dt_seconds=dt).bacterial_load for dt in dts]
    biomarkers = [infection_biomarker(b) for b in trajectory]

    # bacterial_load
    assert trajectory[0] == pytest.approx(0.0406845865161696, rel=1e-9)
    assert trajectory[99] == pytest.approx(0.9209030355917835, rel=1e-9)
    assert trajectory[-1] == pytest.approx(0.9997782225089165, rel=1e-9)
    assert max(trajectory) == pytest.approx(0.9997782225089165, rel=1e-9)
    assert sum(trajectory) / len(trajectory) == pytest.approx(0.7136718981427245, rel=1e-9)

    # infection_biomarker(bacterial_load) along the same trajectory
    assert biomarkers[0] == pytest.approx(11.15977924667928, rel=1e-9)
    assert biomarkers[-1] == pytest.approx(99.63092417931684, rel=1e-9)
    assert sum(biomarkers) / len(biomarkers) == pytest.approx(80.31761290680686, rel=1e-9)

    # still holds the same physiological bounds the property tests check
    # more broadly above -- belt and suspenders on this one specific run.
    assert all(0.0 <= b <= 1.0 for b in trajectory)
    assert all(b2 >= b1 - 1e-12 for b1, b2 in zip(trajectory, trajectory[1:]))
