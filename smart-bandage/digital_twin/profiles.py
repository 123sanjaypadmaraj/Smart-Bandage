"""
DT-4 "Perturbation & profile library" -- the one shared clinical-profile
registry for the digital twin.

Consolidation note: this used to be two unwired registries that never
talked to each other -- this module's own three scripted-onset presets
(`normal_healing`, `complicated_infection`, `chronic_wound`), built against
a since-deleted standalone state model (digital_twin/profile_state.py), and
`backend/app/simulation.py`'s `_PATIENT_PROFILES`, three static risk-level
starting points (`healthy_baseline`, `diabetic_slow_healing`,
`immunocompromised_high_risk`) against DT-2's real `WoundState`, kept
deliberately separate and undocumented as a stand-in "until
digital_twin/profiles.py settles" (see that module's old docstring). Both
sets are real product value -- a risk-level starting point and a scripted
clinical event are different things -- so both are kept here, all six
built against the one real `WoundState`, consumed by both
`backend/app/simulation.py` and `digital_twin/engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Tuple

from common.schemas.measurement import RawMeasurement
from digital_twin.observation import DigitalTwinDevice
from digital_twin.perturbations import DressingDisturbance, InfectionOnset, Perturbation
from digital_twin.state import WoundState


@dataclass(frozen=True)
class TwinProfile:
    """A named starting point for a twin-backed simulation: an initial
    `WoundState` plus an ordered list of `Perturbation`s applied every tick
    on top of `WoundState.step()`'s own mean reversion.

    `sensor_type` is the digital_twin/observation.py:ChannelProfile this
    profile was written against -- the channel most sensitive to the
    variables this profile moves (see get_channel_profile) -- used as the
    default when a caller (digital_twin/engine.py) doesn't say otherwise.
    """

    name: str
    description: str
    sensor_type: str = "pathogen_channel_1"
    initial: WoundState = field(default_factory=WoundState)
    perturbations: Tuple[Perturbation, ...] = ()

    def initial_state(self) -> WoundState:
        """A fresh copy of this profile's starting WoundState --
        `dataclasses.replace` because WoundState is mutable (`.step()`
        mutates in place) and a registry entry must never be handed out
        shared, or two simulations "started" from the same profile would
        silently mutate each other's state."""
        return replace(self.initial)


# The three static risk levels backend/app/simulation.py (DT-6) originally
# hardcoded: each WoundState's `*_target` fields match its initial values,
# so it mean-reverts to its own baseline (see WoundState.step()) instead of
# drifting back toward WoundState's own defaults -- otherwise a "diabetic"
# run would look healthy again after a few minutes.
_PROFILES: dict[str, TwinProfile] = {
    "healthy_baseline": TwinProfile(
        name="healthy_baseline",
        description="WoundState defaults -- low inflammation/bacterial load, well-perfused.",
        initial=WoundState(),
    ),
    "diabetic_slow_healing": TwinProfile(
        name="diabetic_slow_healing",
        description="Elevated inflammation and bacterial load, reduced perfusion -- impaired local immune response.",
        initial=WoundState(
            inflammation=0.35,
            inflammation_target=0.35,
            bacterial_load=0.4,
            bacterial_load_target=0.4,
            moisture=0.6,
            moisture_target=0.6,
            perfusion=0.5,
            perfusion_target=0.5,
        ),
    ),
    "immunocompromised_high_risk": TwinProfile(
        name="immunocompromised_high_risk",
        description="High inflammation and bacterial load, the most reduced perfusion -- least headroom before a serious infection.",
        initial=WoundState(
            inflammation=0.6,
            inflammation_target=0.6,
            bacterial_load=0.75,
            bacterial_load_target=0.75,
            moisture=0.65,
            moisture_target=0.65,
            perfusion=0.4,
            perfusion_target=0.4,
        ),
    ),
    # The three scripted-onset scenarios DT-4 originally built: a clean
    # start plus a schedule of Perturbations, instead of a static baseline.
    "normal_healing": TwinProfile(
        name="normal_healing",
        description="Clean wound, no perturbations: low bacterial load and inflammation throughout.",
        initial=WoundState(
            bacterial_load=0.03,
            bacterial_load_target=0.03,
            inflammation=0.03,
            inflammation_target=0.03,
        ),
    ),
    "complicated_infection": TwinProfile(
        name="complicated_infection",
        description="Infection onset around day 2, ramping fast, plus a dressing disturbance a day and a half later.",
        initial=WoundState(bacterial_load=0.05, inflammation=0.05),
        perturbations=(
            InfectionOnset(onset_day=2.0, ramp_days=1.5, severity=0.85),
            DressingDisturbance(event_days=(3.5, 4.0), severity=0.4),
        ),
    ),
    "chronic_wound": TwinProfile(
        name="chronic_wound",
        description="Persistent low-grade infection from day 0 and an elevated moisture baseline -- slow to resolve, fouls its electrode faster than a clean wound.",
        initial=WoundState(
            bacterial_load=0.2,
            bacterial_load_target=0.2,
            inflammation=0.15,
            inflammation_target=0.15,
            moisture=0.6,
            moisture_target=0.6,
        ),
        perturbations=(InfectionOnset(onset_day=0.0, ramp_days=0.5, severity=0.3),),
    ),
}

DEFAULT_PROFILE = "healthy_baseline"


def get_profile(name: str) -> TwinProfile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        raise KeyError(f"unknown profile {name!r}; available: {', '.join(sorted(_PROFILES))}") from exc


def list_profiles() -> list[str]:
    return sorted(_PROFILES)


def simulate(
    profile: TwinProfile,
    duration_days: float,
    dt_days: float = 1.0 / 24,
    seed: int | None = None,
    device_id: str = "SIM-PROFILE",
    channel_id: str = "SIM-CH",
) -> Iterator[Tuple[WoundState, RawMeasurement]]:
    """Step `profile`'s twin forward from its initial_state to
    `duration_days`, applying its perturbations every tick, yielding one
    (WoundState snapshot, RawMeasurement) pair per tick -- the real DT-2
    state -> RawMeasurement pipeline (digital_twin/observation.py), not a
    standalone summary. The RawMeasurement's channel reads through
    `profile.sensor_type`'s ChannelProfile, so a profile's readings look
    like they would on the sensor it was designed for.

    `dt_days` defaults to an hourly tick (1/24 day) -- fine-grained enough
    that a perturbation's `window_days`/`ramp_days` (see
    digital_twin/perturbations.py) render as a curve rather than a jump,
    without the caller having to pick a tick size themselves. `seed` makes
    the run reproducible, the same as digital_twin/engine.py.
    """
    if dt_days <= 0:
        raise ValueError("dt_days must be > 0")
    if duration_days < 0:
        raise ValueError("duration_days must be >= 0")

    device = DigitalTwinDevice(
        device_id,
        channels={channel_id: profile.sensor_type},
        state=profile.initial_state(),
        seed=seed,
    )
    device.start_measurement()

    dt_seconds = dt_days * 86400.0
    n_ticks = max(1, round(duration_days / dt_days))
    elapsed_days = 0.0
    for _ in range(n_ticks):
        for perturbation in profile.perturbations:
            perturbation.apply(device.state, elapsed_days)
        reading = device.read_channel(channel_id, dt_seconds=dt_seconds)
        elapsed_days += dt_days
        yield replace(device.state), reading
