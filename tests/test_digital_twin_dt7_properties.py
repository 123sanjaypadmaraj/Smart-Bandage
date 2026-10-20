"""
DT-7 deliverable: property-based tests and a seeded regression trajectory
for the digital twin's hidden state and engine.

Consolidation note: this originally targeted DT-1's standalone
`DeviceState`/`DigitalTwinState` (digital_twin/state.py at the time), which
was retired when digital_twin/ consolidated onto DT-2's real `WoundState`
-- see docs/architecture/digital_twin.md. The properties below are the
same kind of thing DT-1's version tested (bounds hold for *any* input, not
just hand-picked examples; a seeded run is pinned as a regression), just
re-pointed at what's actually wired into the twin now: `WoundState.step()`
(digital_twin/state.py) and `DigitalTwinEngine` (digital_twin/engine.py).

See conftest.py for the "default" (fast, local) vs. "ci" (deeper search,
no per-example deadline) Hypothesis profile split -- CI and
scripts/run_checks.sh both run under HYPOTHESIS_PROFILE=ci.
"""
from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

from digital_twin.engine import DigitalTwinEngine
from digital_twin.profiles import list_profiles
from digital_twin.state import WoundState

# ---- shared strategies ----
#
# *_target values are drawn across each field's full documented range
# (see WoundState's docstring), not just its default -- the point is that
# step() keeps a field in-bounds no matter where its target is pushed
# (e.g. by a Perturbation retargeting it), not only near its own default.
_INFLAMMATION_RANGE = st.floats(min_value=0.0, max_value=1.5, allow_nan=False, allow_infinity=False)
_UNIT_RANGE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_DT = st.floats(min_value=0.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)
_DT_SEQUENCE = st.lists(_DT, min_size=1, max_size=50)
_SEED = st.integers(min_value=0, max_value=2**31 - 1)


def _random_wound_state(draw_inflammation, draw_bacterial_load, draw_moisture, draw_perfusion) -> WoundState:
    return WoundState(
        inflammation=draw_inflammation,
        bacterial_load=draw_bacterial_load,
        moisture=draw_moisture,
        perfusion=draw_perfusion,
        inflammation_target=draw_inflammation,
        bacterial_load_target=draw_bacterial_load,
        moisture_target=draw_moisture,
        perfusion_target=draw_perfusion,
    )


# ---- WoundState.step(): bounds hold for any target/dt sequence ----


@given(
    inflammation_target=_INFLAMMATION_RANGE,
    bacterial_load_target=_INFLAMMATION_RANGE,
    moisture_target=_UNIT_RANGE,
    perfusion_target=_UNIT_RANGE,
    dts=_DT_SEQUENCE,
    seed=_SEED,
)
def test_wound_state_never_leaves_its_physiological_bounds(
    inflammation_target, bacterial_load_target, moisture_target, perfusion_target, dts, seed
):
    """For any *_target within its documented range and any sequence of
    non-negative dt steps, every field stays inside its documented bounds
    after every tick -- the clamp in `_revert` is supposed to guarantee
    this regardless of how far a Perturbation pushes a target or how large
    a dt_seconds jump the state advances by."""
    state = WoundState(
        inflammation_target=inflammation_target,
        bacterial_load_target=bacterial_load_target,
        moisture_target=moisture_target,
        perfusion_target=perfusion_target,
    )
    rng = random.Random(seed)
    for dt in dts:
        state.step(dt, rng=rng)
        assert 0.0 <= state.inflammation <= 1.5
        assert 0.0 <= state.bacterial_load <= 1.5
        assert 0.0 <= state.moisture <= 1.0
        assert 0.0 <= state.perfusion <= 1.0


@given(
    inflammation_target=_INFLAMMATION_RANGE,
    bacterial_load_target=_INFLAMMATION_RANGE,
    moisture_target=_UNIT_RANGE,
    perfusion_target=_UNIT_RANGE,
    seed=_SEED,
)
def test_wound_state_zero_or_negative_dt_is_a_no_op(
    inflammation_target, bacterial_load_target, moisture_target, perfusion_target, seed
):
    state = _random_wound_state(inflammation_target, bacterial_load_target, moisture_target, perfusion_target)
    before = (state.inflammation, state.bacterial_load, state.moisture, state.perfusion)
    rng = random.Random(seed)
    state.step(0.0, rng=rng)
    state.step(-1.0, rng=rng)
    after = (state.inflammation, state.bacterial_load, state.moisture, state.perfusion)
    assert before == after


