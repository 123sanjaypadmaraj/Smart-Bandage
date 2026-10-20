"""
Analyte transport through the bandage's layer stack.

An analyte in the wound bed doesn't reach the electrode instantly: it has to
diffuse through the contact layer, the absorbent pad and the electrode's
membrane. That delay (and how it worsens as an electrode fouls or the pad
dries out) is the first thing a bandage design changes, and it's invisible to
a model that maps wound state straight to a signal.

`DiffusionStack` solves 1-D diffusion with a finite-volume grid and
*backward Euler* time stepping (a tridiagonal solve per step). Backward Euler
is unconditionally stable, so a 1 s step and a 10 min step both give a
bounded, monotone answer -- an explicit scheme would blow up once
D*dt/dx^2 > 1/2, which for a 10 um cell and 100 um^2/s is a 0.5 s limit.

  - left boundary: the wound face, held at the wound-bed concentration
  - right boundary: the electrode, no flux (the sensing film consumes far
    too little analyte to deplete the local concentration -- see the note in
    `transducer.py`)
  - interfaces between layers use series conductance, so a slow layer is
    correctly the bottleneck
  - hydrating layers move from their dry to wet diffusivity as they take up
    exudate; the electrode membrane's diffusivity is cut by fouling
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from digital_twin.bandage.design import LayerSpec
from digital_twin.bandage.kernel import relax

_TARGET_DX_UM = 10.0
_MIN_CELLS_PER_LAYER = 3
# biofilm on the membrane slows transport up to this much at full fouling
FOULING_DIFFUSION_PENALTY = 0.6


class LayerHydration:
    """How wet each shared layer is (0..1), relaxing toward the wound's
    moisture. Shared by every electrode -- the pad is one physical object."""

    def __init__(self, layers: Sequence[LayerSpec], initial: float = 0.0) -> None:
        self._layers = list(layers)
        self.level: List[float] = [initial] * len(self._layers)

    def step(self, dt_s: float, wound_moisture: float) -> None:
        for i, layer in enumerate(self._layers):
            if layer.hydration_tau_s is not None:
                self.level[i] = relax(self.level[i], wound_moisture, layer.hydration_tau_s, dt_s)

    def get_state(self) -> List[float]:
        return list(self.level)

    def set_state(self, s: List[float]) -> None:
        self.level = list(s)


def _thomas(lower: List[float], diag: List[float], upper: List[float], rhs: List[float]) -> List[float]:
    """Solve a tridiagonal system in O(n). lower[0] and upper[-1] unused."""
    n = len(diag)
    c = [0.0] * n
    d = [0.0] * n
    c[0] = upper[0] / diag[0]
    d[0] = rhs[0] / diag[0]
    for i in range(1, n):
        denom = diag[i] - lower[i] * c[i - 1]
        c[i] = upper[i] / denom if i < n - 1 else 0.0
        d[i] = (rhs[i] - lower[i] * d[i - 1]) / denom
    x = [0.0] * n
    x[-1] = d[-1]
    for i in range(n - 2, -1, -1):
        x[i] = d[i] - c[i] * x[i + 1]
    return x


class DiffusionStack:
    """Concentration profile through the shared layers plus one electrode's
    own membrane. Index 0 is next to the wound, the last cell is the
    electrode surface."""

    def __init__(
        self,
        layers: Sequence[LayerSpec],
        membrane: Optional[LayerSpec],
        diffusivity_scale: float,
        initial_conc: float,
    ) -> None:
        self._scale = diffusivity_scale
        self._shared = list(layers)
        self._membrane = membrane
        # per-cell geometry and which layer owns the cell (-1 = the membrane)
        self._dx: List[float] = []
        self._owner: List[int] = []
        for idx, layer in enumerate(self._shared):
            self._add_cells(layer, idx)
        if membrane is not None:
            self._add_cells(membrane, -1)
        self.conc: List[float] = [initial_conc] * len(self._dx)

    def _add_cells(self, layer: LayerSpec, owner: int) -> None:
        n = max(_MIN_CELLS_PER_LAYER, round(layer.thickness_um / _TARGET_DX_UM))
        self._dx.extend([layer.thickness_um / n] * n)
        self._owner.extend([owner] * n)

    def _diffusivity(self, cell: int, hydration: Sequence[float], fouling: float) -> float:
        owner = self._owner[cell]
        if owner == -1:
            m = self._membrane
            d = m.diffusivity_wet_um2_s * (1.0 - FOULING_DIFFUSION_PENALTY * fouling)
        else:
            layer = self._shared[owner]
            h = hydration[owner] if layer.hydration_tau_s is not None else 1.0
            d = layer.diffusivity_dry_um2_s + (layer.diffusivity_wet_um2_s - layer.diffusivity_dry_um2_s) * h
        return d * self._scale

    @property
    def surface(self) -> float:
        """Concentration at the electrode."""
        return self.conc[-1]

    @property
    def total_thickness_um(self) -> float:
        return sum(self._dx)

    def step(self, dt_s: float, wound_conc: float, hydration: Sequence[float], fouling: float) -> float:
        if dt_s <= 0:
            return self.surface
        n = len(self._dx)
        d = [self._diffusivity(i, hydration, fouling) for i in range(n)]
        # conductance across each interface i|i+1 (series of two half cells)
        g = [1.0 / ((self._dx[i] / 2.0) / d[i] + (self._dx[i + 1] / 2.0) / d[i + 1]) for i in range(n - 1)]
        g_wound = d[0] / (self._dx[0] / 2.0)

        lower = [0.0] * n
        diag = [0.0] * n
        upper = [0.0] * n
        rhs = [0.0] * n
        for i in range(n):
            storage = self._dx[i] / dt_s
            gl = g[i - 1] if i > 0 else 0.0
            gr = g[i] if i < n - 1 else 0.0
            lower[i] = -gl
            upper[i] = -gr
            diag[i] = storage + gl + gr + (g_wound if i == 0 else 0.0)
            rhs[i] = storage * self.conc[i] + (g_wound * wound_conc if i == 0 else 0.0)
        self.conc = _thomas(lower, diag, upper, rhs)
        return self.surface

    def get_state(self) -> List[float]:
        return list(self.conc)

    def set_state(self, s: List[float]) -> None:
        if len(s) != len(self._dx):
            raise ValueError(f"snapshot has {len(s)} cells, this stack has {len(self._dx)}")
        self.conc = list(s)
