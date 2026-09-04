"""
The 7 simulator scenarios (Blueprint §7 "Simulator scenarios").

A ScenarioConfig is pure configuration — signal shape + optional fault —
consumed by simulator/sensors/scenario_sensor.py. Adding an 8th scenario
later means adding one entry here, not touching the sensor class.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from simulator.faults.faults import CommsDropoutCycle, DisconnectAfter, NoiseAmplifier

ScenarioName = Literal[
    "normal",
    "rising_concentration",
    "sudden_abnormal",
    "electrode_degradation",
    "high_noise",
    "sensor_disconnect",
    "comms_failure",
]


@dataclass(frozen=True)
class ScenarioConfig:
    name: str
    description: str
    baseline: float = 100.0
    drift_per_second: float = 0.02
    noise_std: float = 1.5
    # scenario 2: rising concentration checkpoints
    ramp_checkpoints: Optional[list[float]] = None
    # scenario 3: stable-then-spike
    spike_at_index: Optional[int] = None
    spike_checkpoints: Optional[list[float]] = None
    # scenario 4: declining signal_quality reported via get_status()
    degradation: bool = False
    # scenario 5
    noise_fault: Optional[NoiseAmplifier] = None
    # scenario 6
    disconnect: Optional[DisconnectAfter] = None
    # scenario 7
    comms_dropout: Optional[CommsDropoutCycle] = None


_SCENARIOS: dict[str, ScenarioConfig] = {
    "normal": ScenarioConfig(
        name="normal",
        description="Stable signal, low noise, healthy battery.",
        baseline=100.0,
        drift_per_second=0.02,
        noise_std=1.5,
    ),
    "rising_concentration": ScenarioConfig(
        name="rising_concentration",
        description="100 -> 110 -> 125 -> 140 -> 155 -> 170.",
        ramp_checkpoints=[100.0, 110.0, 125.0, 140.0, 155.0, 170.0],
        noise_std=1.5,
    ),
    "sudden_abnormal": ScenarioConfig(
        name="sudden_abnormal",
        description="105 -> 108 -> 110 -> 160 -> 185 -> 210.",
        baseline=108.0,
        noise_std=1.0,
        spike_at_index=3,
        spike_checkpoints=[160.0, 185.0, 210.0],
    ),
    "electrode_degradation": ScenarioConfig(
        name="electrode_degradation",
        description="Quality: 98% -> 97% -> 94% -> 88% -> 76% -> 62%.",
        baseline=100.0,
        noise_std=1.5,
        degradation=True,
    ),
    "high_noise": ScenarioConfig(
        name="high_noise",
        description="Signal-to-noise degrades sharply.",
        baseline=100.0,
        noise_std=1.5,
        noise_fault=NoiseAmplifier(multiplier=8.0),
    ),
    "sensor_disconnect": ScenarioConfig(
        name="sensor_disconnect",
        description="VALID x3 -> INVALID (disconnected) thereafter.",
        baseline=100.0,
        noise_std=1.5,
        disconnect=DisconnectAfter(trigger_at=3),
    ),
    "comms_failure": ScenarioConfig(
        name="comms_failure",
        description="packet x3 -> dropped x3 -> packet resumes (repeating).",
        baseline=100.0,
        noise_std=1.5,
        comms_dropout=CommsDropoutCycle(ok_count=3, drop_count=3),
    ),
}


def get_scenario(name: str) -> ScenarioConfig:
    try:
        return _SCENARIOS[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown scenario {name!r}; available: {', '.join(sorted(_SCENARIOS))}"
        ) from exc


def list_scenarios() -> list[str]:
    return sorted(_SCENARIOS)
