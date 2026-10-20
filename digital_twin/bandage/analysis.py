"""
Design characterization: the numbers you'd want to compare two bandage
designs by, measured by running the twin itself on a virtual bench.

    sensitivity   a.u. per analyte unit, at the low end of the range
    noise / LOD   total input noise (AFE white + 1/f + ADC quantization) and
                  the concentration it corresponds to at 3 sigma
    t90           seconds for the reading to cover 90% of a concentration
                  step -- diffusion through the stack plus binding kinetics
    wear life     how long the sensing film lasts before it has lost enough to
                  matter (basis differs by transducer; stated in the report)
    battery life  duty-cycled load on the design's battery, at a healthy link

Sensitivity and t90 are *measured* on a fresh twin held in standard solutions
(`BandageTwin.hold_bench`), not computed from a formula, so they include the
layer stack, hydration, kinetics and temperature exactly as the full twin has
them. Noise, LOD, wear life and battery life come from the design's own
numbers analytically. All of it inherits the design's illustrative
parameters (see design.py) -- compare designs with it, don't quote it as a
measured specification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from digital_twin.bandage.design import (
    AptamerSpec,
    BandageDesign,
    ElectrodeSpec,
    EnzymaticSpec,
    PotentiometricSpec,
)
from digital_twin.bandage.twin import BandageTwin

# analyte -> (unit, low reference, step target) for the bench step test
_BENCH: Dict[str, Tuple[str, float, float]] = {
    "pathogen_marker": ("nM", 2.0, 52.0),
    "glucose": ("mM", 4.0, 3.0),
    "pH": ("pH", 7.2, 7.6),
}
_SETTLE_S = 1800.0
_T90_LIMIT_S = 7200.0
_T90_DT_S = 2.0
_BODY_TEMP_C = 37.0


@dataclass(frozen=True)
class ChannelReport:
    channel_id: str
    analyte: str
    unit: str
    sensitivity_au_per_unit: float
    noise_au_rms: float
    lod: float
    t90_s: Optional[float]  # None = did not reach 90% within the limit
    wear_life_h: float
    wear_life_basis: str


@dataclass(frozen=True)
class DesignReport:
    design_name: str
    digest: str
    sample_interval_s: float
    battery_life_h: float
    channels: Tuple[ChannelReport, ...]


def _bench_twin(design: BandageDesign, analyte: str, conc: float, seed: int) -> BandageTwin:
    twin = BandageTwin(design, seed=seed)
    twin.hold_bench(**{analyte: conc})
    twin.advance(_SETTLE_S)
    for e in twin.electrodes.values():
        e.fouling = 0.0  # characterize the fresh sensor, not what the bench soak did to it
    return twin


def _noise_au_rms(design: BandageDesign, e: ElectrodeSpec) -> float:
    s = design.electronics
    nominal = _nominal_native(e)
    if e.transducer.unit == "nA":
        white, flicker = s.noise_nA_rms, s.flicker_nA_rms
    else:
        white, flicker = s.noise_mV_rms, s.flicker_mV_rms
    lsb = s.full_scale_au / (2 ** s.adc_bits - 1)
    electrical = 100.0 / nominal * math.sqrt(white ** 2 + flicker ** 2)
    return math.sqrt(electrical ** 2 + lsb ** 2 / 12.0)


def _nominal_native(e: ElectrodeSpec) -> float:
    t = e.transducer
    if isinstance(t, AptamerSpec):
        return e.area_mm2 * t.j0_nA_per_mm2
    if isinstance(t, EnzymaticSpec):
        return e.area_mm2 * t.j_max_nA_per_mm2 * t.reference_conc_mM / (t.km_mM + t.reference_conc_mM)
    return t.nominal_mV


def _wear_life(e: ElectrodeSpec) -> Tuple[float, str]:
    t = e.transducer
    if isinstance(t, (AptamerSpec, EnzymaticSpec)):
        per_day = t.desorption_per_day if isinstance(t, AptamerSpec) else t.activity_loss_per_day
        what = "probes" if isinstance(t, AptamerSpec) else "enzyme activity"
        if per_day <= 0:
            return math.inf, f"{what}: no loss modeled"
        rate_per_h = -math.log(1.0 - per_day) / 24.0
        return math.log(1.0 / 0.7) / rate_per_h, f"until 70% of {what} remain (37 C)"
    if isinstance(t, PotentiometricSpec):
        if t.drift_mV_per_h <= 0:
            return math.inf, "no drift modeled"
        mv_per_ph = t.slope_mV_per_ph * (_BODY_TEMP_C + 273.15) / 298.15
        return 0.1 * mv_per_ph / t.drift_mV_per_h, "until reference drift = 0.1 pH error"
    raise TypeError(type(t).__name__)


def _measure_channel(design: BandageDesign, e: ElectrodeSpec, seed: int) -> ChannelReport:
    unit, low, high = _BENCH[e.analyte]
    cid = e.channel_id

    low_twin = _bench_twin(design, e.analyte, low, seed)
    au_low = low_twin.read_channel(cid, ideal=True).raw_signal
    high_twin = _bench_twin(design, e.analyte, high, seed)
    au_high = high_twin.read_channel(cid, ideal=True).raw_signal
    span = au_high - au_low
    sensitivity = span / (high - low)

    # t90: from the settled low-solution state, jump to the high solution
    twin = _bench_twin(design, e.analyte, low, seed)
    twin.hold_bench(**{e.analyte: high})
    target = au_low + 0.9 * span
    t90: Optional[float] = None
    elapsed = 0.0
    while elapsed < _T90_LIMIT_S:
        twin.advance(_T90_DT_S)
        elapsed += _T90_DT_S
        au = twin.read_channel(cid, ideal=True).raw_signal
        if (au - target) * span >= 0:
            t90 = elapsed
            break

    noise = _noise_au_rms(design, e)
    lod = 3.0 * noise / abs(sensitivity) if sensitivity else math.inf
    wear, basis = _wear_life(e)
    return ChannelReport(
        channel_id=cid,
        analyte=e.analyte,
        unit=unit,
        sensitivity_au_per_unit=sensitivity,
        noise_au_rms=noise,
        lod=lod,
        t90_s=t90,
        wear_life_h=wear,
        wear_life_basis=basis,
    )


def characterize(design: BandageDesign, seed: int = 0) -> DesignReport:
    twin = BandageTwin(design, seed=seed)
    return DesignReport(
        design_name=design.name,
        digest=design.digest(),
        sample_interval_s=design.sample_interval_s,
        battery_life_h=twin.power.battery_life_h(link_quality=0.92),
        channels=tuple(_measure_channel(design, e, seed) for e in design.electrodes),
    )


def compare(designs: Sequence[BandageDesign], seed: int = 0) -> List[DesignReport]:
    return [characterize(d, seed) for d in designs]


def format_table(reports: Sequence[DesignReport]) -> str:
    """Markdown table, one row per (design, channel)."""
    lines = [
        "| design | channel | analyte | sensitivity (a.u./unit) | noise (a.u. rms) | LOD | t90 (s) | wear life (h) | battery (h) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        for c in r.channels:
            t90 = "n/a" if c.t90_s is None else f"{c.t90_s:.0f}"
            wear = "inf" if math.isinf(c.wear_life_h) else f"{c.wear_life_h:.0f}"
            lines.append(
                f"| {r.design_name} | {c.channel_id} | {c.analyte} | {c.sensitivity_au_per_unit:+.3f} | "
                f"{c.noise_au_rms:.3f} | {c.lod:.3g} {c.unit} | {t90} | {wear} | {r.battery_life_h:.0f} |"
            )
    return "\n".join(lines)
