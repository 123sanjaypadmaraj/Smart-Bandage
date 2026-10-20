"""
Reference implementation of SensorInterface — proves the Phase 1 contract
end-to-end with a real (if simple) signal model.

This is intentionally minimal. It is NOT the Phase 2 simulator described
in the blueprint (multi-channel, the 7 fault/noise/drift scenarios,
temperature and battery generators): that lives in simulator/scenarios/,
simulator/signals/ and simulator/faults/ and builds on top of this class.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from common.interfaces.sensor_interface import SensorInterface
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement


class SimulatedSensor(SensorInterface):
    """
    signal(t) = baseline + drift(t) + noise

    Matches the shape of Blueprint §11's raw-signal model without yet
    including the recognition/temperature terms — those arrive in Phase 2.
    """

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        baseline: float = 100.0,
        noise_std: float = 1.5,
        drift_per_second: float = 0.02,
    ) -> None:
        self.device_id = device_id
        self.channel_id = channel_id
        self.baseline = baseline
        self.noise_std = noise_std
        self.drift_per_second = drift_per_second
        self._running = False
        self._t0: float | None = None
        self._battery = 100.0

    def initialize(self) -> None:
        self._running = False
        self._t0 = None

    def start_measurement(self) -> None:
        self._running = True
        self._t0 = time.monotonic()

    def read_measurement(self) -> RawMeasurement:
        if not self._running or self._t0 is None:
            raise RuntimeError("call start_measurement() before read_measurement()")

        elapsed = time.monotonic() - self._t0
        drift = self.drift_per_second * elapsed
        noise = random.gauss(0.0, self.noise_std)
        signal = self.baseline + drift + noise

        # slow, monotonic battery drain so device-status has something to show
        self._battery = max(0.0, self._battery - random.uniform(0.0, 0.01))

        return RawMeasurement(
            device_id=self.device_id,
            channel_id=self.channel_id,
            timestamp=datetime.now(timezone.utc),
            raw_signal=round(signal, 4),
            temperature=round(36.5 + random.gauss(0.0, 0.2), 2),
            battery=round(self._battery),
        )

    def stop_measurement(self) -> None:
        self._running = False

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=True,
            battery=round(self._battery),
            signal_quality=0.95,
            last_error=None,
        )
