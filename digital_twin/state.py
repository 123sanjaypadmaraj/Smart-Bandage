"""
DT-2: the physiological state a device's channels are observations of.

simulator/scenarios.py scripts each channel's signal independently (a
checkpoint list per channel). That's fine for exercising one failure mode
in isolation, but it can't produce the thing DT-2 unlocks: several channels
moving together because they share one underlying cause. WoundState is
that shared cause -- one instance per device, read (never scripted) by
every channel's transfer function in digital_twin/observation.py.

Deliberately small and not a clinical model of wound healing -- four
latent variables, each a bounded mean-reverting random walk -- just enough
shared structure for multiple electrodes to plausibly respond to the same
underlying process, the way real channels on the same wound bed do.

Consolidation note (DT-1/DT-3/DT-4/DT-7): this is now the *only*
physiological state model in digital_twin/ -- DT-1's separate DeviceState
(logistic bacterial-load growth, never wired into anything) and DT-4's
separate day-paced WoundState (digital_twin/profile_state.py) both existed
alongside this one at various points during concurrent development; both
were retired in favor of this one, which is what observation.py/
device_physics.py/backend/app/simulation.py actually run. DT-4's
perturbations (digital_twin/perturbations.py) now retarget this
WoundState's `*_target` fields directly instead of stepping a model of
their own -- see that module.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional


def _revert(
    current: float,
    target: float,
    rate_per_second: float,
    vol: float,
    dt_seconds: float,
    lo: float,
    hi: float,
    rng: Optional[random.Random] = None,
) -> float:
    """One step of a mean-reverting random walk toward `target` -- the same
    Ornstein-Uhlenbeck process simulator/signals/generators.py:ou_step
    already uses for per-read noise, generalized to a non-zero `target` and
    an arbitrary `dt_seconds` instead of a fixed per-tick `phi`.

    Uses the *exact* OU transition (decay = exp(-rate * dt), innovation
    variance = vol^2 * (1 - decay^2) / (2 * rate)) rather than an Euler
    step -- a plain `current + rate * (target - current) * dt` blows past
    `target` (and the clamp below) once `rate * dt_seconds` exceeds ~1,
    which a running process advanced by real wall-clock elapsed time will
    eventually hit. The exact transition stays correct for any dt_seconds,
    including a large jump after a paused/resumed session.

    `rng` draws the innovation from a caller-owned `random.Random` instead
    of the global `random` module -- digital_twin/engine.py passes a seeded
    one so a run is reproducible; every existing caller (unseeded) keeps
    behaving exactly as before, same as simulator/signals/generators.py:ou_step.
    """
    if rate_per_second <= 0 or dt_seconds <= 0:
        return max(lo, min(hi, current))
    decay = math.exp(-rate_per_second * dt_seconds)
    reverted = target + (current - target) * decay
    noise_std = vol * math.sqrt((1.0 - decay * decay) / (2.0 * rate_per_second))
    generator = rng if rng is not None else random
    innovation = generator.gauss(0.0, noise_std)
    return max(lo, min(hi, reverted + innovation))


@dataclass
class WoundState:
    """Latent physiological state shared by every channel on a device.

    Each variable reverts toward its own `*_target` -- retarget one (e.g.
    `state.bacterial_load_target = 0.9` to script an infection onset) to
    move the whole state over time, the continuous analogue of
    simulator/scenarios.py's checkpoint list. `step()` is the "running
    process"; nothing in digital_twin/observation.py ever sets these
    values directly. digital_twin/perturbations.py is the other legitimate
    caller of `*_target` -- see its module docstring.
    """

    inflammation: float = 0.1  # 0 (none) .. 1.5 (severe) -- CRP/pathogen + temperature + pH
    bacterial_load: float = 0.05  # 0 (sterile) .. 1.5 (heavily colonized) -- pathogen + pH
    moisture: float = 0.5  # 0 (dry) .. 1 (saturated) -- impedance baseline + electrode fouling rate
    perfusion: float = 0.7  # 0 (poor) .. 1 (well-perfused) -- temperature + baseline stability

    inflammation_target: float = 0.1
    bacterial_load_target: float = 0.05
    moisture_target: float = 0.5
    perfusion_target: float = 0.7

    def step(self, dt_seconds: float, rng: Optional[random.Random] = None) -> None:
        """Advance the state by `dt_seconds` of wall-clock time. A no-op
        for dt_seconds <= 0 so repeated reads at (near-)the same instant --
        e.g. one channel read right after another in the same cycle --
        don't each perturb the shared state independently.

        `rng`: an optional caller-owned `random.Random` for reproducible
        runs (see `_revert`'s docstring) -- `DigitalTwinDevice` always
        passes its own seeded instance; direct callers/tests that omit it
        get the previous (global-`random`, unseeded) behavior.
        """
        if dt_seconds <= 0:
            return
        self.inflammation = _revert(
            self.inflammation, self.inflammation_target, 0.15, 0.012, dt_seconds, 0.0, 1.5, rng
        )
        self.bacterial_load = _revert(
            self.bacterial_load, self.bacterial_load_target, 0.12, 0.010, dt_seconds, 0.0, 1.5, rng
        )
        self.moisture = _revert(
            self.moisture, self.moisture_target, 0.25, 0.015, dt_seconds, 0.0, 1.0, rng
        )
        self.perfusion = _revert(
            self.perfusion, self.perfusion_target, 0.2, 0.012, dt_seconds, 0.0, 1.0, rng
        )
