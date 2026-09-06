"""
DT-2: the state -> RawMeasurement mapping per channel.

simulator/scenarios.py's ScenarioSensor is a good reference for the shape
of a RawMeasurement (baseline + drift + noise, plus temperature/battery/
quality) but each channel there runs its own independent script. Here the
transfer function is state-dependent: every channel on a device reads the
same WoundState (digital_twin/state.py) through its own ChannelProfile, and
every device physics quantity (digital_twin/device_physics.py) is a running
process instead of a closed-form function of elapsed time or a scripted
checkpoint curve.

That shared state is what "unlocks cross-channel coupling" -- one rise in
`WoundState.inflammation` nudges the pathogen channel, temperature, and any
other inflammation-sensitive channel at once, correlated, the way one real
physiological cause actually shows up across several electrodes on the same
wound bed.

Not a SensorInterface implementation -- SensorInterface is single-channel
by design (common/interfaces/sensor_interface.py); this mirrors
simulator/sensors/multi_channel_sensor.py's per-device, per-channel
composition instead, so it's a drop-in alternative data source for the same
callers (ingestion pipeline, backend simulation endpoints) once wired in.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.device_physics import BatteryProcess, ElectrodeFoulingProcess, LinkQualityProcess
from digital_twin.state import WoundState
from simulator.faults.faults import (
    CommsTimeoutError,
    NoiseAmplifier,
    ProbabilisticDropout,
    SensorDisconnectedError,
    SustainedDisconnect,
)
from simulator.signals.generators import ou_step, temperature as temperature_generator


@dataclass(frozen=True)
class ChannelProfile:
    """How one channel's electrode responds to the shared WoundState --
    the per-channel half of the state -> RawMeasurement transfer function.
    `*_gain` is how many raw_signal units a full swing (0 -> 1, or 0 -> 1.5
    for the two variables whose plausible range runs that high) of the
    corresponding WoundState variable is worth for this channel; a channel
    left at 0.0 for a variable simply doesn't respond to it.
    """

    sensor_type: str
    baseline: float = 100.0
    inflammation_gain: float = 0.0
    bacterial_load_gain: float = 0.0
    moisture_gain: float = 0.0
    perfusion_gain: float = 0.0
    noise_std: float = 1.5


# Presets for the sensor_type examples already documented in
# common/schemas/device.py:SensorChannel and docs/architecture/data_dictionary.md.
# sensor_type is deliberately free-form there (no fixed vocabulary -- real
# assay names vary by electrode/aptamer design), so an unrecognized value
# falls back to _GENERIC_PROFILE below rather than raising, unlike
# simulator/scenarios.py:get_scenario's closed set of 7 named scenarios.
_CHANNEL_PROFILES: dict[str, ChannelProfile] = {
    "pathogen_channel_1": ChannelProfile(
        sensor_type="pathogen_channel_1",
        baseline=100.0,
        inflammation_gain=40.0,
        bacterial_load_gain=60.0,
        moisture_gain=5.0,
        noise_std=1.5,
    ),
    "glucose": ChannelProfile(
        sensor_type="glucose",
        baseline=90.0,
        inflammation_gain=10.0,
        moisture_gain=8.0,
        perfusion_gain=6.0,
        noise_std=1.2,
    ),
    "pH": ChannelProfile(
        sensor_type="pH",
        baseline=100.0,
        inflammation_gain=15.0,
        bacterial_load_gain=25.0,
        moisture_gain=10.0,
        noise_std=1.0,
    ),
}

_GENERIC_PROFILE = ChannelProfile(sensor_type="generic", baseline=100.0, noise_std=1.5)


def get_channel_profile(sensor_type: str) -> ChannelProfile:
    """Known sensor_type -> its preset ChannelProfile; anything else ->
    a flat generic profile (still noisy, still fouls, just insensitive to
    WoundState -- a safe default for a sensor_type this module doesn't
    know about yet)."""
    return _CHANNEL_PROFILES.get(sensor_type, _GENERIC_PROFILE)


class DigitalTwinDevice:
    """One simulated device: a shared WoundState, shared battery/link-quality
    processes, and one ElectrodeFoulingProcess + ChannelProfile per channel.

    `channels` maps channel_id -> sensor_type (see common/schemas/device.py:
    SensorChannel.sensor_type), so callers describe *what* each electrode is
    for; this class decides *how* that channel's electrode responds to the
    shared state.
    """

    def __init__(
        self,
        device_id: str,
        channels: Dict[str, str],
        state: Optional[WoundState] = None,
        battery_start_pct: float = 100.0,
        battery_drain_pct_per_hour: float = 2.0,
    ) -> None:
        self.device_id = device_id
        self.channels = dict(channels)  # channel_id -> sensor_type
        self._initial_state = state
        self.battery_start_pct = battery_start_pct
        self.battery_drain_pct_per_hour = battery_drain_pct_per_hour
        self.initialize()

    def initialize(self) -> None:
        self._running = False
        self._last_tick: Optional[float] = None
        self._elapsed_total = 0.0

        self.state = self._initial_state if self._initial_state is not None else WoundState()
        self.battery = BatteryProcess(
            start_pct=self.battery_start_pct,
            base_drain_pct_per_hour=self.battery_drain_pct_per_hour,
        )
        self.link_quality = LinkQualityProcess()

        self._fouling: Dict[str, ElectrodeFoulingProcess] = {
            cid: ElectrodeFoulingProcess() for cid in self.channels
        }
        self._channel_noise: Dict[str, float] = {cid: 0.0 for cid in self.channels}
        self._dropout: Dict[str, ProbabilisticDropout] = {
            cid: ProbabilisticDropout() for cid in self.channels
        }
        self._sustained_disconnect: Dict[str, SustainedDisconnect] = {
            cid: SustainedDisconnect() for cid in self.channels
        }
        self._connected: Dict[str, bool] = {cid: True for cid in self.channels}
        self._last_error: Dict[str, Optional[str]] = {cid: None for cid in self.channels}
        self._last_quality: Dict[str, float] = {cid: 0.98 for cid in self.channels}

        self._temp_noise = 0.0

    def start_measurement(self) -> None:
        self._running = True
        self._last_tick = time.monotonic()

    def stop_measurement(self) -> None:
        self._running = False

    def _advance(self, dt_seconds: Optional[float]) -> float:
        """Advance every running process by `dt_seconds` of wall-clock time
        (or however much really elapsed since the last advance, if not
        given explicitly -- tests pass it explicitly for determinism). A
        no-op past the first call within the same instant, so reading
        several channels "at once" advances the shared state once, not
        once per channel.
        """
        if dt_seconds is None:
            now = time.monotonic()
            dt_seconds = (now - self._last_tick) if self._last_tick is not None else 0.0
            self._last_tick = now

        if dt_seconds > 0:
            self._elapsed_total += dt_seconds
            self.state.step(dt_seconds)
            quality = self.link_quality.step(dt_seconds)
            self.battery.step(dt_seconds, link_quality=quality)
            for fouling in self._fouling.values():
                fouling.step(dt_seconds, moisture=self.state.moisture, bacterial_load=self.state.bacterial_load)
            self._temp_noise = ou_step(self._temp_noise, 0.2)

        return dt_seconds

    def read_channel(self, channel_id: str, dt_seconds: Optional[float] = None) -> RawMeasurement:
        if not self._running:
            raise RuntimeError("call start_measurement() before read_channel()")
        if channel_id not in self.channels:
            raise KeyError(f"unknown channel_id {channel_id!r} for device {self.device_id!r}")

        self._advance(dt_seconds)

        link_quality = self.link_quality.value
        try:
            self._sustained_disconnect[channel_id].check(link_quality)
            self._dropout[channel_id].check(link_quality)
        except SensorDisconnectedError as exc:
            self._connected[channel_id] = False
            self._last_error[channel_id] = str(exc)
            raise
        except CommsTimeoutError as exc:
            self._last_error[channel_id] = str(exc)
            raise

        self._connected[channel_id] = True
        self._last_error[channel_id] = None

        profile = get_channel_profile(self.channels[channel_id])
        fouling_level = self._fouling[channel_id].level

        # NoiseAmplifier (simulator/faults/faults.py) is the same "widen the
        # noise" fault simulator/scenarios.py's high_noise scenario uses --
        # here the multiplier comes from a running fouling level instead of
        # a fixed 8x.
        effective_noise_std = NoiseAmplifier(multiplier=1.0 + 3.0 * fouling_level).apply(profile.noise_std)
        self._channel_noise[channel_id] = ou_step(self._channel_noise[channel_id], effective_noise_std)

        raw_signal = self._observe_signal(profile, fouling_level, self._channel_noise[channel_id])

        jitter = random.gauss(0.0, 0.004)
        self._last_quality[channel_id] = max(0.05, min(0.98, 0.98 - 0.9 * fouling_level + jitter))

        return RawMeasurement(
            device_id=self.device_id,
            channel_id=channel_id,
            timestamp=datetime.now(timezone.utc),
            raw_signal=round(raw_signal, 4),
            temperature=round(
                temperature_generator(
                    self._elapsed_total,
                    base=36.5 + 1.5 * self.state.inflammation,
                    noise=self._temp_noise,
                ),
                2,
            ),
            battery=round(self.battery.pct),
        )

    def _observe_signal(self, profile: ChannelProfile, fouling_level: float, noise: float) -> float:
        """The state -> raw_signal transfer function for one channel: a
        linear response to WoundState, attenuated toward baseline as the
        electrode fouls (a biofilm-coated electrode reads a damped version
        of the true signal), plus this read's noise."""
        s = self.state
        true_response = (
            profile.baseline
            + profile.inflammation_gain * s.inflammation
            + profile.bacterial_load_gain * s.bacterial_load
            + profile.moisture_gain * (s.moisture - 0.5)
            + profile.perfusion_gain * (s.perfusion - 0.7)
        )
        attenuation = 1.0 - 0.5 * fouling_level
        signal = profile.baseline + (true_response - profile.baseline) * attenuation
        return signal + noise

    def read_all(self, dt_seconds: Optional[float] = None) -> Dict[str, RawMeasurement | Exception]:
        """One read per channel, advancing shared state once for the whole
        cycle. A channel whose link check raises (dropout, sustained
        disconnect) contributes its exception instead of a reading, so one
        failing channel never blocks the others -- same contract as
        simulator/sensors/multi_channel_sensor.py:MultiChannelSensor.read_all().
        """
        self._advance(dt_seconds)
        results: Dict[str, RawMeasurement | Exception] = {}
        for channel_id in self.channels:
            try:
                results[channel_id] = self.read_channel(channel_id, dt_seconds=0.0)
            except Exception as exc:  # noqa: BLE001 - deliberately surfaced, not swallowed
                results[channel_id] = exc
        return results

    def get_status(self, channel_id: Optional[str] = None) -> DeviceStatus | Dict[str, DeviceStatus]:
        if channel_id is not None:
            return DeviceStatus(
                connected=self._connected[channel_id],
                battery=round(self.battery.pct),
                signal_quality=round(self._last_quality[channel_id], 4),
                last_error=self._last_error[channel_id],
            )
        return {cid: self.get_status(cid) for cid in self.channels}
