"""
The electronics side of the device: analog front end + ADC, battery, BLE link
and electrode contact.

AnalogFrontEnd  normalizes a native reading to a.u. (nominal = 100), adds
                white + 1/f-like (OU) noise sized from the design's
                input-referred noise, applies this unit's fixed gain/offset
                error, and quantizes through the ADC. Noise grows when the
                dressing has lifted (poor contact).
PowerModel      coulomb counter on a duty-cycled load: sleep + measurement +
                BLE bursts, with radio retries costing more as the link
                degrades.
LinkProcess     BLE link quality in [0, 1], a seeded exact OU walk.
ContactProcess  electrode/skin contact in [0, 1]; a disturbance knocks it
                down (less so with a better adhesive) and it re-seals.

Every random draw comes from a named stream in `RngStreams`, so none of this
touches the global `random` module.
"""
from __future__ import annotations

import math
import random
from typing import Dict, Sequence

from digital_twin.bandage.design import AdhesiveSpec, ElectrodeSpec, ElectronicsSpec, PowerSpec
from digital_twin.bandage.kernel import RngStreams, ou_exact, relax

_CONTACT_NOISE_GAIN = 6.0
_CONTACT_SIGNAL_LOSS = 0.3  # amperometric channels lose up to this much signal at zero contact


class AnalogFrontEnd:
    def __init__(self, spec: ElectronicsSpec, electrodes: Sequence[ElectrodeSpec], streams: RngStreams) -> None:
        self.spec = spec
        self._streams = streams
        self._unit: Dict[str, tuple] = {}
        self._flicker: Dict[str, float] = {}
        for e in electrodes:
            # fixed at "manufacture": same seed -> same physical unit
            rng = streams.get("afe", "unit", e.channel_id)
            self._unit[e.channel_id] = (rng.gauss(1.0, spec.gain_error_std), rng.gauss(0.0, spec.offset_std_au))
            self._flicker[e.channel_id] = 0.0

    def step(self, dt_s: float) -> None:
        for cid in self._flicker:
            rng = self._streams.get("afe", "flicker", cid)
            self._flicker[cid] = ou_exact(self._flicker[cid], 0.0, self.spec.flicker_tau_s, 1.0, dt_s, rng)

    def _sigma_native(self, unit: str) -> tuple:
        s = self.spec
        return (s.noise_nA_rms, s.flicker_nA_rms) if unit == "nA" else (s.noise_mV_rms, s.flicker_mV_rms)

    def to_au(self, native: float, nominal: float, unit: str, contact: float) -> float:
        au = 100.0 * native / nominal
        if unit == "nA":  # potentiometric readings don't depend on contact area
            au *= 1.0 - _CONTACT_SIGNAL_LOSS * (1.0 - contact)
        return au

    def sample(self, cid: str, ideal_au: float, nominal: float, unit: str, contact: float) -> float:
        white_native, flicker_native = self._sigma_native(unit)
        scale = 100.0 / nominal * (1.0 + _CONTACT_NOISE_GAIN * (1.0 - contact))
        gain, offset = self._unit[cid]
        rng = self._streams.get("afe", "white", cid)
        noisy = ideal_au * gain + offset
        noisy += rng.gauss(0.0, white_native * scale) + self._flicker[cid] * flicker_native * scale
        return self.quantize(noisy)

    def quantize(self, value_au: float) -> float:
        s = self.spec
        lsb = s.full_scale_au / (2 ** s.adc_bits - 1)
        clipped = min(max(value_au, 0.0), s.full_scale_au)
        return round(clipped / lsb) * lsb

    def get_state(self) -> dict:
        return {
            "flicker": dict(self._flicker),
            "unit": {cid: list(v) for cid, v in self._unit.items()},
        }

    def set_state(self, s: dict) -> None:
        self._flicker = dict(s["flicker"])
        self._unit = {cid: tuple(v) for cid, v in s["unit"].items()}


class PowerModel:
    def __init__(self, spec: PowerSpec, sample_interval_s: float) -> None:
        self.spec = spec
        self.sample_interval_s = sample_interval_s
        self.charge_mAh = spec.capacity_mAh

    def average_current_mA(self, link_quality: float = 1.0) -> float:
        """Mean draw over one sample interval. The radio's on-time stretches
        with retries as the link degrades."""
        s = self.spec
        retry = 1.0 + s.ble_retry_gain * (1.0 - link_quality)
        measure = s.measure_mA * s.measure_s
        radio = s.ble_tx_mA * s.ble_tx_s * retry
        return s.sleep_uA / 1000.0 + (measure + radio) / self.sample_interval_s

    def battery_life_h(self, link_quality: float = 1.0) -> float:
        return self.spec.capacity_mAh / self.average_current_mA(link_quality)

    def step(self, dt_s: float, link_quality: float) -> None:
        if dt_s > 0:
            self.charge_mAh = max(0.0, self.charge_mAh - self.average_current_mA(link_quality) * dt_s / 3600.0)

    @property
    def percent(self) -> float:
        return 100.0 * self.charge_mAh / self.spec.capacity_mAh

    @property
    def depleted(self) -> bool:
        return self.charge_mAh <= 0.0

    def get_state(self) -> dict:
        return {"charge_mAh": self.charge_mAh}

    def set_state(self, s: dict) -> None:
        self.charge_mAh = s["charge_mAh"]


class LinkProcess:
    TAU_S = 3.3  # ~ 1 / 0.3 s^-1, same speed as the DT-2 process it replaces
    STD = 0.09

    def __init__(self, rng: random.Random, target: float = 0.92) -> None:
        self._rng = rng
        self.target = target
        self.value = 0.95

    def step(self, dt_s: float) -> None:
        self.value = min(1.0, max(0.0, ou_exact(self.value, self.target, self.TAU_S, self.STD, dt_s, self._rng)))

    def get_state(self) -> dict:
        return {"value": self.value, "target": self.target}

    def set_state(self, s: dict) -> None:
        self.value = s["value"]
        self.target = s["target"]


class ContactProcess:
    def __init__(self, spec: AdhesiveSpec) -> None:
        self.spec = spec
        self.value = 1.0

    def disturb(self, severity: float) -> None:
        """A knock or peeled edge; a stronger adhesive absorbs most of it."""
        dip = max(0.0, min(1.0, severity)) * (1.0 - self.spec.adhesion)
        self.value = min(self.value, 1.0 - dip)

    def step(self, dt_s: float) -> None:
        self.value = relax(self.value, 1.0, self.spec.contact_recovery_tau_s, dt_s)

    def get_state(self) -> dict:
        return {"value": self.value}

    def set_state(self, s: dict) -> None:
        self.value = s["value"]
