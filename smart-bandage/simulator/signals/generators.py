"""
Phase 2 signal generators (Blueprint §10 roadmap, §11 scenarios).

Stateless math only — no sensor/session state, no schema imports. Each
function takes the current position in the run (elapsed seconds and/or a
read index) plus a handful of physical parameters and returns one float.
`simulator/sensors/scenario_sensor.py` is what turns these into
RawMeasurement objects (and owns the *stateful* pieces -- the running noise
process, the checkpoint-follow smoothing -- feeding their results back in
here as plain numbers); `simulator/faults/` is what perturbs them.

Kept dependency-free (stdlib `random`/`math` only) so the simulator never
needs anything beyond what Phase 1 already installs.
"""
from __future__ import annotations

import math
import random


def ou_step(prev: float, target_std: float, phi: float = 0.35) -> float:
    """
    One tick of a mean-reverting noise process (a discretized
    Ornstein-Uhlenbeck / AR(1) process): `next = phi * prev + innovation`.

    Real electrode/ADC noise is autocorrelated -- each sample wanders from
    the last rather than landing independently -- which is what actually
    produces the smooth, organic jitter on a real biosignal trace instead
    of the "static" look of independent-per-sample noise. `target_std` is
    the *stationary* standard deviation this process settles into, chosen
    so it stays a drop-in replacement for a plain `noise_std`; `phi`
    controls the memory (closer to 1 = smoother, slower-drifting noise).
    """
    if target_std <= 0:
        return 0.0
    innovation_std = target_std * math.sqrt(max(1.0 - phi * phi, 1e-9))
    return phi * prev + random.gauss(0.0, innovation_std)


def physiological_ripple(
    elapsed_seconds: float,
    amplitude: float = 0.6,
    period_seconds: float = 1.7,
) -> float:
    """
    Small quasi-periodic ripple layered on top of a signal -- a stand-in for
    the pulse/breathing-linked micro-variation a real electrode against skin
    actually picks up, so the trace isn't perfectly flat between noise
    events. Purely cosmetic texture: kept small enough to never threaten a
    scenario's checkpoint thresholds or trip alerting on its own.
    """
    return amplitude * math.sin(2 * math.pi * elapsed_seconds / period_seconds)


def baseline_drift_noise(
    elapsed_seconds: float,
    baseline: float = 100.0,
    drift_per_second: float = 0.02,
    noise: float = 0.0,
) -> float:
    """signal(t) = baseline + linear drift + noise. `noise` is a value the
    caller already generated (see `ou_step`) rather than a std to sample
    from here, so consecutive reads can be correlated."""
    drift = drift_per_second * elapsed_seconds
    return baseline + drift + noise


def ramp(
    read_index: int,
    checkpoints: list[float],
    noise: float = 0.0,
) -> float:
    """
    Interpolate through a list of named checkpoint values (e.g. the
    "100 -> 110 -> 125 -> 140 -> 155 -> 170" rising-concentration sequence
    from Blueprint §7), continuing at the final slope once past the last
    checkpoint so an arbitrarily long run still trends the same direction.

    This returns the *target* the checkpoint sequence points to for
    `read_index`; ScenarioSensor eases its displayed signal toward that
    target over several reads rather than snapping straight to it, so a
    scenario transition looks like a trend/spike instead of a stair-step.
    """
    if not checkpoints:
        raise ValueError("ramp() requires at least one checkpoint")
    if len(checkpoints) == 1:
        base = checkpoints[0]
    elif read_index >= len(checkpoints) - 1:
        # past the last checkpoint: keep extrapolating at the final slope
        last_slope = checkpoints[-1] - checkpoints[-2]
        overshoot = read_index - (len(checkpoints) - 1)
        base = checkpoints[-1] + last_slope * overshoot
    else:
        lo = math.floor(read_index)
        frac = read_index - lo
        base = checkpoints[lo] + (checkpoints[lo + 1] - checkpoints[lo]) * frac
    return base + noise


def step_then_spike(
    read_index: int,
    stable_value: float,
    spike_checkpoints: list[float],
    spike_at_index: int,
    noise: float = 0.0,
) -> float:
    """
    Stable value until `spike_at_index`, then jumps through
    `spike_checkpoints` (Blueprint scenario 3: "105 -> 108 -> 110 ->
    160 -> 185 -> 210"). Same target/smoothing split as `ramp()`.
    """
    if read_index < spike_at_index:
        return stable_value + noise
    return ramp(read_index - spike_at_index, spike_checkpoints, noise=noise)


def declining_quality(
    read_index: int,
    checkpoints: list[float] = (0.98, 0.97, 0.94, 0.88, 0.76, 0.62),
    floor: float = 0.05,
    jitter: float = 0.0,
) -> float:
    """
    Electrode-degradation quality curve (Blueprint scenario 4:
    "98% -> 97% -> 94% -> 88% -> 76% -> 62%"), extrapolated downward past
    the last checkpoint at the final slope, clamped at `floor`. `jitter` is
    a small caller-supplied wobble so the curve doesn't look like a
    perfectly noiseless staircase.
    """
    checkpoints = list(checkpoints)
    if read_index >= len(checkpoints) - 1:
        last_slope = checkpoints[-1] - checkpoints[-2]
        overshoot = read_index - (len(checkpoints) - 1)
        value = checkpoints[-1] + last_slope * overshoot
    else:
        lo = math.floor(read_index)
        frac = read_index - lo
        value = checkpoints[lo] + (checkpoints[lo + 1] - checkpoints[lo]) * frac
    return max(floor, min(1.0, value + jitter))


def temperature(
    elapsed_seconds: float,
    base: float = 36.5,
    drift_per_second: float = 0.0,
    noise: float = 0.0,
) -> float:
    """On-board thermistor reading — body-adjacent, near-constant with slow
    drift. `noise` is caller-supplied (see `ou_step`) so it wanders smoothly
    instead of jittering independently every read."""
    return base + drift_per_second * elapsed_seconds + noise


def battery_drain(
    elapsed_seconds: float,
    start_pct: float = 100.0,
    drain_pct_per_hour: float = 2.0,
    wobble: float = 0.0,
) -> float:
    """Monotonic-trending battery drain plus a small caller-supplied wobble
    (voltage sag / ADC quantization noise a real fuel gauge would show),
    clamped to a valid percentage."""
    drained = drain_pct_per_hour * (elapsed_seconds / 3600.0)
    return max(0.0, min(start_pct, start_pct - drained + wobble))
