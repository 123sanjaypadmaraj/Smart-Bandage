"""
Emulated EIS analyzer: replays datasets/ecoli_eis/ the way the real hardware
(an AD5933-style impedance front end on the bandage reader) would report it.

The device never hands the backend an interpreted result. It streams one
frequency point at a time as signed 16-bit real/imaginary ADC counts, in the
order of the assay protocol:

    idle -> baseline sweep -> exposure -> binding -> post-exposure sweep
         -> compare -> result

so this module does the same: a `capture()` is the ordered list of frames,
and everything past the raw counts -- the equivalent-circuit fit, dRct, dZ,
the calibration lookup, the detect/no-detect call -- is computed here from
the counts that were "received", never read back from the dataset's labels
(those are attached only as `truth`, for comparing the call to the answer).

Frame shapes (`type` discriminates; backend/app/routers/hardware_eis.py
sends them as-is over WS and as a list over REST):

  phase   {type, phase, t_s}
  point   {type, phase, seq, t_s, freq_hz, raw_real, raw_imag,
           re_ohm, im_ohm, mag_ohm, phase_deg}
  result  {type, phase:"result", t_s, baseline, post, delta, detection, truth}
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, List, Optional

from digital_twin.bandage.eis_dataset import Dataset, Spec, load, make_run
from digital_twin.bandage.impedance import REF_FREQ_HZ, Calibration, fit_randles

# Front-end scaling: ohms per ADC count. 0.25 ohm/LSB covers +/-8.19 kohm in
# int16, above the largest real/imag part at 0.1 Hz for this electrode.
OHM_PER_COUNT = 0.25
ADC_MIN, ADC_MAX = -32768, 32767

SWEEP_POINT_S = 2.0  # instrument time per frequency point (simulated clock, not wall)
EXPOSURE_S = 60.0
SETTLE_S = 60.0

_calibration = Calibration()


@lru_cache(maxsize=1)
def dataset() -> Dataset:
    return load()


def list_samples(sample_class: Optional[str] = None) -> List[dict]:
    out = []
    for r in dataset().runs.values():
        if sample_class is not None and r["sample_class"] != sample_class:
            continue
        out.append(
            {
                "run_id": r["run_id"],
                "split": r["split"],
                "sample_class": r["sample_class"],
                "organism": r["organism"],
                "cfu_per_ml": r["cfu_per_ml"],
                "label": int(r["label"]),
                "incubation_s": r["incubation_s"],
            }
        )
    return out


def _adc(value_ohm: float) -> int:
    return max(ADC_MIN, min(ADC_MAX, round(value_ohm / OHM_PER_COUNT)))


def _quantize(point: dict) -> dict:
    """A dataset point as the hardware reports it: ADC counts, then the ohm
    values the firmware derives from those counts."""
    raw_re, raw_im = _adc(point["re_ohm"]), _adc(point["im_ohm"])
    re, im = raw_re * OHM_PER_COUNT, raw_im * OHM_PER_COUNT
    return {
        "freq_hz": point["freq_hz"],
        "raw_real": raw_re,
        "raw_imag": raw_im,
        "re_ohm": re,
        "im_ohm": im,
        "mag_ohm": math.hypot(re, im),
        "phase_deg": math.degrees(math.atan2(im, re)),
    }


def _pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0


def _ref(points: List[dict]) -> dict:
    return min(points, key=lambda p: abs(math.log(p["freq_hz"] / REF_FREQ_HZ)))


def _fit_block(points: List[dict]):
    freqs = [p["freq_hz"] for p in points]
    fit, rms = fit_randles(freqs, [complex(p["re_ohm"], p["im_ohm"]) for p in points])
    ref = _ref(points)
    return fit, {
        "params_fit": fit.to_dict(),
        "fit_rms": rms,
        "z_ref": {"freq": ref["freq_hz"], "mag": ref["mag_ohm"], "phase_deg": ref["phase_deg"]},
    }


def capture(
    run_id: Optional[str] = None,
    *,
    sample_class: str = "target",
    cfu: float = 0.0,
    seed: int = 0,
) -> List[dict]:
    """The full ordered frame list for one assay.

    `run_id` replays a stored dataset run. Without it a fresh run is
    synthesised from (sample_class, cfu, seed) with the dataset's own
    generator, so any concentration can be explored, not just the stored ones."""
    if run_id is not None:
        runs = dataset().runs
        if run_id not in runs:
            raise KeyError(f"unknown run_id {run_id!r}")
        run = runs[run_id]
        base_pts, post_pts = dataset().sweep(run_id, "baseline"), dataset().sweep(run_id, "post")
    else:
        organism = {"target": "E. coli", "nonspecific": "S. aureus"}.get(sample_class, "none (PBS)")
        if sample_class not in ("target", "nonspecific", "blank"):
            raise ValueError(f"sample_class must be target, nonspecific or blank, not {sample_class!r}")
        run, spectra = make_run(
            "LIVE", sample_class, organism, 0.0 if sample_class == "blank" else cfu, 0, Spec(seed=seed)
        )
        base_pts = [s for s in spectra if s["phase"] == "baseline"]
        post_pts = [s for s in spectra if s["phase"] == "post"]

    frames: List[dict] = []
    t = 0.0

    def phase(name: str, dt: float = 0.0) -> None:
        nonlocal t
        t += dt
        frames.append({"type": "phase", "phase": name, "t_s": t})

    def sweep(name: str, pts: List[dict]) -> List[dict]:
        nonlocal t
        got = []
        for seq, p in enumerate(pts):
            t += SWEEP_POINT_S
            q = _quantize(p)
            got.append(q)
            frames.append({"type": "point", "phase": name, "seq": seq, "t_s": t, **q})
        return got

    phase("idle")
    phase("baseline")
    base_rx = sweep("baseline", base_pts)
    phase("exposure")
    phase("binding", EXPOSURE_S)
    phase("measure2", float(run["incubation_s"]))
    post_rx = sweep("measure2", post_pts)
    phase("compare", SETTLE_S)

    base_fit, base_block = _fit_block(base_rx)
    post_fit, post_block = _fit_block(post_rx)
    zb, zp = base_block["z_ref"], post_block["z_ref"]
    d_rct = _pct(post_fit.rct, base_fit.rct)
    detected = d_rct > _calibration.threshold_pct
    t += 1.0
    frames.append(
        {
            "type": "result",
            "phase": "result",
            "t_s": t,
            "baseline": base_block,
            "post": post_block,
            "delta": {
                "z_ref_pct": _pct(zp["mag"], zb["mag"]),
                "phase_ref_deg": zp["phase_deg"] - zb["phase_deg"],
                "rct_pct": d_rct,
                "cdl_pct": _pct(post_fit.cdl, base_fit.cdl),
                "rs_pct": _pct(post_fit.rs, base_fit.rs),
                "sigma_pct": _pct(post_fit.sigma, base_fit.sigma) if base_fit.sigma > 0 else 0.0,
            },
            "detection": {
                "detected": detected,
                "threshold_pct": _calibration.threshold_pct,
                "estimated_cfu": _calibration.estimate_cfu(d_rct),
                "lod_cfu": _calibration.lod_cfu(),
            },
            "truth": {
                "run_id": run["run_id"],
                "sample_class": run["sample_class"],
                "organism": run["organism"],
                "cfu_per_ml": run["cfu_per_ml"],
                "label": int(run["label"]),
                "correct": detected == bool(run["label"]),
            },
        }
    )
    return frames
