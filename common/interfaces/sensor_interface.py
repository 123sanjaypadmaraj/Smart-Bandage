"""
The hardware abstraction layer (Blueprint §3 / §5, "the most important
architectural decision").

Nothing above this line may import ESP32 registers, an AFE part number,
or anything simulator-specific. RealSensor and SimulatedSensor both
implement this contract; the rest of the application only ever depends
on SensorInterface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement


class SensorInterface(ABC):
    """Contract every sensor backend (real or simulated) must satisfy."""

    @abstractmethod
    def initialize(self) -> None:
        """One-time setup: open the transport, reset the AFE/model state."""
        raise NotImplementedError

    @abstractmethod
    def start_measurement(self) -> None:
        """Begin a measurement session (arm the electrode / start the model clock)."""
        raise NotImplementedError

    @abstractmethod
    def read_measurement(self) -> RawMeasurement:
        """Return exactly one RawMeasurement. Called on a fixed interval by the caller."""
        raise NotImplementedError

    @abstractmethod
    def stop_measurement(self) -> None:
        """End the measurement session and release/quiesce the sensor."""
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> DeviceStatus:
        """Battery, connectivity, signal quality — independent of the last reading."""
        raise NotImplementedError
