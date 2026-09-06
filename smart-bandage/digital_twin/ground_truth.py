"""
DT-5 "Ground truth & validation harness" (Smart Bandage Digital Twin
artifact, build order DT-5): the accessor that reads out the hidden
WoundState a DigitalTwinDevice actually integrated, alongside the clean
(no noise, no electrode-fouling attenuation) signal each channel's
transfer function would produce for it.

This is what turns "the pipeline works" from something a person eyeballs
on a chart into a number: how far `estimated_value` drifts from the true
signal, tracked over a run instead of guessed at. See
ml/evaluation/twin_backtest.py for the harness that does the tracking, and
the artifact's "Why this is worth building" callout for the motivation.

Deliberately NOT a method on DigitalTwinSensor/DigitalTwinDevice and NOT
reachable through SensorInterface -- a real electrode has no such
accessor, and this invariant (artifact: "What doesn't change") must hold
for however many more digital-twin tickets land after this one. Only two
callers are meant to exist: backend/app/simulation.py's dev-only "ground
truth overlay" (gated behind `not settings.is_production`) and
ml/evaluation/twin_backtest.py's offline scoring harness below. Both
already reach into `DigitalTwinDevice.state` and duplicate this exact
transfer function inline (see backend/app/simulation.py:
_true_channel_signal) rather than share it -- that predates this module;
consolidating the backend copy onto this one is a follow-up for whoever
next touches backend/app/simulation.py, not done here to keep this ticket
to new files only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from digital_twin.observation import ChannelProfile, DigitalTwinDevice, get_channel_profile
from digital_twin.state import WoundState


def true_channel_signal(state: WoundState, profile: ChannelProfile) -> float:
    """The clean value `profile` would report for hidden state `state` --
    no noise, no electrode-fouling attenuation. Mirrors
    digital_twin/observation.py:DigitalTwinDevice._observe_signal's linear
    response term exactly, minus the two things that function adds on top
    for a real (noisy, fouled) reading."""
    return (
        profile.baseline
        + profile.inflammation_gain * state.inflammation
        + profile.bacterial_load_gain * state.bacterial_load
        + profile.moisture_gain * (state.moisture - 0.5)
        + profile.perfusion_gain * (state.perfusion - 0.7)
    )


@dataclass(frozen=True)
class GroundTruth:
    """One channel's ground truth at the instant it was read: the shared
    WoundState a DigitalTwinDevice actually integrated, plus the clean
    signal `channel_id`'s profile maps it to. `estimated_signal` is the
    caller's own pipeline output for the same tick -- this module never
    runs a pipeline itself, so it's accepted (optionally) rather than
    computed, letting a caller attach it without a second accessor."""

    device_id: str
    channel_id: str
    timestamp: datetime
    inflammation: float
    bacterial_load: float
    moisture: float
    perfusion: float
    true_signal: float
    estimated_signal: Optional[float] = None


def get_ground_truth(
    device: DigitalTwinDevice,
    channel_id: str,
    *,
    estimated_signal: Optional[float] = None,
) -> GroundTruth:
    """Read `device`'s current WoundState and `channel_id`'s clean signal
    at this instant. Call right after a `read_channel()`/`read_all()` for
    that channel so the snapshot lines up with the reading it explains --
    this does not itself advance the twin's state."""
    if channel_id not in device.channels:
        raise KeyError(f"unknown channel_id {channel_id!r} for device {device.device_id!r}")
    state = device.state
    profile = get_channel_profile(device.channels[channel_id])
    return GroundTruth(
        device_id=device.device_id,
        channel_id=channel_id,
        timestamp=datetime.now(timezone.utc),
        inflammation=state.inflammation,
        bacterial_load=state.bacterial_load,
        moisture=state.moisture,
        perfusion=state.perfusion,
        true_signal=round(true_channel_signal(state, profile), 4),
        estimated_signal=estimated_signal,
    )
