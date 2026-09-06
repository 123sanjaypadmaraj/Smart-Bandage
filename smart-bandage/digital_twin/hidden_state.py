"""
DT-1 deliverable: a per-device hidden state model.

Every scenario built so far (simulator/scenarios/scenarios.py) is a script:
a fixed checkpoint array (`ramp_checkpoints`, `spike_checkpoints`) that
simulator/sensors/scenario_sensor.py eases its displayed signal toward,
indexed by read count. It can replay a shape but it can't *evolve* --
there's nothing underneath the checkpoints that responds to time the way a
real wound does, and two runs at different read rates play back identical
checkpoint sequences instead of covering different amounts of physiological
time.

`DeviceState` is that underneath: a small hidden state vector --

    bacterial_load          -- 0..1, fraction of local carrying capacity
    inflammation             -- 0..1
    healing_stage_progress   -- 0..1
    moisture                  -- 0..1

-- integrated forward one tick at a time with a first-order (forward) Euler
step, `x[t+dt] = x[t] + dt * dx/dt(x[t])`, instead of being read off a
pre-authored array. The state lives on the `DigitalTwinState` instance and
persists across ticks; nothing resets it except constructing a new one, and
each tick only ever depends on the state the previous tick produced plus
how much time passed -- not on a read index.

DT-1 proves this on exactly one channel end-to-end (hidden state ->
observable biomarker): `bacterial_load` grows by a logistic infection model
and `infection_biomarker()` turns that hidden load into the observable
biomarker value. `inflammation` / `healing_stage_progress` / `moisture` are
carried in the vector already -- so its shape doesn't have to change under
later tickets -- but get zero dynamics for now (`_zero_derivative`) and
simply hold their initial value. Wiring them to real dynamics, and wiring
any of this into ScenarioSensor/ChannelPipeline, is later DT tickets.

Naming note: this module originally lived at `digital_twin/state.py`, the
same path DT-2 independently used for its own (unrelated) `WoundState`
model. The two collided; DT-2's `WoundState` is the one wired into
digital_twin/observation.py, digital_twin/device_physics.py and the
backend twin integration, so it kept the `state.py` path and this
deliverable moved here instead. Nothing outside this module and
tests/test_digital_twin_dt1.py references the names below, so the move is
a pure rename -- no behavior changed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class DeviceState:
    """The hidden state vector for one device.

    Frozen -- `DigitalTwinState.tick()` replaces its held instance each
    tick rather than mutating fields in place, so a snapshot handed to a
    caller (e.g. for logging or a test assertion) can never change out from
    under them later.
    """

    bacterial_load: float = 0.05
    inflammation: float = 0.0
    healing_stage_progress: float = 0.0
    moisture: float = 0.5


@dataclass(frozen=True)
class BacterialLoadParams:
    """Logistic growth: `dB/dt = growth_rate * B * (1 - B / carrying_capacity)`
    -- the standard bounded-population model for unchecked bacterial growth
    at a wound site. Slow to start, fastest at `B == carrying_capacity / 2`,
    saturating at `carrying_capacity` instead of diverging, the way a real
    site with finite nutrients/space would."""

    growth_rate: float = 0.01
    carrying_capacity: float = 1.0


def _bacterial_load_derivative(state: DeviceState, params: BacterialLoadParams) -> float:
    b = state.bacterial_load
    return params.growth_rate * b * (1.0 - b / params.carrying_capacity)


def _zero_derivative(state: DeviceState) -> float:
    """Placeholder dynamics for the channels DT-1 doesn't wire yet. Held at
    zero (not omitted from the vector) so the state's shape is final now
    without inventing inflammation/healing/moisture physiology ahead of
    their own ticket -- see module docstring."""
    return 0.0


def infection_biomarker(bacterial_load: float, midpoint: float = 0.3, steepness: float = 8.0) -> float:
    """Hidden `bacterial_load` (0..1, fraction of carrying capacity) -> the
    observable infection biomarker, on a 0..100 a.u. scale matching the
    rest of the simulator's channels (simulator/scenarios/scenarios.py's
    `baseline=100.0` convention).

    A logistic response curve, not a linear one: real infection assays
    saturate at high bacterial load and sit near a floor at low load rather
    than reading out proportionally, so this is deliberately a sigmoid of
    `bacterial_load` around `midpoint` (how much load it takes to read as
    "infected"), not `bacterial_load * 100`.
    """
    x = steepness * (bacterial_load - midpoint)
    return 100.0 / (1.0 + math.exp(-x))


class DigitalTwinState:
    """Owns one device's `DeviceState` and Euler-integrates it forward one
    tick at a time. Nothing here reads from a checkpoint array or a read
    index -- see module docstring.
    """

    def __init__(
        self,
        initial: Optional[DeviceState] = None,
        bacterial_load_params: Optional[BacterialLoadParams] = None,
    ) -> None:
        self.state = initial if initial is not None else DeviceState()
        self._bacterial_load_params = bacterial_load_params or BacterialLoadParams()

    def tick(self, dt_seconds: float) -> DeviceState:
        """Advance the state by `dt_seconds` with one explicit (forward)
        Euler step per field: `x_next = x + dt * dx/dt(x)`.

        `dt_seconds` should stay small relative to the fastest dynamics in
        play -- the default `growth_rate` keeps that true for read
        intervals up to several seconds. Euler integration is only
        first-order accurate: a caller taking too large a step against
        faster dynamics would see the estimate diverge or oscillate instead
        of tracking the true curve.
        """
        if dt_seconds < 0:
            raise ValueError(f"dt_seconds must be >= 0, got {dt_seconds}")

        s = self.state
        params = self._bacterial_load_params
        bacterial_load = s.bacterial_load + dt_seconds * _bacterial_load_derivative(s, params)
        # Defensive clamp: within-range logistic growth never crosses these
        # bounds in continuous time (the derivative vanishes at both), but a
        # large enough dt_seconds can make a first-order Euler step
        # overshoot past them.
        bacterial_load = min(max(bacterial_load, 0.0), params.carrying_capacity)

        self.state = replace(
            s,
            bacterial_load=bacterial_load,
            inflammation=s.inflammation + dt_seconds * _zero_derivative(s),
            healing_stage_progress=s.healing_stage_progress + dt_seconds * _zero_derivative(s),
            moisture=s.moisture + dt_seconds * _zero_derivative(s),
        )
        return self.state
