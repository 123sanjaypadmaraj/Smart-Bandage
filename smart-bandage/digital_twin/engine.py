"""
DT-3: the digital twin engine -- a tick loop decoupled from wall-clock time.

ScenarioSensor (simulator/sensors/scenario_sensor.py) paces every reading
off `time.monotonic() - self._t0`, which is exactly right for a live demo
(one reading per real second) but wrong for two things that don't want to
wait on a wall clock at all:

  - CI: a test asserting on "24 hours of electrode_degradation" shouldn't
    spend 24 hours -- or even 24 seconds of sleeping -- producing it.
  - Reproducibility: ScenarioSensor's noise draws from the global `random`
    module, so no two runs (even at the same wall-clock pace) trace the
    same path. A regression test that wants to pin an exact trajectory
    has nothing to pin to.

DigitalTwinEngine reuses the same scenario configs and signal generators
ScenarioSensor does (simulator/scenarios/, simulator/signals/generators.py)
-- same 7 scenarios, same fault contract -- but advances on a tick counter
instead of time.monotonic(), and draws every random number from a
`random.Random` instance seeded at construction. `engine.run(n)` produces a
day of trajectory in milliseconds; `DigitalTwinEngine(..., seed=1234)`
produces bit-for-bit the same trajectory every time it's built, on any
machine. A caller that instead wants real-time pacing just calls `.tick()`
once a second itself -- the engine never touches the wall clock either
way, so it can't tell (or care) which one it's being driven by.

Not a SensorInterface implementation itself -- SensorInterface speaks one
read per call, paced by whatever's calling read_measurement(). See
digital_twin/adapter.py for the thin wrapper that lets an engine stand in
for a ScenarioSensor behind that contract.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError
from simulator.scenarios.scenarios import ScenarioConfig, get_scenario
from simulator.signals.generators import (
    baseline_drift_noise,
    battery_drain,
    declining_quality,
    ou_step,
    physiological_ripple,
    ramp,
    step_then_spike,
    temperature,
)

# Same follow-speed constants ScenarioSensor uses (see its module docstring)
# -- kept identical so a scenario looks the same whether it's played back
# live or generated in bulk.
_RAMP_FOLLOW_ALPHA = 0.6
_SPIKE_FOLLOW_ALPHA = 0.85

# Fixed epoch a run's timestamps are laid out from when the caller doesn't
# supply one, so two runs with the same seed produce identical
# RawMeasurement.timestamp values too, not just identical signal values.
_DEFAULT_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


class DigitalTwinEngine:
    """ScenarioSensor's math, replayed on a tick counter instead of
    time.monotonic(), with every random draw seeded.

    One tick == one `dt_seconds` slice of simulated time. Whether those
    ticks are paced by a wall clock (a demo calling `.tick()` once a
    second) or fired back-to-back in a loop (`.run()` generating a day of
    trajectory in one call), the engine itself never sleeps or reads the
    clock, so both look identical to it.
    """

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        scenario: str = "normal",
        dt_seconds: float = 1.0,
        seed: Optional[int] = None,
        battery_start_pct: float = 100.0,
        battery_drain_pct_per_hour: float = 2.0,
        start_time: Optional[datetime] = None,
    ) -> None:
        if dt_seconds <= 0:
            raise ValueError("dt_seconds must be > 0")
        self.device_id = device_id
        self.channel_id = channel_id
        self.dt_seconds = dt_seconds
        self.battery_start_pct = battery_start_pct
        self.battery_drain_pct_per_hour = battery_drain_pct_per_hour
        self._start_time = start_time or _DEFAULT_EPOCH

        self._config: ScenarioConfig = get_scenario(scenario)
        self.seed = seed
        self._rng = random.Random(seed)
        self._running = False
        self._tick_count = 0
        self._connected = True
        self._last_error: Optional[str] = None
        self._last_quality = 0.95

        # Stateful noise/smoothing -- same split as ScenarioSensor: the
        # generators are pure math, this is what owns their running state.
        self._signal_noise = 0.0
        self._temp_noise = 0.0
        self._battery_wobble = 0.0
        self._smoothed_signal: Optional[float] = None

    @property
    def scenario_name(self) -> str:
        return self._config.name

    @property
    def elapsed_seconds(self) -> float:
        return self._tick_count * self.dt_seconds

    def set_scenario(self, scenario: str) -> None:
        """Switch scenarios without losing the running session or reseeding
        -- the tick counter resets so the new scenario's ramps/faults start
        from their own tick 0, exactly like ScenarioSensor.set_scenario()."""
        self._config = get_scenario(scenario)
        self._tick_count = 0
        self._connected = True
        self._last_error = None
        self._smoothed_signal = None

    def reset(self, seed: Optional[int] = None) -> None:
        """Rewind to tick 0 and reseed. The same seed reproduces the exact
        same run again; a different one (or None, for a fresh
        non-reproducible run) starts a new trajectory."""
        self.seed = seed
        self._rng = random.Random(seed)
        self._tick_count = 0
        self._connected = True
        self._last_error = None
        self._last_quality = 0.95
        self._signal_noise = 0.0
        self._temp_noise = 0.0
        self._battery_wobble = 0.0
        self._smoothed_signal = None

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def tick(self) -> RawMeasurement:
        """Advance one `dt_seconds` slice and return its reading. Raises
        SensorDisconnectedError / CommsTimeoutError on the same ticks
        ScenarioSensor.read_measurement() would raise on -- same scenario
        configs, same fault contract, just paced by tick count instead of
        the wall clock."""
        if not self._running:
            raise RuntimeError("call start() before tick()")

        cfg = self._config
        idx = self._tick_count
        elapsed = self.elapsed_seconds

        if cfg.disconnect is not None:
            try:
                cfg.disconnect.check(idx)
            except SensorDisconnectedError as exc:
                self._connected = False
                self._last_error = str(exc)
                self._tick_count += 1
                raise

        if cfg.comms_dropout is not None:
            try:
                cfg.comms_dropout.check(idx)
            except CommsTimeoutError as exc:
                self._last_error = str(exc)
                self._tick_count += 1
                raise

        # reached a live tick: mark connected again (comms_failure recovers)
        self._connected = True
        self._last_error = None

        noise_std = cfg.noise_std
        if cfg.noise_fault is not None:
            noise_std = cfg.noise_fault.apply(noise_std)

        self._signal_noise = ou_step(self._signal_noise, noise_std, rng=self._rng)
        ripple = physiological_ripple(elapsed)

        if cfg.ramp_checkpoints is not None:
            target = ramp(idx, cfg.ramp_checkpoints)
            self._smoothed_signal = self._ease_toward(target, _RAMP_FOLLOW_ALPHA)
            signal = self._smoothed_signal + self._signal_noise + ripple
        elif cfg.spike_checkpoints is not None and cfg.spike_at_index is not None:
            target = step_then_spike(
                idx,
                stable_value=cfg.baseline,
                spike_checkpoints=cfg.spike_checkpoints,
                spike_at_index=cfg.spike_at_index,
            )
            self._smoothed_signal = self._ease_toward(target, _SPIKE_FOLLOW_ALPHA)
            signal = self._smoothed_signal + self._signal_noise + ripple
        else:
            signal = (
                baseline_drift_noise(
                    elapsed,
                    baseline=cfg.baseline,
                    drift_per_second=cfg.drift_per_second,
                    noise=self._signal_noise,
                )
                + ripple
            )

        if cfg.degradation:
            jitter = self._rng.gauss(0.0, 0.004)
            self._last_quality = declining_quality(idx, jitter=jitter)
        elif cfg.noise_fault is not None:
            self._last_quality = max(0.1, 0.95 - (cfg.noise_fault.multiplier - 1) * 0.08)
        else:
            self._last_quality = 0.95

        self._temp_noise = ou_step(self._temp_noise, 0.2, rng=self._rng)
        self._battery_wobble = ou_step(self._battery_wobble, 0.15, rng=self._rng)
        battery = battery_drain(
            elapsed,
            start_pct=self.battery_start_pct,
            drain_pct_per_hour=self.battery_drain_pct_per_hour,
            wobble=self._battery_wobble,
        )

        timestamp = self._start_time + timedelta(seconds=elapsed)
        self._tick_count += 1

        return RawMeasurement(
            device_id=self.device_id,
            channel_id=self.channel_id,
            timestamp=timestamp,
            raw_signal=round(signal, 4),
            temperature=round(temperature(elapsed, noise=self._temp_noise), 2),
            battery=round(battery),
        )

    def run(
        self, n_ticks: int, stop_on_fault: bool = False
    ) -> List[Union[RawMeasurement, Exception]]:
        """Bulk-generate `n_ticks` of trajectory in one call -- the "hours
        or days of trajectory in seconds" path `.tick()` alone doesn't give
        you. Starts the engine if it isn't already running.

        A tick whose scenario fault fires contributes its exception instead
        of a reading (matching MultiChannelSensor.read_all()'s per-channel
        isolation) and the run continues, unless `stop_on_fault` says to
        stop there instead -- useful for "generate until it disconnects".
        """
        if not self._running:
            self.start()
        results: List[Union[RawMeasurement, Exception]] = []
        for _ in range(n_ticks):
            try:
                results.append(self.tick())
            except (SensorDisconnectedError, CommsTimeoutError) as exc:
                results.append(exc)
                if stop_on_fault:
                    break
        return results

    def _ease_toward(self, target: float, alpha: float) -> float:
        """Move the displayed signal a fraction of the way from where it
        currently is toward `target`, instead of snapping straight to it."""
        if self._smoothed_signal is None:
            return target
        return self._smoothed_signal + alpha * (target - self._smoothed_signal)

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=self._connected,
            battery=round(
                battery_drain(
                    self.elapsed_seconds,
                    start_pct=self.battery_start_pct,
                    drain_pct_per_hour=self.battery_drain_pct_per_hour,
                )
            ),
            signal_quality=round(self._last_quality, 4),
            last_error=self._last_error,
        )
