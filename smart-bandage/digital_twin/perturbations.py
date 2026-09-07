"""
DT-4 "Perturbation & profile library": the Phase 2 scenarios re-expressed
as parameterized perturbations to digital_twin/state.py's real `WoundState`
(DT-2) instead of canned checkpoint arrays -- or, after consolidation, a
second state model of their own.

simulator/scenarios/scenarios.py stays exactly as it is (Phase 2 pinned):
a ScenarioConfig is a fixed set of numbers -- "100 -> 110 -> 125 -> 140 ->
155 -> 170", "disconnect once read_index >= 3". A Perturbation here is the
same *idea* but as a function of simulated time and a handful of tunable
parameters, so e.g. "infection onset" can start on day 0.5 or day 5, ramp
fast or slow, and reach mild or severe -- one class, many presets --
instead of a new hardcoded array per variant.

Consolidation note: this originally targeted a separate, day-paced
WoundState of its own (digital_twin/profile_state.py, since deleted) that
collided on the `state.py` path with DT-2's real one and was never wired
into a sensor or the backend. A `Perturbation` now retargets DT-2's real
`WoundState.*_target` fields directly -- exactly the mechanism
`WoundState.step()` was already built for (see its docstring: "retarget
one ... to move the whole state over time") -- so `digital_twin/profiles.py`
and `backend/app/simulation.py` apply the same perturbation to the same
state every device actually runs on, live or in a backtest.

Two of the original three perturbations carry over, retargeted to fields
`WoundState` actually has:

  - InfectionOnset -> bacterial_load_target (and inflammation_target
    follows, the way a real inflammatory response trails the infection
    that causes it)
  - DressingDisturbance -> moisture_target (a disturbed/loosened dressing
    lets more moisture in at the wound bed; WoundState has no
    `dressing_integrity` field of its own to knock down instead)

The third, ElectrodeFouling, is dropped as a distinct perturbation:
digital_twin/device_physics.py:ElectrodeFoulingProcess already fouls faster
with higher moisture/bacterial_load as a continuous, state-coupled
process, so a profile that wants "fouls faster" gets it for free from an
elevated moisture/bacterial_load baseline (see profiles.py's
`chronic_wound`) instead of a second mechanism modeling the same thing.

Each perturbation is applied once per simulated tick, in the order given,
by `TwinProfile`'s caller (`digital_twin/profiles.py.simulate()`,
`backend/app/simulation.py`), *before* that tick's `WoundState.step()` --
each only ever raises its target field's floor, it doesn't lower it back
down (`DressingDisturbance` is the one exception: it explicitly relaxes
`moisture_target` back to baseline once its window passes, since a
resealed dressing doesn't keep leaking).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Tuple

from digital_twin.state import WoundState


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class Perturbation(Protocol):
    """Anything that nudges a WoundState's `*_target` fields forward one
    tick's worth of a scripted clinical event. `elapsed_days` is simulated
    time since the run started (or since the profile's initial_state was
    taken as tick 0) -- WoundState itself has no notion of "day", since
    it's paced by `dt_seconds`, not a calendar."""

    def apply(self, state: WoundState, elapsed_days: float) -> None: ...


@dataclass(frozen=True)
class InfectionOnset:
    """Bacterial load (and, right behind it, inflammation) rises toward
    `severity` starting at `onset_day`, ramping in over `ramp_days`.

    Generalizes the old rising_concentration ("gradual rise") and
    sudden_abnormal ("stable, then a sudden spike") scenarios as the same
    mechanism: `ramp_days` near zero reproduces a sudden spike, a larger
    value reproduces a gradual climb -- a parameter, not a second class.
    Only ever raises the floor -- a later, lower-severity InfectionOnset in
    the same profile can't undo an earlier, more severe one.
    """

    onset_day: float
    ramp_days: float = 1.0
    severity: float = 0.8
    inflammation_follow: float = 0.9  # inflammation_target tracks this fraction of bacterial_load_target

    def apply(self, state: WoundState, elapsed_days: float) -> None:
        if elapsed_days < self.onset_day:
            return
        progress = _clamp01((elapsed_days - self.onset_day) / max(self.ramp_days, 1e-6))
        target = self.severity * progress
        if target > state.bacterial_load_target:
            state.bacterial_load_target = _clamp01(target)
        inflammation_floor = target * self.inflammation_follow
        if inflammation_floor > state.inflammation_target:
            state.inflammation_target = _clamp01(inflammation_floor)


@dataclass(frozen=True)
class DressingDisturbance:
    """Raises `moisture_target` by `severity` for each window in
    `event_days`, relaxing back to `baseline_moisture_target` once the
    window passes -- a real dressing lets moisture in while disturbed and
    stops once it's resealed/replaced, unlike `InfectionOnset`'s
    one-directional floor.

    Generalizes the old high_noise (wider spread), sensor_disconnect
    (dropped to invalid) and comms_failure (cyclic packet loss) scenarios:
    a real physical disturbance of the dressing/electrode contact is what
    would actually widen noise or drop a reading (via the moisture ->
    fouling -> noise/dropout chain already in device_physics.py/
    observation.py), so those three become one physical event, timed and
    repeatable, instead of three unrelated fixed-index triggers.
    """

    event_days: Tuple[float, ...]
    severity: float = 0.4
    window_days: float = 0.25
    baseline_moisture_target: float = 0.5

    def apply(self, state: WoundState, elapsed_days: float) -> None:
        active = any(
            event_day <= elapsed_days < event_day + self.window_days
            for event_day in self.event_days
        )
        target = self.baseline_moisture_target + self.severity if active else self.baseline_moisture_target
        state.moisture_target = _clamp01(target)
