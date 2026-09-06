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

DisconnectAfter and CommsDropoutCycle (below) decide *when* to raise from a
scripted read index -- right for a scenario with a fixed script, but not
for digital_twin/observation.py (DT-2), where BLE link quality is a running
process (digital_twin/device_physics.py:LinkQualityProcess) that can wander
down and recover on its own. ProbabilisticDropout and SustainedDisconnect
below are the continuous-condition analogues: same two exception shapes,
raised from a live link-quality value instead of a fixed cycle position.
"""
from __future__ import annotations

import random


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


class ProbabilisticDropout:
    """Continuous-probability packet loss, driven by a live link-quality
    value in [0, 1] (DT-2: BLE link quality as a running process) rather
    than a scripted index cycle. Below `floor_quality` the drop probability
    rises linearly with the deficit, capped at `max_drop_probability`; at
    or above the floor, reads are always delivered. Reuses
    CommsTimeoutError -- the same failure shape a caller already knows how
    to handle from CommsDropoutCycle -- so this is a drop-in alternative
    trigger, not a new fault type.
    """

    def __init__(self, floor_quality: float = 0.5, max_drop_probability: float = 0.9) -> None:
        self.floor_quality = floor_quality
        self.max_drop_probability = max_drop_probability

    def check(self, link_quality: float) -> None:
        if link_quality >= self.floor_quality:
            return
        deficit = (self.floor_quality - link_quality) / self.floor_quality
        drop_probability = min(self.max_drop_probability, deficit)
        if random.random() < drop_probability:
            raise CommsTimeoutError(
                f"no packet received (link quality {link_quality:.2f} below "
                f"reliable floor {self.floor_quality:.2f})"
            )


class SustainedDisconnect:
    """Raises SensorDisconnectedError once link quality has stayed below
    `floor_quality` for `sustained_reads` consecutive reads, and then keeps
    raising it on every later check -- matching DisconnectAfter's "stays
    disconnected" behavior (Blueprint scenario 6), but the continuous
    analogue: a sustained *condition* trips it, not a fixed read count.
    """

    def __init__(self, floor_quality: float = 0.15, sustained_reads: int = 5) -> None:
        self.floor_quality = floor_quality
        self.sustained_reads = sustained_reads
        self._consecutive_bad = 0
        self._tripped = False

    def check(self, link_quality: float) -> None:
        if self._tripped:
            raise SensorDisconnectedError("device stopped responding (sustained link failure)")
        if link_quality < self.floor_quality:
            self._consecutive_bad += 1
        else:
            self._consecutive_bad = 0
        if self._consecutive_bad >= self.sustained_reads:
            self._tripped = True
            raise SensorDisconnectedError(
                f"link quality below {self.floor_quality:.2f} for "
                f"{self._consecutive_bad} consecutive reads"
            )


class NoiseAmplifier:
    """Multiplies the base noise_std a generator would otherwise use
    (Blueprint scenario 5: high noise / degraded SNR)."""

    def __init__(self, multiplier: float = 8.0) -> None:
        self.multiplier = multiplier

    def apply(self, noise_std: float) -> float:
        return noise_std * self.multiplier
