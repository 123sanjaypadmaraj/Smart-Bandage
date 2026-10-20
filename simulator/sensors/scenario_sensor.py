"""
Phase 2 deliverable: a virtual bandage that can be pointed at any of the 7
scenarios in simulator/scenarios/scenarios.py, still satisfying
SensorInterface exactly like the Phase 1 SimulatedSensor.

Kept separate from simulator/sensors/simulated_sensor.py (that one stays as
the minimal Phase 1 reference implementation the contract tests pin to).
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Optional

from common.interfaces.sensor_interface import SensorInterface
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

# How fast the displayed signal chases a checkpoint target, per read. Higher
# = snappier. Ramps (a gradual trend) ease in slowly so the climb reads as a
# curve; the spike scenario is deliberately "sudden", so it eases in fast --
# still a hair short of an instant snap, like a real sensor's response time.
_RAMP_FOLLOW_ALPHA = 0.6
_SPIKE_FOLLOW_ALPHA = 0.85


class ScenarioSensor(SensorInterface):
    """One simulated channel, running one scenario at a time.

    `set_scenario()` can be called at any point after `initialize()` —
    including mid-run — so the API's `/simulation/scenario` endpoint (Phase
    4) can inject a fault into an already-running virtual bandage.
    """

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        scenario: str = "normal",
        battery_start_pct: float = 100.0,
        battery_drain_pct_per_hour: float = 2.0,
    ) -> None:
        self.device_id = device_id
        self.channel_id = channel_id
        self.battery_start_pct = battery_start_pct
        self.battery_drain_pct_per_hour = battery_drain_pct_per_hour

        self._config: ScenarioConfig = get_scenario(scenario)
        self._running = False
        self._t0: Optional[float] = None
        self._read_index = 0
        self._connected = True
        self._last_error: Optional[str] = None
        self._last_quality = 0.95

        # Stateful noise/smoothing the generators (stateless math) don't
        # own themselves -- see simulator/signals/generators.py.
        self._signal_noise = 0.0
        self._temp_noise = 0.0
        self._battery_wobble = 0.0
        self._smoothed_signal: Optional[float] = None

    def set_scenario(self, scenario: str) -> None:
        """Switch scenarios without losing the running session — read_index
        resets so ramps/faults in the new scenario start from their own t0."""
        self._config = get_scenario(scenario)
        self._read_index = 0
        self._connected = True
        self._last_error = None
        self._smoothed_signal = None

    @property
    def scenario_name(self) -> str:
        return self._config.name

    def initialize(self) -> None:
        self._running = False
        self._t0 = None
        self._read_index = 0
        self._connected = True
        self._last_error = None
        self._last_quality = 0.95
        self._signal_noise = 0.0
        self._temp_noise = 0.0
        self._battery_wobble = 0.0
        self._smoothed_signal = None

    def start_measurement(self) -> None:
        self._running = True
        self._t0 = time.monotonic()

    def read_measurement(self) -> RawMeasurement:
        if not self._running or self._t0 is None:
            raise RuntimeError("call start_measurement() before read_measurement()")

        cfg = self._config
        idx = self._read_index
        elapsed = time.monotonic() - self._t0

        # scenario 6: disconnect
        if cfg.disconnect is not None:
            try:
                cfg.disconnect.check(idx)
            except SensorDisconnectedError as exc:
                self._connected = False
                self._last_error = str(exc)
                self._read_index += 1
                raise

        # scenario 7: comms dropout
        if cfg.comms_dropout is not None:
            try:
                cfg.comms_dropout.check(idx)
            except CommsTimeoutError as exc:
                self._last_error = str(exc)
                self._read_index += 1
                raise

        # reached a live read: mark connected again (comms_failure recovers)
        self._connected = True
        self._last_error = None

        noise_std = cfg.noise_std
        if cfg.noise_fault is not None:
            noise_std = cfg.noise_fault.apply(noise_std)

        # One correlated noise process for the whole read -- each tick
        # wanders from the last instead of landing independently (see
        # generators.ou_step) -- plus a small cosmetic ripple on top.
        self._signal_noise = ou_step(self._signal_noise, noise_std)
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
            jitter = random.gauss(0.0, 0.004)
            self._last_quality = declining_quality(idx, jitter=jitter)
        elif cfg.noise_fault is not None:
            self._last_quality = max(0.1, 0.95 - (cfg.noise_fault.multiplier - 1) * 0.08)
        else:
            self._last_quality = 0.95

        self._temp_noise = ou_step(self._temp_noise, 0.2)
        self._battery_wobble = ou_step(self._battery_wobble, 0.15)
        battery = battery_drain(
            elapsed,
            start_pct=self.battery_start_pct,
            drain_pct_per_hour=self.battery_drain_pct_per_hour,
            wobble=self._battery_wobble,
        )

        self._read_index += 1

        return RawMeasurement(
            device_id=self.device_id,
            channel_id=self.channel_id,
            timestamp=datetime.now(timezone.utc),
            raw_signal=round(signal, 4),
            temperature=round(temperature(elapsed, noise=self._temp_noise), 2),
            battery=round(battery),
        )

    def _ease_toward(self, target: float, alpha: float) -> float:
        """Move the displayed signal a fraction of the way from where it
        currently is toward `target`, instead of snapping straight to it."""
        if self._smoothed_signal is None:
            return target
        return self._smoothed_signal + alpha * (target - self._smoothed_signal)

    def stop_measurement(self) -> None:
        self._running = False

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=self._connected,
            battery=round(
                battery_drain(
                    (time.monotonic() - self._t0) if self._t0 is not None else 0.0,
                    start_pct=self.battery_start_pct,
                    drain_pct_per_hour=self.battery_drain_pct_per_hour,
                )
            ),
            signal_quality=round(self._last_quality, 4),
            last_error=self._last_error,
        )
