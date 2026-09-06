"""
DT-4 "Perturbation & profile library".

The existing seven simulator/scenarios/scenarios.py scenarios become
parameterized perturbations to digital_twin/profile_state.py's state model
(digital_twin/perturbations.py) instead of canned arrays. This module is
where those perturbations get named and packaged into clinical presets --
`normal_healing`, `complicated_infection`, `chronic_wound` -- and where a
profile actually gets stepped forward in simulated time.

The old scripted scenarios and tests/test_simulator_phase2.py are
untouched: twin-driven profiles are added alongside, following the same
registry shape (`get_*` / `list_*`) as scenarios.py so a later phase can
wire a TwinProfile into a sensor the same way ScenarioSensor already
consumes a ScenarioConfig, without either registry having to know about
the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Tuple

from digital_twin.perturbations import (
    DressingDisturbance,
    ElectrodeFouling,
    InfectionOnset,
    Perturbation,
)
from digital_twin.profile_state import HealingRates, Observables, WoundState, step, to_observables


@dataclass(frozen=True)
class TwinProfile:
    name: str
    description: str
    initial_state: WoundState = field(default_factory=WoundState)
    rates: HealingRates = field(default_factory=HealingRates)
    perturbations: Tuple[Perturbation, ...] = ()


_PROFILES: dict[str, TwinProfile] = {
    "normal_healing": TwinProfile(
        name="normal_healing",
        description="Clean wound, no perturbations: steady healing, low bacterial load, intact dressing throughout.",
        initial_state=WoundState(bacterial_load=0.03, inflammation=0.03),
        perturbations=(),
    ),
    "complicated_infection": TwinProfile(
        name="complicated_infection",
        description="Infection onset around day 2, ramping fast, plus a dressing disturbance a day and a half later.",
        initial_state=WoundState(bacterial_load=0.05, inflammation=0.05),
        perturbations=(
            InfectionOnset(onset_day=2.0, ramp_days=1.5, severity=0.85),
            DressingDisturbance(event_days=(3.5, 4.0), severity=0.6),
        ),
    ),
    "chronic_wound": TwinProfile(
        name="chronic_wound",
        description="Slow healing, persistent low-grade infection from day 0, and electrode fouling accumulating over a long monitoring window.",
        initial_state=WoundState(bacterial_load=0.2, inflammation=0.15),
        rates=HealingRates(healing_per_day=0.015, bacterial_clearance_per_day=0.05),
        perturbations=(
            InfectionOnset(onset_day=0.0, ramp_days=0.1, severity=0.3),
            ElectrodeFouling(onset_day=1.0, extra_rate_per_day=0.03),
        ),
    ),
}


def get_profile(name: str) -> TwinProfile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown profile {name!r}; available: {', '.join(sorted(_PROFILES))}"
        ) from exc


def list_profiles() -> list[str]:
    return sorted(_PROFILES)


def simulate(
    profile: TwinProfile,
    duration_days: float,
    dt_days: float = 1.0 / 24,
) -> Iterator[Tuple[WoundState, Observables]]:
    """Step `profile`'s twin forward from its initial_state to
    `duration_days`, applying its perturbations every tick, yielding one
    (state, observables) pair per tick.

    `dt_days` defaults to an hourly tick (1/24 day) -- fine-grained enough
    that a perturbation's `window_days`/`ramp_days` (see
    digital_twin/perturbations.py) render as a curve rather than a jump,
    without the caller having to pick a tick size themselves.
    """
    if dt_days <= 0:
        raise ValueError("dt_days must be > 0")
    if duration_days < 0:
        raise ValueError("duration_days must be >= 0")

    state = profile.initial_state
    n_ticks = max(1, round(duration_days / dt_days))
    for _ in range(n_ticks):
        state = step(state, dt_days, profile.rates)
        for perturbation in profile.perturbations:
            state = perturbation.apply(state)
        yield state, to_observables(state)
