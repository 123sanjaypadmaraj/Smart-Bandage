"""
DT-4 "Perturbation & profile library": the day-paced state model that
digital_twin/perturbations.py and digital_twin/profiles.py step forward.

Naming note: this deliverable was originally written directly against
`digital_twin/state.py`, the same path DT-2 independently used for its own
(unrelated, dt_seconds-paced, mean-reverting) `WoundState` model -- the
same kind of path collision DT-1 hit against DT-2 (see
digital_twin/hidden_state.py's module docstring). DT-2's `WoundState` is
the one wired into digital_twin/observation.py and the backend twin
integration, so it kept the `state.py` path; this model -- a separate
`WoundState` with its own fields, meant only for
digital_twin/perturbations.py's clinical-preset scenarios
(digital_twin/profiles.py) -- moved here instead. The two `WoundState`
classes are unrelated and never interchanged; nothing outside
perturbations.py/profiles.py and their tests references this module.

Four fields advance every tick (`step()`), each toward the *current*
`Perturbation`-free baseline; a `Perturbation` (digital_twin/perturbations.py)
then nudges one of them further for the ticks it's active on top of that:

  day                 -- simulated days elapsed, advances by `dt_days` every step
  bacterial_load       -- 0..1, clears toward 0 on its own (a real immune
                           response) unless an InfectionOnset perturbation
                           holds a floor under it
  inflammation          -- 0..1, follows bacterial_load with a lag (a real
                           inflammatory response trails the infection that
                           causes it, not react instantly)
  dressing_integrity    -- 0..1, recovers toward 1 on its own (a real
                           dressing reseals) unless a DressingDisturbance
                           perturbation knocks it down
  electrode_fouling     -- 0..1, accrues slowly on its own (baseline
                           biofilm buildup) plus whatever an
                           ElectrodeFouling perturbation adds on top
  healing_progress      -- 0..1, accrues fastest when bacterial_load is low

`to_observables()` is the one-way mapping from this hidden state to the
simulator-facing numbers a channel would actually show -- how profiles.py's
callers (and their tests) read the state out without reaching into its
internals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class WoundState:
    """Latent state for one DT-4 profile run. Frozen -- `step()` and every
    `Perturbation.apply()` return a new instance via `dataclasses.replace`
    rather than mutating fields in place."""

    day: float = 0.0
    bacterial_load: float = 0.05
    inflammation: float = 0.0
    dressing_integrity: float = 1.0
    electrode_fouling: float = 0.0
    healing_progress: float = 0.0


@dataclass(frozen=True)
class HealingRates:
    """Tunable rates `step()` advances a `WoundState` by. Defaults model an
    uncomplicated wound; `digital_twin/profiles.py`'s `chronic_wound` preset
    slows `healing_per_day`/`bacterial_clearance_per_day` to model one that
    doesn't."""

    healing_per_day: float = 0.05
    bacterial_clearance_per_day: float = 0.15
    inflammation_follow_per_day: float = 0.3
    dressing_recovery_per_day: float = 0.5
    fouling_per_day: float = 0.01


@dataclass(frozen=True)
class Observables:
    """The simulator-facing readout of a `WoundState`, on the same
    `baseline=100.0` a.u. scale simulator/scenarios/scenarios.py uses."""

    signal_baseline: float
    signal_quality: float
    noise_std_multiplier: float
    dressing_disturbed: bool


def step(state: WoundState, dt_days: float, rates: Optional[HealingRates] = None) -> WoundState:
    """Advance `state` by `dt_days` of simulated time, absent any
    perturbation. `digital_twin/profiles.py.simulate()` calls this once per
    tick, then applies that tick's `Perturbation`s on top of the result."""
    if rates is None:
        rates = HealingRates()

    bacterial_load = state.bacterial_load * math.exp(-rates.bacterial_clearance_per_day * dt_days)

    inflammation_decay = math.exp(-rates.inflammation_follow_per_day * dt_days)
    inflammation = _clamp01(
        bacterial_load + (state.inflammation - bacterial_load) * inflammation_decay
    )

    dressing_integrity = _clamp01(
        state.dressing_integrity
        + (1.0 - state.dressing_integrity) * (1.0 - math.exp(-rates.dressing_recovery_per_day * dt_days))
    )

    electrode_fouling = _clamp01(state.electrode_fouling + rates.fouling_per_day * dt_days)

    healing_progress = _clamp01(
        state.healing_progress + rates.healing_per_day * dt_days * (1.0 - bacterial_load)
    )

    return WoundState(
        day=state.day + dt_days,
        bacterial_load=_clamp01(bacterial_load),
        inflammation=inflammation,
        dressing_integrity=dressing_integrity,
        electrode_fouling=electrode_fouling,
        healing_progress=healing_progress,
    )


def to_observables(state: WoundState) -> Observables:
    signal_baseline = 100.0 + 60.0 * state.bacterial_load + 40.0 * state.inflammation
    signal_quality = _clamp01(1.0 - state.electrode_fouling)
    noise_std_multiplier = 1.0 + 3.0 * (1.0 - state.dressing_integrity)
    dressing_disturbed = state.dressing_integrity < 0.9
    return Observables(
        signal_baseline=signal_baseline,
        signal_quality=signal_quality,
        noise_std_multiplier=noise_std_multiplier,
        dressing_disturbed=dressing_disturbed,
    )
