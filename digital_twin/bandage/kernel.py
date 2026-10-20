"""
Engine kernel for the bandage digital twin.

The DT-1..DT-4 modules each grew their own way of advancing time and drawing
randomness, and three problems came out of that:

  - Randomness: `state.py`, `device_physics.py` and `observation.py` draw from
    the *global* `random` module, so a run can't be reproduced and tests that
    compare two runs (e.g. "poor link drains faster") are flaky.
  - Time: `DigitalTwinDevice` reads `time.monotonic()` / `datetime.now()`;
    `DigitalTwinEngine` is tick-counted but doesn't run a physical model at
    all (it replays scripted scenario checkpoints).
  - Accuracy: DT-1 integrates with forward Euler, so results depend on the
    caller's step size, and a big `dt` after a pause overshoots.

This module is the shared foundation that fixes those, in four small pieces:

  RngStreams   one named `random.Random` per component, derived from a master
               seed by hashing (not by draw order), so adding or removing a
               component never shifts another component's random numbers.
               Full state exports to JSON for snapshot/restore.
  ou_exact     the exact Ornstein-Uhlenbeck transition: correct for any `dt`.
  SimClock     simulated seconds since t=0; nothing in the twin reads a wall
               clock.
  EventQueue   timed interventions (infection, antibiotic, dressing peel...)
               applied at their exact simulated time -- the twin splits its
               sub-steps at event times so the result doesn't depend on how
               the caller chunks `advance()`.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def derive_seed(master: int, *path: str) -> int:
    """Stable 64-bit seed for the stream named by `path` under `master`.

    Hash-derived rather than drawn from a parent RNG: the seed for
    ("afe", "CH-01") is the same whether or not the "link" stream exists.
    """
    key = f"{master}::" + "/".join(path)
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def _pack_rng_state(state: tuple) -> list:
    version, internal, gauss_next = state
    return [version, list(internal), gauss_next]


def _unpack_rng_state(packed: list) -> tuple:
    version, internal, gauss_next = packed
    return (version, tuple(internal), gauss_next)


class RngStreams:
    """Named, independently seeded random streams under one master seed."""

    def __init__(self, seed: Optional[int] = None) -> None:
        # seed=None still yields a reproducible-after-the-fact run: the
        # chosen master seed is kept on `.seed` so it can be recorded.
        self.seed = seed if seed is not None else random.SystemRandom().getrandbits(63)
        self._streams: Dict[str, random.Random] = {}

    def get(self, *path: str) -> random.Random:
        key = "/".join(path)
        rng = self._streams.get(key)
        if rng is None:
            rng = random.Random(derive_seed(self.seed, *path))
            self._streams[key] = rng
        return rng

    def get_state(self) -> Dict[str, list]:
        return {key: _pack_rng_state(rng.getstate()) for key, rng in self._streams.items()}

    def set_state(self, state: Dict[str, list]) -> None:
        for key, packed in state.items():
            rng = self._streams.get(key)
            if rng is None:
                rng = self._streams[key] = random.Random()
            rng.setstate(_unpack_rng_state(packed))


def ou_exact(
    x: float, mean: float, tau_s: float, stationary_std: float, dt_s: float, rng: random.Random
) -> float:
    """One exact Ornstein-Uhlenbeck step: x relaxes toward `mean` with time
    constant `tau_s`, driven by noise whose long-run standard deviation is
    `stationary_std`.

    Exact for any `dt_s` (decay = exp(-dt/tau), innovation std =
    stationary_std * sqrt(1 - decay^2)), so a 1 s step and a 1 h step land on
    the same distribution -- an Euler step would overshoot once dt > tau.
    """
    if dt_s <= 0:
        return x
    decay = math.exp(-dt_s / tau_s)
    innovation = stationary_std * math.sqrt(max(0.0, 1.0 - decay * decay))
    return mean + (x - mean) * decay + rng.gauss(0.0, innovation)


def relax(x: float, target: float, tau_s: float, dt_s: float) -> float:
    """Deterministic first-order relaxation, exact for any dt."""
    if dt_s <= 0:
        return x
    return target + (x - target) * math.exp(-dt_s / tau_s)


class SimClock:
    """Simulated seconds since the start of the run. Never reads a wall clock."""

    def __init__(self, t_s: float = 0.0) -> None:
        self.t_s = t_s

    def advance(self, dt_s: float) -> float:
        if dt_s < 0:
            raise ValueError(f"cannot advance the clock backwards (dt={dt_s})")
        self.t_s += dt_s
        return self.t_s


@dataclass(frozen=True)
class Event:
    """A timed intervention. `kind` selects the handler; `params` are its
    arguments. Plain data on purpose, so a scheduled event survives snapshot
    to JSON and back."""

    at_s: float
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"at_s": self.at_s, "kind": self.kind, "params": dict(self.params)}

    @staticmethod
    def from_dict(d: dict) -> "Event":
        return Event(at_s=d["at_s"], kind=d["kind"], params=dict(d.get("params", {})))


class EventQueue:
    """Events ordered by time; ties keep scheduling order."""

    def __init__(self) -> None:
        self._events: List[Event] = []

    def schedule(self, event: Event) -> None:
        # stable insertion: after every event with at_s <= this one's
        idx = len(self._events)
        while idx > 0 and self._events[idx - 1].at_s > event.at_s:
            idx -= 1
        self._events.insert(idx, event)

    def next_time(self) -> Optional[float]:
        return self._events[0].at_s if self._events else None

    def pop_due(self, t_s: float) -> List[Event]:
        due = []
        while self._events and self._events[0].at_s <= t_s + 1e-9:
            due.append(self._events.pop(0))
        return due

    def __len__(self) -> int:
        return len(self._events)

    def get_state(self) -> List[dict]:
        return [e.to_dict() for e in self._events]

    def set_state(self, state: List[dict]) -> None:
        self._events = [Event.from_dict(d) for d in state]
