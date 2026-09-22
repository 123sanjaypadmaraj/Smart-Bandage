"""
WoundBed: the physiology the bandage is measuring, as one coupled system.

It replaces three earlier, unrelated wound-state models (digital_twin/state.py,
hidden_state.py, profile_state.py) with one, and fixes their integration:

  - bacterial load B and inflammation I are a coupled ODE pair integrated with
    classical RK4 instead of forward Euler, so accuracy doesn't hinge on `dt`.
      dB/dt = g*V*B*(1-B) - c*A*(k0 + I)*B      growth vs immune clearance
      dI/dt = (B + floor - I) / tau_I           inflammation trails infection
    V (virulence) and A (antibiotic) are timed multipliers set by events.
  - moisture (exudate) and perfusion follow B/I through exact
    Ornstein-Uhlenbeck relaxation with seeded process noise.

The outputs are *analyte concentrations at the wound face* -- what the
bandage's layers then have to transport to the electrodes -- not the abstract
0..1 numbers the old twins mapped straight to a raw signal. The concentration
laws (marker rises with load, glucose is consumed by bacteria, infected wounds
turn alkaline) follow the qualitative wound-biochemistry literature; the
coefficients are illustrative, not fitted.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from digital_twin.bandage.kernel import ou_exact

_H = 3600.0


@dataclass(frozen=True)
class WoundBedParams:
    growth_per_h: float = 0.06
    clearance_per_h: float = 0.15
    immune_baseline: float = 0.5
    inflammation_tau_h: float = 6.0
    inflammation_floor: float = 0.03
    moisture_tau_h: float = 2.0
    moisture_baseline: float = 0.35
    moisture_per_inflammation: float = 0.5
    perfusion_tau_h: float = 3.0
    perfusion_baseline: float = 0.75
    perfusion_loss_per_inflammation: float = 0.3
    process_noise: float = 0.01
    initial_bacterial_load: float = 0.03
    initial_inflammation: float = 0.03
    # analyte laws
    marker_background_nM: float = 2.0
    marker_max_nM: float = 600.0
    glucose_baseline_mM: float = 4.0
    glucose_consumed_at_full_load: float = 0.7
    ph_baseline: float = 7.2
    ph_shift_at_full_load: float = 0.9


@dataclass(frozen=True)
class Analytes:
    """Wound-face conditions at one instant."""

    pathogen_marker: float  # nM
    glucose: float  # mM
    pH: float
    temperature_C: float
    moisture: float

    def get(self, analyte: str) -> float:
        return getattr(self, analyte)


class WoundBed:
    def __init__(self, params: WoundBedParams, rng: random.Random) -> None:
        self.params = params
        self._rng = rng
        self.bacterial_load = params.initial_bacterial_load
        self.inflammation = params.initial_inflammation
        self.moisture = params.moisture_baseline
        self.perfusion = params.perfusion_baseline
        # timed multipliers: {"kind": "virulence"|"antibiotic", "until_s", "magnitude"}
        self.effects: List[dict] = []

    # -- interventions ------------------------------------------------------
    def apply_event(self, kind: str, params: Dict[str, float], t_s: float) -> bool:
        """Handle a wound-bed event; returns False if `kind` isn't one of ours."""
        if kind == "infection":
            severity = float(params.get("severity", 0.6))
            self.effects.append(
                {"kind": "virulence", "until_s": t_s + float(params.get("duration_h", 48.0)) * _H,
                 "magnitude": 6.0 * severity}
            )
            self.bacterial_load = min(1.0, self.bacterial_load + 0.02 * severity)
            return True
        if kind == "antibiotic":
            self.effects.append(
                {"kind": "antibiotic", "until_s": t_s + float(params.get("duration_h", 24.0)) * _H,
                 "magnitude": float(params.get("potency", 3.0))}
            )
            return True
        if kind == "debridement":
            self.bacterial_load *= float(params.get("residual", 0.2))
            self.inflammation *= 0.7
            return True
        return False

    def _multiplier(self, kind: str, t_s: float) -> float:
        return 1.0 + sum(e["magnitude"] for e in self.effects if e["kind"] == kind and t_s < e["until_s"])

    def next_boundary_s(self, t_s: float) -> Optional[float]:
        """Earliest future time an active effect expires (the twin splits its
        sub-steps there so a coefficient change lands on the exact instant)."""
        ends = [e["until_s"] for e in self.effects if e["until_s"] > t_s]
        return min(ends) if ends else None

    # -- dynamics -------------------------------------------------------------
    def step(self, t_s: float, dt_s: float) -> None:
        """Advance from t_s to t_s + dt_s. Coefficients (virulence,
        antibiotic) are held at their value at t_s; the twin guarantees no
        effect expires strictly inside the step."""
        if dt_s <= 0:
            return
        p = self.params
        g = p.growth_per_h / _H * self._multiplier("virulence", t_s)
        c = p.clearance_per_h / _H * self._multiplier("antibiotic", t_s)
        tau_i = p.inflammation_tau_h * _H

        def f(b: float, i: float) -> tuple:
            db = g * b * (1.0 - b) - c * (p.immune_baseline + i) * b
            di = (b + p.inflammation_floor - i) / tau_i
            return db, di

        b, i = self.bacterial_load, self.inflammation
        k1b, k1i = f(b, i)
        k2b, k2i = f(b + 0.5 * dt_s * k1b, i + 0.5 * dt_s * k1i)
        k3b, k3i = f(b + 0.5 * dt_s * k2b, i + 0.5 * dt_s * k2i)
        k4b, k4i = f(b + dt_s * k3b, i + dt_s * k3i)
        self.bacterial_load = min(1.0, max(0.0, b + dt_s / 6.0 * (k1b + 2 * k2b + 2 * k3b + k4b)))
        self.inflammation = min(1.5, max(0.0, i + dt_s / 6.0 * (k1i + 2 * k2i + 2 * k3i + k4i)))

        moisture_target = min(1.0, p.moisture_baseline + p.moisture_per_inflammation * self.inflammation)
        perfusion_target = max(0.0, p.perfusion_baseline - p.perfusion_loss_per_inflammation * self.inflammation)
        self.moisture = min(1.0, max(0.0, ou_exact(
            self.moisture, moisture_target, p.moisture_tau_h * _H, p.process_noise, dt_s, self._rng)))
        self.perfusion = min(1.0, max(0.0, ou_exact(
            self.perfusion, perfusion_target, p.perfusion_tau_h * _H, p.process_noise, dt_s, self._rng)))

    def analytes(self) -> Analytes:
        p = self.params
        b = self.bacterial_load
        return Analytes(
            pathogen_marker=p.marker_background_nM + p.marker_max_nM * b,
            glucose=p.glucose_baseline_mM * (1.0 - p.glucose_consumed_at_full_load * b),
            pH=p.ph_baseline + p.ph_shift_at_full_load * b,
            temperature_C=36.0 + 0.5 * self.perfusion + 1.5 * self.inflammation,
            moisture=self.moisture,
        )

    # -- snapshot ---------------------------------------------------------------
    def get_state(self) -> dict:
        return {
            "bacterial_load": self.bacterial_load,
            "inflammation": self.inflammation,
            "moisture": self.moisture,
            "perfusion": self.perfusion,
            "effects": [dict(e) for e in self.effects],
        }

    def set_state(self, s: dict) -> None:
        self.bacterial_load = s["bacterial_load"]
        self.inflammation = s["inflammation"]
        self.moisture = s["moisture"]
        self.perfusion = s["perfusion"]
        self.effects = [dict(e) for e in s["effects"]]
