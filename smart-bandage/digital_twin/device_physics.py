"""
DT-2 device physics: battery drain, electrode fouling, and BLE link quality
as continuously running processes, each advanced by however much real time
(`dt_seconds`) elapsed since its last `step()` call -- the same running-clock
pattern simulator/sensors/scenario_sensor.py already uses for its noise
processes (`ou_step`), extended here to the device-physics values
themselves and coupled to WoundState (digital_twin/state.py) so device
health responds to the wound, not just the clock:

  - a poor BLE link burns extra battery retransmitting
  - a wetter, more colonized wound bed fouls its electrode faster

This replaces the fixed schedules simulator/scenarios.py uses for the same
three failure surfaces (`battery_drain`'s closed-form elapsed-time formula,
`declining_quality`'s checkpoint curve, `CommsDropoutCycle`'s fixed
ok/drop index cycle) with running state that can go up or down, not just
follow a script.

Consolidation note (DT-3/DT-7): every `step()` below takes an optional
`rng` so `DigitalTwinDevice` (observation.py) can drive these processes
from one seeded `random.Random` per device -- what makes
`digital_twin/engine.py`'s seeded, bit-for-bit-reproducible runs possible.
Omitting `rng` keeps the previous (global-`random`, unseeded) behavior for
existing direct callers/tests.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from simulator.signals.generators import ou_step


class BatteryProcess:
    """State of charge as a running process, in percent.

    Same shape as simulator/signals/generators.py:battery_drain (monotonic
    drain plus a small wobble) but tick-by-tick and rate-coupled to link
    quality instead of a closed-form function of total elapsed time.

    `_true_pct` only ever decreases (the actual charge remaining);
    `.pct` overlays the current wobble on top of it for display, the way a
    real fuel gauge's reported percentage jitters a little from one read to
    the next without the underlying charge actually going up and down.
    Bug note: an earlier version added `_wobble * dt_seconds` directly into
    a single running `pct` every step, so the jitter never un-happened --
    over a long run (hours/days, exactly what digital_twin/engine.py's bulk
    generation and DT-5's backtests actually do) that additive jitter
    behaves like a slow random walk on top of the real drain, drifting the
    reported percentage tens of points off the true one. Recomputing `.pct`
    fresh from `_true_pct` + the *current* wobble each time fixes that: the
    jitter can only ever be off by one tick's wobble, never by its
    accumulated history.
    """

    def __init__(self, start_pct: float = 100.0, base_drain_pct_per_hour: float = 2.0) -> None:
        self._true_pct = start_pct
        self.base_drain_pct_per_hour = base_drain_pct_per_hour
        self._wobble = 0.0

    @property
    def pct(self) -> float:
        return max(0.0, min(100.0, self._true_pct + self._wobble))

    def step(self, dt_seconds: float, link_quality: float, rng: Optional[random.Random] = None) -> float:
        if dt_seconds <= 0:
            return round(self.pct, 2)
        # a poor link means more retries/retransmits on the radio, which
        # costs real battery -- up to 2.5x the base drain rate at quality 0
        retry_penalty = 1.0 + 1.5 * (1.0 - link_quality)
        drained = self.base_drain_pct_per_hour * retry_penalty * (dt_seconds / 3600.0)
        self._true_pct = max(0.0, min(100.0, self._true_pct - drained))
        self._wobble = ou_step(self._wobble, 0.15, rng=rng)
        return round(self.pct, 2)


class ElectrodeFoulingProcess:
    """Protein/biofilm buildup on one electrode, in [0, 1].

    Continuous analogue of simulator/signals/generators.py:declining_quality's
    fixed checkpoint curve -- accumulates faster with more moisture and
    bacterial load at the wound bed (state-dependent) instead of following a
    scripted schedule. One instance per channel: electrodes foul
    independently even though they share the same wound-bed state.
    """

    def __init__(self) -> None:
        self.level = 0.0

    def step(
        self,
        dt_seconds: float,
        moisture: float,
        bacterial_load: float,
        rng: Optional[random.Random] = None,
    ) -> float:
        if dt_seconds <= 0:
            return self.level
        accumulation_per_hour = 0.02 * (0.3 + moisture) * (0.5 + bacterial_load)
        drift = accumulation_per_hour * (dt_seconds / 3600.0)
        # jitter is a pure (non-mean-reverting) random walk, so its stdev
        # over any interval grows with sqrt(elapsed seconds) regardless of
        # how finely it's ticked -- keep its per-sqrt-second coefficient
        # small enough that accumulated jitter over a multi-hour run stays
        # well below the deterministic drift above, or the state-dependent
        # signal this process exists to carry gets swamped by noise.
        generator = rng if rng is not None else random
        jitter = generator.gauss(0.0, 0.00005 * math.sqrt(dt_seconds))
        self.level = max(0.0, min(1.0, self.level + drift + jitter))
        return self.level


class LinkQualityProcess:
    """BLE link quality in [0, 1] (RSSI-like), a mean-reverting random walk
    around a healthy set point.

    This is the continuous process simulator/faults/faults.py's
    ProbabilisticDropout and SustainedDisconnect read from, replacing
    CommsDropoutCycle's fixed ok/drop index cycle with a value that can
    wander down and recover on its own.
    """

    _RATE_PER_SECOND = 0.3
    _VOL = 0.05

    def __init__(self, start: float = 0.95, target: float = 0.92) -> None:
        self.value = start
        self.target = target

    def step(self, dt_seconds: float, rng: Optional[random.Random] = None) -> float:
        if dt_seconds <= 0:
            return self.value
        # exact OU transition, not an Euler step -- see
        # digital_twin/state.py:_revert's docstring for why: a plain
        # `value + rate * (target - value) * dt` overshoots once
        # `rate * dt` exceeds ~1, which a large dt_seconds hits easily.
        decay = math.exp(-self._RATE_PER_SECOND * dt_seconds)
        reverted = self.target + (self.value - self.target) * decay
        noise_std = self._VOL * math.sqrt((1.0 - decay * decay) / (2.0 * self._RATE_PER_SECOND))
        generator = rng if rng is not None else random
        innovation = generator.gauss(0.0, noise_std)
        self.value = max(0.0, min(1.0, reverted + innovation))
        return self.value
