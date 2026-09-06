"""
DT-4 "Perturbation & profile library": the seven Phase 2 scenarios,
re-expressed as parameterized perturbations to
digital_twin/profile_state.py's WoundState instead of canned checkpoint
arrays. (Not digital_twin/state.py's WoundState -- that's DT-2's unrelated
model; see profile_state.py's module docstring for why this one lives on
its own path.)

simulator/scenarios/scenarios.py stays exactly as it is (Phase 2 pinned):
a ScenarioConfig is a fixed set of numbers -- "100 -> 110 -> 125 -> 140 ->
155 -> 170", "disconnect once read_index >= 3". A Perturbation here is the
same *idea* but as a function of simulated time and a handful of tunable
parameters, so e.g. "infection onset" can start on day 0.5 or day 5,
ramp fast or slow, and reach mild or severe -- one class, many presets --
instead of a new hardcoded array per variant.

Each Perturbation is applied once per simulated tick, in the order given,
by digital_twin/profiles.py's `simulate()`, each returning the state with
its own piece nudged toward where that perturbation wants it -- never
undoing what an earlier perturbation in the same tick already did (each
only raises its target variable's floor, it doesn't lower it back down).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Tuple

from digital_twin.profile_state import WoundState, _clamp01


class Perturbation(Protocol):
    """Anything that nudges a WoundState forward one tick's worth of event."""

    def apply(self, state: WoundState) -> WoundState: ...


@dataclass(frozen=True)
class InfectionOnset:
    """Bacterial load (and, via state.step's inflammation_follow term,
    inflammation right behind it) rises toward `severity` starting at
    `onset_day`, ramping in over `ramp_days`.

    Generalizes the old rising_concentration ("gradual rise") and
    sudden_abnormal ("stable, then a sudden spike") scenarios as the same
    mechanism: `ramp_days` near zero reproduces a sudden spike, a larger
    value reproduces a gradual climb -- a parameter, not a second class.
    """

    onset_day: float
    ramp_days: float = 1.0
    severity: float = 0.8

    def apply(self, state: WoundState) -> WoundState:
        if state.day < self.onset_day:
            return state
        progress = _clamp01((state.day - self.onset_day) / max(self.ramp_days, 1e-6))
        target = self.severity * progress
        if target <= state.bacterial_load:
            return state
        return replace(state, bacterial_load=_clamp01(target))


@dataclass(frozen=True)
class DressingDisturbance:
    """Knocks dressing_integrity down by `severity` at each of
    `event_days`, held for `window_days` before state.step's own
    dressing_recovery_per_day re-seals it.

    Generalizes the old high_noise (wider spread), sensor_disconnect
    (dropped to invalid) and comms_failure (cyclic packet loss) scenarios:
    a real physical disturbance of the dressing/electrode contact is what
    would actually widen noise or drop a reading, so those three become
    one physical event, timed and repeatable, instead of three unrelated
    fixed-index triggers.
    """

    event_days: Tuple[float, ...]
    severity: float = 0.7
    window_days: float = 0.25

    def apply(self, state: WoundState) -> WoundState:
        for event_day in self.event_days:
            if event_day <= state.day < event_day + self.window_days:
                fraction_remaining = 1.0 - (state.day - event_day) / self.window_days
                dip = self.severity * fraction_remaining
                floor = 1.0 - dip
                if floor < state.dressing_integrity:
                    state = replace(state, dressing_integrity=_clamp01(floor))
        return state


@dataclass(frozen=True)
class ElectrodeFouling:
    """Floors electrode_fouling at `extra_rate_per_day * days since
    onset_day`, capped at `ceiling`, on top of state.step's own slow
    baseline drift.

    Generalizes the old electrode_degradation scenario's fixed
    "98% -> 97% -> 94% -> 88% -> 76% -> 62%" checkpoint curve as a
    configurable onset + rate: a faster rate reproduces a sharper decline,
    a later onset reproduces a probe that starts out clean.
    """

    onset_day: float = 0.0
    extra_rate_per_day: float = 0.05
    ceiling: float = 1.0

    def apply(self, state: WoundState) -> WoundState:
        if state.day < self.onset_day:
            return state
        elapsed = state.day - self.onset_day
        target = min(self.ceiling, self.extra_rate_per_day * elapsed)
        if target <= state.electrode_fouling:
            return state
        return replace(state, electrode_fouling=_clamp01(target))
