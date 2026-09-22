"""
Transducers: local analyte concentration at the electrode -> a native reading
(nA for amperometric sensors, mV for potentiometric).

Each one is a small stateful model of the sensing film:

  AptamerTransducer         binding kinetics + probe loss (baseline drift)
  EnzymaticTransducer       Michaelis-Menten + enzyme activity loss
  PotentiometricTransducer  Nernst slope, temperature-scaled, + reference drift

All state updates use closed-form solutions for the interval (exact for
constant input over `dt`), so a large step is as correct as a small one.

Not modeled: the sensing film consuming analyte (depleting the local
concentration). For a monolayer aptamer film on a ~1 mm^2 electrode that
uptake is many orders of magnitude below the analyte flux, so the transport
boundary condition at the electrode is a plain no-flux wall.
"""
from __future__ import annotations

import math
from typing import Union

from digital_twin.bandage.design import AptamerSpec, EnzymaticSpec, PotentiometricSpec, TransducerSpec

_BODY_TEMP_C = 37.0
# amperometric currents scale with temperature (diffusion + kinetics)
_CURRENT_TEMP_COEFF_PER_C = 0.02
_Q10 = 2.0


def _q10_factor(temp_c: float) -> float:
    return _Q10 ** ((temp_c - _BODY_TEMP_C) / 10.0)


def _per_day_to_rate_per_s(fraction_lost_per_day: float) -> float:
    return -math.log(1.0 - fraction_lost_per_day) / 86400.0


def _current_temp_factor(temp_c: float) -> float:
    return 1.0 + _CURRENT_TEMP_COEFF_PER_C * (temp_c - _BODY_TEMP_C)


class AptamerTransducer:
    def __init__(self, spec: AptamerSpec, area_mm2: float, initial_conc_nM: float) -> None:
        self.spec = spec
        self.area_mm2 = area_mm2
        self.occupancy = initial_conc_nM / (initial_conc_nM + spec.kd_nM)
        self.probe_fraction = 1.0

    def nominal(self) -> float:
        return self.area_mm2 * self.spec.j0_nA_per_mm2

    def step(self, dt_s: float, conc_nM: float, temp_c: float, t_s: float = 0.0) -> None:
        if dt_s <= 0:
            return
        s = self.spec
        k_on = s.k_on_per_nM_s * _q10_factor(temp_c)
        k_off = s.kd_nM * k_on
        rate = k_on * conc_nM + k_off
        theta_eq = conc_nM / (conc_nM + s.kd_nM)
        self.occupancy = theta_eq + (self.occupancy - theta_eq) * math.exp(-rate * dt_s)
        if s.desorption_per_day > 0:
            self.probe_fraction *= math.exp(
                -_per_day_to_rate_per_s(s.desorption_per_day) * _q10_factor(temp_c) * dt_s
            )

    def native(self, fouling: float, temp_c: float) -> float:
        s = self.spec
        return (
            self.nominal()
            * self.probe_fraction
            * (1.0 + s.signal_gain * self.occupancy)
            * (1.0 - s.fouling_signal_loss * fouling)
            * _current_temp_factor(temp_c)
        )

    @property
    def health(self) -> float:
        return self.probe_fraction

    def get_state(self) -> dict:
        return {"occupancy": self.occupancy, "probe_fraction": self.probe_fraction}

    def set_state(self, s: dict) -> None:
        self.occupancy = s["occupancy"]
        self.probe_fraction = s["probe_fraction"]


class EnzymaticTransducer:
    def __init__(self, spec: EnzymaticSpec, area_mm2: float, initial_conc_mM: float) -> None:
        self.spec = spec
        self.area_mm2 = area_mm2
        self.activity = 1.0
        self._conc = initial_conc_mM

    def nominal(self) -> float:
        s = self.spec
        return self.area_mm2 * s.j_max_nA_per_mm2 * s.reference_conc_mM / (s.km_mM + s.reference_conc_mM)

    def step(self, dt_s: float, conc_mM: float, temp_c: float, t_s: float = 0.0) -> None:
        self._conc = conc_mM  # instantaneous: enzymatic turnover is much faster than transport
        if dt_s > 0 and self.spec.activity_loss_per_day > 0:
            self.activity *= math.exp(
                -_per_day_to_rate_per_s(self.spec.activity_loss_per_day) * _q10_factor(temp_c) * dt_s
            )

    def native(self, fouling: float, temp_c: float) -> float:
        s = self.spec
        c = self._conc
        return (
            self.area_mm2 * s.j_max_nA_per_mm2 * self.activity * c / (s.km_mM + c)
            * (1.0 - s.fouling_signal_loss * fouling)
            * _current_temp_factor(temp_c)
        )

    @property
    def health(self) -> float:
        return self.activity

    def get_state(self) -> dict:
        return {"activity": self.activity, "conc": self._conc}

    def set_state(self, s: dict) -> None:
        self.activity = s["activity"]
        self._conc = s["conc"]


class PotentiometricTransducer:
    def __init__(self, spec: PotentiometricSpec, area_mm2: float, initial_ph: float) -> None:
        self.spec = spec
        self.area_mm2 = area_mm2
        self._ph = initial_ph
        self.drift_mV = 0.0

    def nominal(self) -> float:
        return self.spec.nominal_mV

    def step(self, dt_s: float, ph: float, temp_c: float, t_s: float = 0.0) -> None:
        self._ph = ph
        if dt_s > 0:
            self.drift_mV += self.spec.drift_mV_per_h * dt_s / 3600.0

    def native(self, fouling: float, temp_c: float) -> float:
        s = self.spec
        slope = s.slope_mV_per_ph * (temp_c + 273.15) / 298.15 * (1.0 - s.fouling_slope_loss * fouling)
        return s.nominal_mV + slope * (self._ph - s.reference_ph) + self.drift_mV

    @property
    def health(self) -> float:
        return max(0.0, 1.0 - abs(self.drift_mV) / self.spec.nominal_mV)

    def get_state(self) -> dict:
        return {"ph": self._ph, "drift_mV": self.drift_mV}

    def set_state(self, s: dict) -> None:
        self._ph = s["ph"]
        self.drift_mV = s["drift_mV"]


Transducer = Union[AptamerTransducer, EnzymaticTransducer, PotentiometricTransducer]


def make_transducer(spec: TransducerSpec, area_mm2: float, initial_local: float) -> Transducer:
    if isinstance(spec, AptamerSpec):
        return AptamerTransducer(spec, area_mm2, initial_local)
    if isinstance(spec, EnzymaticSpec):
        return EnzymaticTransducer(spec, area_mm2, initial_local)
    if isinstance(spec, PotentiometricSpec):
        return PotentiometricTransducer(spec, area_mm2, initial_local)
    raise TypeError(f"unknown transducer spec {type(spec).__name__}")
