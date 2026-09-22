"""
One electrode: wound-face analyte -> (diffusion through the layers) ->
transducer -> normalized reading, with its own fouling.

Fouling is per electrode (each has its own membrane and coating), driven by
the shared wound bed:  dF/dt = k*(0.3 + moisture)*(0.5 + load)*(1 - antifouling)*(1 - F).
It saturates rather than running away, and it feeds back on the physics twice:
it slows transport through the membrane (transport.py) and attenuates the
transducer's response (transducer.py).
"""
from __future__ import annotations

import math
from typing import Sequence

from digital_twin.bandage.design import ANALYTE_DIFFUSIVITY_SCALE, ElectrodeSpec, LayerSpec
from digital_twin.bandage.transducer import Transducer, make_transducer
from digital_twin.bandage.transport import DiffusionStack
from digital_twin.bandage.wound_bed import Analytes

_FOULING_RATE_PER_H = 0.02


class Electrode:
    def __init__(self, spec: ElectrodeSpec, layers: Sequence[LayerSpec], wound0: Analytes) -> None:
        self.spec = spec
        initial = wound0.get(spec.analyte)
        self.stack = DiffusionStack(layers, spec.membrane, ANALYTE_DIFFUSIVITY_SCALE[spec.analyte], initial)
        self.transducer: Transducer = make_transducer(spec.transducer, spec.area_mm2, initial)
        self.fouling = 0.0
        self.failed = False

    @property
    def channel_id(self) -> str:
        return self.spec.channel_id

    def step(
        self,
        dt_s: float,
        wound: Analytes,
        bacterial_load: float,
        hydration: Sequence[float],
    ) -> None:
        if dt_s <= 0:
            return
        rate = _FOULING_RATE_PER_H / 3600.0 * (0.3 + wound.moisture) * (0.5 + bacterial_load)
        rate *= 1.0 - self.spec.antifouling
        self.fouling = 1.0 - (1.0 - self.fouling) * math.exp(-rate * dt_s)

        local = self.stack.step(dt_s, wound.get(self.spec.analyte), hydration, self.fouling)
        self.transducer.step(dt_s, local, wound.temperature_C)

    def native_reading(self, temp_c: float) -> float:
        return self.transducer.native(self.fouling, temp_c)

    @property
    def unit(self) -> str:
        return self.spec.transducer.unit

    @property
    def local_concentration(self) -> float:
        return self.stack.surface

    def get_state(self) -> dict:
        return {
            "stack": self.stack.get_state(),
            "transducer": self.transducer.get_state(),
            "fouling": self.fouling,
            "failed": self.failed,
        }

    def set_state(self, s: dict) -> None:
        self.stack.set_state(s["stack"])
        self.transducer.set_state(s["transducer"])
        self.fouling = s["fouling"]
        self.failed = s["failed"]
