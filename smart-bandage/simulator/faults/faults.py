"""
Phase 2 fault injection (Blueprint §7 scenarios 3, 4, 5, 6, 7).

These mirror failure modes a *real* ESP32 + AFE + electrode will eventually
produce, so the software built against the simulator (Phase 3 processing,
Phase 4 backend, Phase 5 dashboard, Phase 6 alerting) already knows how to
survive them before real hardware exists.

Two faults raise instead of returning a degraded value, because that's how
a real read failure actually surfaces to a caller: the transport times out
or the device stops responding. `ScenarioSensor.read_measurement()` lets
these propagate; callers (the ingestion pipeline, the backend) are expected
to catch them and record status="error", same as they would for a real
dropped BLE packet.
"""
from __future__ import annotations


class SensorDisconnectedError(RuntimeError):
    """The sensor stopped responding (Blueprint scenario 6: sensor disconnect)."""


class CommsTimeoutError(RuntimeError):
    """A read was attempted but the transport didn't deliver a packet in time
    (Blueprint scenario 7: communication failure)."""


class DisconnectAfter:
    """Raises SensorDisconnectedError once `read_index >= trigger_at`, and stays
    disconnected (matches Blueprint scenario 6: "VALID x3 -> INVALID x3...")."""

    def __init__(self, trigger_at: int) -> None:
        self.trigger_at = trigger_at

    def check(self, read_index: int) -> None:
        if read_index >= self.trigger_at:
            raise SensorDisconnectedError(
                f"device stopped responding after {self.trigger_at} reads"
            )


class CommsDropoutCycle:
    """
    Cyclic packet-loss pattern: `ok_count` good reads, then `drop_count`
    dropped reads, repeating (Blueprint scenario 7: "packet x3 -> dropped
    x3 -> packet resumes").
    """

    def __init__(self, ok_count: int = 3, drop_count: int = 3) -> None:
        if ok_count < 1 or drop_count < 1:
            raise ValueError("ok_count and drop_count must both be >= 1")
        self.ok_count = ok_count
        self.drop_count = drop_count

    def check(self, read_index: int) -> None:
        cycle_len = self.ok_count + self.drop_count
        position = read_index % cycle_len
        if position >= self.ok_count:
            raise CommsTimeoutError(
                f"no packet received (dropout window, position {position - self.ok_count + 1}/{self.drop_count})"
            )


class NoiseAmplifier:
    """Multiplies the base noise_std a generator would otherwise use
    (Blueprint scenario 5: high noise / degraded SNR)."""

    def __init__(self, multiplier: float = 8.0) -> None:
        self.multiplier = multiplier

    def apply(self, noise_std: float) -> float:
        return noise_std * self.multiplier
