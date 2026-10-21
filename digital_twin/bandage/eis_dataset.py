"""
Synthetic EIS dataset for the graphene-PBASE-aptamer electrode vs E. coli.

One *run* is one full assay on one electrode: a baseline sweep, sample
exposure + binding, and a post-exposure sweep. The run table (`runs.csv`)
carries the labels, the fitted equivalent-circuit parameters and the
baseline-vs-post differences (dZ, dRct, dCdl, ...); the spectra table
(`spectra.csv`, long format) carries the raw measured points a hardware
analyzer would have reported. Both are written by `generate()` and read by
`load()`; backend/app/hardware_eis.py replays them.

Sample classes (what makes the set usable for detection ML, not just curve
fitting):

    blank         PBS buffer, 0 CFU/mL                         negative control
    nonspecific   non-target bacteria (S. aureus), weak        specificity control
                  low-affinity adsorption
    target        E. coli, 1e1 .. 1e7 CFU/mL                   positives

Realism on top of the ideal model in impedance.py: every electrode gets its
own lot-to-lot spread of Rs/Rct/Cdl/sigma/n, the baseline-to-post interval
adds a small non-binding Rct drift, contact time varies per run (so low
concentrations are not always at equilibrium), and both sweeps carry
seeded measurement noise. Same seed -> byte-identical files.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from digital_twin.bandage.impedance import (
    REF_FREQ_HZ,
    BindingModel,
    Calibration,
    EisParams,
    binding_kinetics,
    coverage,
    fit_randles,
    impedance,
    log_sweep,
    params_at_coverage,
)
from digital_twin.bandage.kernel import RngStreams

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "datasets" / "ecoli_eis"
RUNS_FILE = "runs.csv"
SPECTRA_FILE = "spectra.csv"
MANIFEST_FILE = "README.md"

TARGET_CFU_LEVELS: Tuple[float, ...] = (1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7)
NONSPECIFIC_CFU_LEVELS: Tuple[float, ...] = (1e3, 1e5, 1e7)
# Non-target bacteria stick weakly: far lower affinity and capped occupancy.
NONSPECIFIC_KD = 5e7
NONSPECIFIC_THETA_MAX = 0.12

RUN_FIELDS: Tuple[str, ...] = (
    "run_id", "split", "sample_class", "organism", "cfu_per_ml", "label", "replicate", "seed",
    "incubation_s", "noise_pct", "theta",
    "true_rs", "true_rct", "true_cdl", "true_sigma", "true_n",
    "fit_base_rs", "fit_base_rct", "fit_base_cdl", "fit_base_sigma", "fit_base_n",
    "fit_post_rs", "fit_post_rct", "fit_post_cdl", "fit_post_sigma", "fit_post_n",
    "z100_base_ohm", "z100_post_ohm", "phase100_base_deg", "phase100_post_deg",
    "d_z100_pct", "d_phase100_deg", "d_rct_pct", "d_cdl_pct", "d_rs_pct", "d_sigma_pct",
    "detected", "est_cfu_per_ml",
)
SPECTRA_FIELDS: Tuple[str, ...] = ("run_id", "phase", "freq_hz", "re_ohm", "im_ohm", "mag_ohm", "phase_deg")


@dataclass(frozen=True)
class Spec:
    """Which runs to make. Defaults give 84 runs (56 target, 16 blank, 12 nonspecific)."""

    seed: int = 20261021
    target_replicates: int = 8
    blank_replicates: int = 16
    nonspecific_replicates: int = 4
    noise_pct: float = 1.0
    per_decade: int = 10
    test_every: int = 4  # every 4th replicate of each condition goes to split="test"


def _pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0


def _electrode(rng, nominal: EisParams) -> EisParams:
    """One physical electrode: lot-to-lot spread around the nominal parameters."""
    ln = lambda sd: math.exp(rng.gauss(0.0, sd))  # noqa: E731
    return EisParams(
        rs=nominal.rs * ln(0.06),
        rct=nominal.rct * ln(0.10),
        cdl=nominal.cdl * ln(0.08),
        sigma=nominal.sigma * ln(0.10),
        n=min(1.0, max(0.8, rng.gauss(nominal.n, 0.01))),
    )


def _measure(freqs: List[float], p: EisParams, rng, noise_pct: float) -> List[dict]:
    s = noise_pct / 100.0
    rows = []
    for f in freqs:
        z = impedance(f, p)
        m = abs(z)
        z = complex(z.real + rng.gauss(0.0, s * m), z.imag + rng.gauss(0.0, s * m))
        rows.append(
            {"freq_hz": f, "re_ohm": z.real, "im_ohm": z.imag, "mag_ohm": abs(z),
             "phase_deg": math.degrees(math.atan2(z.imag, z.real))}
        )
    return rows


def _ref(spec: List[dict]) -> dict:
    return min(spec, key=lambda p: abs(math.log(p["freq_hz"] / REF_FREQ_HZ)))


def _conditions(spec: Spec) -> List[Tuple[str, str, float, int]]:
    out: List[Tuple[str, str, float, int]] = []
    for cfu in TARGET_CFU_LEVELS:
        out += [("target", "E. coli", cfu, r) for r in range(spec.target_replicates)]
    out += [("blank", "none (PBS)", 0.0, r) for r in range(spec.blank_replicates)]
    for cfu in NONSPECIFIC_CFU_LEVELS:
        out += [("nonspecific", "S. aureus", cfu, r) for r in range(spec.nonspecific_replicates)]
    return out


def make_run(
    run_id: str,
    sample_class: str,
    organism: str,
    cfu: float,
    replicate: int,
    spec: Spec,
    nominal: EisParams = EisParams(),
    model: BindingModel = BindingModel(),
    calibration: Calibration = Calibration(),
) -> Tuple[Dict[str, object], List[dict]]:
    rngs = RngStreams(spec.seed)
    cond = f"{sample_class}/{cfu:g}/{replicate}"
    lot = rngs.get("lot", cond)
    base = _electrode(lot, nominal)
    incubation_s = lot.uniform(300.0, 1800.0)
    drift = lot.gauss(-0.005, 0.003)  # non-binding Rct drift between the two sweeps

    if sample_class == "target":
        theta = binding_kinetics(incubation_s, cfu)
    elif sample_class == "nonspecific":
        theta = NONSPECIFIC_THETA_MAX * coverage(cfu, NONSPECIFIC_KD)
    else:
        theta = 0.0
    post = params_at_coverage(base, theta, model)
    post = EisParams(post.rs, post.rct * (1.0 + drift), post.cdl, post.sigma, post.n)

    freqs = log_sweep(per_decade=spec.per_decade)
    base_spec = _measure(freqs, base, rngs.get("meas", cond, "baseline"), spec.noise_pct)
    post_spec = _measure(freqs, post, rngs.get("meas", cond, "post"), spec.noise_pct)

    cplx = lambda s: [complex(p["re_ohm"], p["im_ohm"]) for p in s]  # noqa: E731
    fb, _ = fit_randles(freqs, cplx(base_spec))
    fp, _ = fit_randles(freqs, cplx(post_spec))
    zb, zp = _ref(base_spec), _ref(post_spec)
    d_rct = _pct(fp.rct, fb.rct)

    est = calibration.estimate_cfu(d_rct)
    row: Dict[str, object] = {
        "run_id": run_id,
        "split": "test" if (replicate + 1) % spec.test_every == 0 else "train",
        "sample_class": sample_class,
        "organism": organism,
        "cfu_per_ml": cfu,
        "label": int(sample_class == "target"),
        "replicate": replicate,
        "seed": spec.seed,
        "incubation_s": incubation_s,
        "noise_pct": spec.noise_pct,
        "theta": theta,
        "true_rs": base.rs, "true_rct": base.rct, "true_cdl": base.cdl, "true_sigma": base.sigma, "true_n": base.n,
        "fit_base_rs": fb.rs, "fit_base_rct": fb.rct, "fit_base_cdl": fb.cdl, "fit_base_sigma": fb.sigma, "fit_base_n": fb.n,
        "fit_post_rs": fp.rs, "fit_post_rct": fp.rct, "fit_post_cdl": fp.cdl, "fit_post_sigma": fp.sigma, "fit_post_n": fp.n,
        "z100_base_ohm": zb["mag_ohm"], "z100_post_ohm": zp["mag_ohm"],
        "phase100_base_deg": zb["phase_deg"], "phase100_post_deg": zp["phase_deg"],
        "d_z100_pct": _pct(zp["mag_ohm"], zb["mag_ohm"]),
        "d_phase100_deg": zp["phase_deg"] - zb["phase_deg"],
        "d_rct_pct": d_rct,
        "d_cdl_pct": _pct(fp.cdl, fb.cdl),
        "d_rs_pct": _pct(fp.rs, fb.rs),
        "d_sigma_pct": _pct(fp.sigma, fb.sigma) if fb.sigma > 0 else 0.0,
        "detected": int(d_rct > calibration.threshold_pct),
        "est_cfu_per_ml": "" if est is None else est,
    }
    spectra = [{"run_id": run_id, "phase": ph, **pt} for ph, sp in (("baseline", base_spec), ("post", post_spec)) for pt in sp]
    return row, spectra


def generate(out_dir: Path = DEFAULT_DIR, spec: Spec = Spec()) -> Tuple[int, int]:
    """Write runs.csv + spectra.csv into `out_dir`; returns (n_runs, n_spectrum_rows)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: List[Dict[str, object]] = []
    spectra: List[dict] = []
    for i, (cls, organism, cfu, rep) in enumerate(_conditions(spec), start=1):
        row, sp = make_run(f"EIS-{i:03d}", cls, organism, cfu, rep, spec)
        runs.append(row)
        spectra += sp
    with open(out_dir / RUNS_FILE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()} for r in runs)
    with open(out_dir / SPECTRA_FILE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SPECTRA_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows({k: (f"{v:.7g}" if isinstance(v, float) else v) for k, v in r.items()} for r in spectra)
    return len(runs), len(spectra)


# --------------------------------------------------------------------------- loading
_NUMERIC = set(RUN_FIELDS) - {"run_id", "split", "sample_class", "organism"}


@dataclass(frozen=True)
class Dataset:
    runs: Dict[str, Dict[str, object]]  # run_id -> row (numbers parsed)
    spectra: Dict[Tuple[str, str], List[dict]]  # (run_id, phase) -> points ordered by frequency

    def run_ids(self) -> List[str]:
        return list(self.runs)

    def sweep(self, run_id: str, phase: str) -> List[dict]:
        return self.spectra[(run_id, phase)]


def load(directory: Optional[Path] = None) -> Dataset:
    d = Path(directory) if directory is not None else DEFAULT_DIR
    runs: Dict[str, Dict[str, object]] = {}
    with open(d / RUNS_FILE, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            runs[r["run_id"]] = {
                k: (None if v == "" else float(v)) if k in _NUMERIC else v for k, v in r.items()
            }
    spectra: Dict[Tuple[str, str], List[dict]] = {}
    with open(d / SPECTRA_FILE, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            spectra.setdefault((r["run_id"], r["phase"]), []).append(
                {k: float(r[k]) for k in ("freq_hz", "re_ohm", "im_ohm", "mag_ohm", "phase_deg")}
            )
    for pts in spectra.values():
        pts.sort(key=lambda p: p["freq_hz"])
    return Dataset(runs, spectra)
