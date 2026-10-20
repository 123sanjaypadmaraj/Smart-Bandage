"""
Bridge from the running `BandageTwin` to the EIS biosensor model.

The aptamer electrode's pathogen-marker occupancy theta (the twin's own
Langmuir/kinetic state) sets the EIS coverage; the sensor parameters and
spectrum then follow from `impedance.params_at_coverage`. Read-only: nothing
here mutates the twin, and the reading is noise-free unless a noise level is
asked for (then it uses a seeded stream, never global `random`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from digital_twin.bandage.impedance import (
    REF_FREQ_HZ,
    BindingModel,
    Calibration,
    EisParams,
    impedance,
    log_sweep,
    params_at_coverage,
    spectrum,
)
from digital_twin.bandage.kernel import RngStreams
from digital_twin.bandage.twin import BandageTwin


@dataclass(frozen=True)
class EisReading:
    channel_id: str
    t_s: float
    local_concentration_nM: float
    theta: float
    cfu_equivalent_per_ml: float  # inverse Langmuir of theta with the EIS model's Kd
    baseline_params: EisParams
    params: EisParams
    spectrum: tuple
    z_ref_mag: float
    z_ref_phase_deg: float
    d_z_ref_pct: float
    d_rct_pct: float
    d_cdl_pct: float
    detected: bool
    estimated_cfu: Optional[float]

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "t_s": self.t_s,
            "local_concentration_nM": self.local_concentration_nM,
            "theta": self.theta,
            "cfu_equivalent_per_ml": self.cfu_equivalent_per_ml,
            "baseline_params": self.baseline_params.to_dict(),
            "params": self.params.to_dict(),
            "spectrum": [dict(p) for p in self.spectrum],
            "z_ref": {"freq": REF_FREQ_HZ, "mag": self.z_ref_mag, "phase_deg": self.z_ref_phase_deg},
            "delta": {"z_ref_pct": self.d_z_ref_pct, "rct_pct": self.d_rct_pct, "cdl_pct": self.d_cdl_pct},
            "detection": {"detected": self.detected, "estimated_cfu": self.estimated_cfu},
        }


def eis_from_twin(
    twin: BandageTwin,
    channel_id: str,
    base: EisParams = EisParams(),
    model: BindingModel = BindingModel(),
    calibration: Calibration = Calibration(),
    freqs: Optional[Sequence[float]] = None,
    noise_pct: float = 0.0,
    seed: int = 0,
) -> EisReading:
    """EIS reading of the twin's aptamer (pathogen-marker) electrode `channel_id` now."""
    truth = twin.truth()
    ch = truth.channels.get(channel_id)
    if ch is None:
        raise KeyError(f"unknown channel {channel_id!r}; have {sorted(truth.channels)}")
    electrode = twin.electrodes[channel_id]
    if electrode.spec.analyte != "pathogen_marker":
        raise ValueError(f"channel {channel_id!r} senses {electrode.spec.analyte!r}, not a pathogen marker")
    theta = min(1.0, max(0.0, float(electrode.transducer.occupancy)))
    fr: List[float] = list(freqs) if freqs is not None else log_sweep()
    p = params_at_coverage(base, theta, model)
    spec = spectrum(fr, p)
    if noise_pct > 0:
        rng = RngStreams(seed).get("eis", "twin", channel_id)
        s = noise_pct / 100.0
        for pt in spec:
            re = pt["re"] + rng.gauss(0.0, s * pt["mag"])
            im = pt["im"] + rng.gauss(0.0, s * pt["mag"])
            pt.update(re=re, im=im, mag=math.hypot(re, im), phase_deg=math.degrees(math.atan2(im, re)))
    z0, z1 = impedance(REF_FREQ_HZ, base), impedance(REF_FREQ_HZ, p)
    d_rct = (p.rct - base.rct) / base.rct * 100.0
    return EisReading(
        channel_id=channel_id,
        t_s=twin.clock.t_s,
        local_concentration_nM=ch.local_concentration,
        theta=theta,
        cfu_equivalent_per_ml=model.kd * theta / (1.0 - theta) if theta < 1.0 - 1e-12 else 1e12,
        baseline_params=base,
        params=p,
        spectrum=tuple(spec),
        z_ref_mag=abs(z1),
        z_ref_phase_deg=math.degrees(math.atan2(z1.imag, z1.real)),
        d_z_ref_pct=(abs(z1) - abs(z0)) / abs(z0) * 100.0,
        d_rct_pct=d_rct,
        d_cdl_pct=(p.cdl - base.cdl) / base.cdl * 100.0,
        detected=d_rct > calibration.threshold_pct,
        estimated_cfu=calibration.estimate_cfu(d_rct),
    )