@given(seed=_SEED, dts=_DT_SEQUENCE)
@settings(max_examples=25)
def test_wound_state_with_target_already_at_a_bound_never_overshoots(seed, dts):
    """A target pinned exactly at a field's ceiling (a Perturbation with
    severity at its max, e.g. InfectionOnset well past onset+ramp) should
    never push the state past that ceiling, however large or however many
    dt steps follow."""
    state = WoundState(
        inflammation=1.5,
        inflammation_target=1.5,
        bacterial_load=1.5,
        bacterial_load_target=1.5,
        moisture=1.0,
        moisture_target=1.0,
        perfusion=1.0,
        perfusion_target=1.0,
    )
    rng = random.Random(seed)
    for dt in dts:
        state.step(dt, rng=rng)
        assert state.inflammation <= 1.5
        assert state.bacterial_load <= 1.5
        assert state.moisture <= 1.0
        assert state.perfusion <= 1.0


# ---- DigitalTwinEngine: determinism holds for any profile/seed ----


@given(scenario=st.sampled_from(list_profiles()), seed=_SEED, n_ticks=st.integers(min_value=1, max_value=40))
@settings(max_examples=25)
def test_engine_same_seed_reproduces_any_profile_bit_for_bit(scenario, seed, n_ticks):
    a = DigitalTwinEngine("SB-001", "CH-01", scenario=scenario, seed=seed).run(n_ticks)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario=scenario, seed=seed).run(n_ticks)
    assert [type(r).__name__ for r in a] == [type(r).__name__ for r in b]
    assert [getattr(r, "raw_signal", None) for r in a] == [getattr(r, "raw_signal", None) for r in b]
    assert [getattr(r, "timestamp", None) for r in a] == [getattr(r, "timestamp", None) for r in b]


@given(scenario=st.sampled_from(list_profiles()), seed_a=_SEED, seed_b=_SEED)
@settings(max_examples=25)
def test_engine_different_seeds_diverge(scenario, seed_a, seed_b):
    if seed_a == seed_b:
        return  # not the property under test -- see the same-seed test above
    a = DigitalTwinEngine("SB-001", "CH-01", scenario=scenario, seed=seed_a).run(20)
    b = DigitalTwinEngine("SB-001", "CH-01", scenario=scenario, seed=seed_b).run(20)
    a_signals = [getattr(r, "raw_signal", None) for r in a]
    b_signals = [getattr(r, "raw_signal", None) for r in b]
    assert a_signals != b_signals


# ---- seeded regression: pin one exact trajectory, not just its bounds ----


def test_seeded_engine_trajectory_matches_pinned_statistics():
    """A property test alone can't catch a *quiet* change to the
    dynamics -- e.g. a rearranged transfer function that still satisfies
    every bound above but produces a different curve. This pins one fixed,
    reproducible DigitalTwinEngine run's summary statistics as a
    regression: a deliberate change to the dynamics needs a deliberate
    update to these numbers (rerun this exact engine, print
    raw_signal[0]/[-1]/mean/max and copy the new values in, saying why in
    the commit); an accidental change fails it.
    """
    engine = DigitalTwinEngine(
        "SB-001", "CH-01", scenario="complicated_infection", seed=20260906, dt_seconds=3600.0
    )
    readings = engine.run(24 * 9)  # 9 simulated days, hourly ticks
    signals = [r.raw_signal for r in readings]

    assert signals[0] == pytest.approx(107.7205, rel=1e-9)
    assert signals[-1] == pytest.approx(136.1764, rel=1e-9)
    assert max(signals) == pytest.approx(155.5236, rel=1e-9)
    assert sum(signals) / len(signals) == pytest.approx(131.10097222222223, rel=1e-9)

    # still holds the same physiological bounds the property tests check
    # more broadly above -- belt and suspenders on this one specific run.
    final_state = engine._device.state
    assert 0.0 <= final_state.bacterial_load <= 1.5
    assert 0.0 <= final_state.inflammation <= 1.5
