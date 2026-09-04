"""
Phase 8 hardware/software integration (Blueprint §10 Phase 8: "swap
SimulatedSensor for RealSensor. Nothing downstream changes.").

`Transport` is the boundary `RealSensor` (common/interfaces/real_sensor.py)
depends on instead of a specific link -- BLE GATT notifications, a
USB-serial gateway, whatever Phase 7 hardware ends up using. Exactly the
same abstraction move `SensorInterface` makes one level up
(common/interfaces/sensor_interface.py): nothing above this line may
assume BLE vs. serial, so swapping one for the other, or a real device for
a test double, never touches `RealSensor` itself.
"""
from __future__ import annotations

import struct
import time
from abc import ABC, abstractmethod

from common.protocol.packet import MAGIC, PACKET_SIZE


class TransportTimeoutError(TimeoutError):
    """No full packet arrived within the caller's timeout."""


class Transport(ABC):
    @abstractmethod
    def open(self) -> None:
        """One-time setup: open the serial port / connect the BLE link."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_packet(self, timeout: float) -> bytes:
        """Blocks until one full `common.protocol.packet.PACKET_SIZE`-byte
        frame is available, or raises TransportTimeoutError."""
        raise NotImplementedError

    def write_time_sync(self, unix_seconds: int) -> None:
        """Best-effort: not every transport can push a clock sync back to
        the device (e.g. a receive-only serial link synced some other
        way). Default no-op; a transport that can override it."""
        return None


class StreamTransport(Transport):
    """
    Frames `Transport.read_packet()` on top of anything duck-typed like
    `serial.Serial` -- a `.read(n) -> bytes` that returns 0-n bytes
    (never blocks past what's immediately available) and an optional
    `.write(bytes)`. Works unmodified with a real pyserial port, a BLE
    notification-queue adapter, or (for tests) an in-memory fake -- this
    class never imports pyserial or any BLE library, so it has nothing
    hardware-specific to get wrong.

    Resyncs on `MAGIC`: the first byte of a frame must be
    `common.protocol.packet.MAGIC`; anything else is discarded one byte at
    a time until a magic byte turns up (or the timeout expires). Without
    this, a receiver that attaches mid-stream, or recovers from one
    corrupted frame, would misinterpret whatever's left of that frame as
    the start of the next one and stay desynced forever.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def open(self) -> None:
        opener = getattr(self._stream, "open", None)
        if callable(opener):
            opener()

    def close(self) -> None:
        closer = getattr(self._stream, "close", None)
        if callable(closer):
            closer()

    def read_packet(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout

        first = self._read_exact(1, deadline)
        while first[0] != MAGIC:
            first = self._read_exact(1, deadline)

        rest = self._read_exact(PACKET_SIZE - 1, deadline)
        return first + rest

    def write_time_sync(self, unix_seconds: int) -> None:
        writer = getattr(self._stream, "write", None)
        if callable(writer):
            writer(struct.pack("<I", unix_seconds))

    def _read_exact(self, n: int, deadline: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            if time.monotonic() > deadline:
                raise TransportTimeoutError(f"timed out waiting for {n - len(buf)} more byte(s)")
            chunk = self._stream.read(n - len(buf))
            if chunk:
                buf.extend(chunk)
        return bytes(buf)
