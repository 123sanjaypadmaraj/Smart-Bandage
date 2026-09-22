"""
EIS biosensor model: graphene-PBASE-aptamer working electrode read by
electrochemical impedance spectroscopy against E. coli.

Equivalent circuit = modified Randles (contract shared with the TypeScript
port in frontend/src/twin3d/types.ts):

    Z(w)  = Rs + 1 / ( 1/(Rct + Zw(w)) + Q (j w)^n )      CPE for the double layer
    Zw(w) = sigma / sqrt(w) * (1 - j)                     semi-infinite Warburg
    Q     = Cdl^n * (1/Rs + 1/Rct)^(1 - n)               Brug relation, inverse of
                                                          Cdl = Q^(1/n) (1/Rs + 1/Rct)^((n-1)/n)
                                                          (Q == Cdl when n == 1)

Binding (Langmuir), C in CFU/mL:

    theta = C / (Kd + C)
    Rct   = Rct0   (1 + A_RCT   theta)      bacteria insulate the surface
    Cdl   = Cdl0   (1 - A_CDL   theta)      thicker dielectric layer
    Rs    = Rs0    (1 + A_RS    theta)
    sigma = sigma0 (1 + A_SIGMA theta)      redox-probe diffusion hindered

Detection: dRct% = (Rct_post - Rct_base) / Rct_base * 100, positive when it
exceeds k_sigma * baseline-noise SD; concentration estimated from the log
calibration curve dRct% = slope * log10(CFU/mL) + intercept.

Everything is a pure function of its arguments; the only randomness is the
measurement noise in `EisAssay`, drawn from seeded `RngStreams` (no global
`random`, no wall clock). All parameter values are illustrative, not fitted to
bench data.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from digital_twin.bandage.kernel import RngStreams, SimClock

REF_FREQ_HZ = 100.0
DEFAULT_KD_CFU_ML = 1e4

ASSAY_PHASES: Tuple[str, ...] = ("idle", "baseline", "exposure", "binding", "measure2", "compare", "result")


# --------------------------------------------------------------------------- parameters
def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


@dataclass(frozen=True)
class EisParams:
    """Linearised sensor parameters (the 'sensor parameters')."""

    rs: float = 120.0  # solution resistance, ohm
    rct: float = 2000.0  # charge-transfer resistance, ohm
    cdl: float = 2.0e-6  # double-layer capacitance, F
    sigma: float = 150.0  # Warburg coefficient, ohm s^-1/2
    n: float = 0.92  # CPE exponent (1 = ideal capacitor)

    def __post_init__(self) -> None:
        _check(self.rs > 0, "rs must be > 0")
        _check(self.rct > 0, "rct must be > 0")
        _check(self.cdl > 0, "cdl must be > 0")
        _check(self.sigma >= 0, "sigma must be >= 0")
        _check(0 < self.n <= 1, "n must be in (0, 1]")

    @property
    def q(self) -> float:
        """CPE coefficient Q (F s^(n-1)) from the reported Cdl (Brug inverse)."""
        return q_from_cdl(self.cdl, self.rs, self.rct, self.n)

    def to_dict(self) -> Dict[str, float]:
        return {"rs": self.rs, "rct": self.rct, "cdl": self.cdl, "sigma": self.sigma, "n": self.n}


def q_from_cdl(cdl: float, rs: float, rct: float, n: float) -> float:
    if n == 1.0:
        return cdl
    return cdl ** n * (1.0 / rs + 1.0 / rct) ** (1.0 - n)


def cdl_from_q(q: float, rs: float, rct: float, n: float) -> float:
    if n == 1.0:
        return q
    return q ** (1.0 / n) * (1.0 / rs + 1.0 / rct) ** ((n - 1.0) / n)


@dataclass(frozen=True)
class BindingModel:
    kd: float = DEFAULT_KD_CFU_ML  # CFU/mL
    a_rct: float = 1.6
    a_cdl: float = 0.35
    a_rs: float = 0.04
    a_sigma: float = 0.5

    def __post_init__(self) -> None:
        _check(self.kd > 0, "kd must be > 0")
        _check(self.a_rct >= 0 and self.a_rs >= 0 and self.a_sigma >= 0, "a_rct/a_rs/a_sigma must be >= 0")
        _check(0 <= self.a_cdl < 1, "a_cdl must be in [0, 1)")


@dataclass(frozen=True)
class Calibration:
    """dRct% = slope * log10(CFU/mL) + intercept; LOD threshold = k_sigma * noise SD."""

    # defaults = Calibration.fit_to_model() over 1e2..1e5 CFU/mL (the browser twin uses the same line)
    slope: float = 54.8246883402954  # % per decade
    intercept: float = -136.12773025202046  # %
    noise_sd_pct: float = 1.5  # baseline-noise SD of dRct, %
    k_sigma: float = 3.0

    def __post_init__(self) -> None:
        _check(self.slope > 0, "slope must be > 0")
        _check(self.noise_sd_pct >= 0 and self.k_sigma > 0, "noise_sd_pct >= 0 and k_sigma > 0 required")

    @property
    def threshold_pct(self) -> float:
        return self.k_sigma * self.noise_sd_pct

    def lod_cfu(self) -> float:
        """Concentration whose calibrated dRct% equals the LOD threshold."""
        return 10.0 ** ((self.threshold_pct - self.intercept) / self.slope)

    def estimate_cfu(self, d_rct_pct: float) -> Optional[float]:
        """Invert the curve; None when dRct% is not above the LOD threshold."""
        if not d_rct_pct > self.threshold_pct:
            return None
        return 10.0 ** ((d_rct_pct - self.intercept) / self.slope)

    def to_dict(self) -> Dict[str, float]:
        return {
            "slope": self.slope,
            "intercept": self.intercept,
            "noise_sd_pct": self.noise_sd_pct,
            "k_sigma": self.k_sigma,
            "threshold_pct": self.threshold_pct,
            "lod_cfu": self.lod_cfu(),
        }

    @staticmethod
    def fit_to_model(
        model: BindingModel = BindingModel(),
        cfu_lo: float = 1e2,
        cfu_hi: float = 1e5,
        noise_sd_pct: float = 1.5,
        k_sigma: float = 3.0,
    ) -> "Calibration":
        """Log-linear least-squares fit of the model's own noise-free dRct%
        over [cfu_lo, cfu_hi]. The default `Calibration()` is this same fit
        (over 1e2..1e5 CFU/mL), so the API and the browser twin agree; the sensor
        saturates at dRct% = 160 %, so estimates above ~1e5 CFU/mL under-read."""
        x = np.linspace(math.log10(cfu_lo), math.log10(cfu_hi), 41)
        y = np.array([100.0 * model.a_rct * coverage(10.0 ** v, model.kd) for v in x])
        slope, intercept = np.polyfit(x, y, 1)
        return Calibration(float(slope), float(intercept), noise_sd_pct, k_sigma)


# --------------------------------------------------------------------------- physics
def impedance(freq_hz: float, p: EisParams) -> complex:
    """Complex impedance of the modified Randles circuit at `freq_hz`."""
    _check(freq_hz > 0, "freq_hz must be > 0")
    w = 2.0 * math.pi * freq_hz
    zw = p.sigma / math.sqrt(w) * complex(1.0, -1.0)
    y_cpe = p.q * w ** p.n * cmath.exp(1j * math.pi * p.n / 2.0)
    return p.rs + 1.0 / (1.0 / (p.rct + zw) + y_cpe)


def _point(freq_hz: float, z: complex) -> Dict[str, float]:
    return {
        "freq": freq_hz,
        "re": z.real,
        "im": z.imag,
        "mag": abs(z),
        "phase_deg": math.degrees(math.atan2(z.imag, z.real)),
    }


def spectrum(freqs: Sequence[float], p: EisParams) -> List[Dict[str, float]]:
    return [_point(f, impedance(f, p)) for f in freqs]


def log_sweep(f_lo: float = 0.1, f_hi: float = 1e5, per_decade: int = 10) -> List[float]:
    """Log-spaced frequencies f_lo..f_hi inclusive: 10**(log10(f_lo) + k/per_decade)."""
    _check(0 < f_lo < f_hi and per_decade >= 1, "need 0 < f_lo < f_hi and per_decade >= 1")
    lo = math.log10(f_lo)
    npts = int(round((math.log10(f_hi) - lo) * per_decade))
    return [10.0 ** (lo + k / per_decade) for k in range(npts + 1)]


def coverage(cfu: float, kd: float = DEFAULT_KD_CFU_ML) -> float:
    """Langmuir equilibrium occupancy theta = C / (Kd + C)."""
    _check(cfu >= 0, "cfu must be >= 0")
    _check(kd > 0, "kd must be > 0")
    return cfu / (kd + cfu)


def params_at_coverage(base: EisParams, theta: float, model: BindingModel = BindingModel()) -> EisParams:
    _check(0.0 <= theta <= 1.0, "theta must be in [0, 1]")
    return EisParams(
        rs=base.rs * (1.0 + model.a_rs * theta),
        rct=base.rct * (1.0 + model.a_rct * theta),
        cdl=base.cdl * (1.0 - model.a_cdl * theta),
        sigma=base.sigma * (1.0 + model.a_sigma * theta),
        n=base.n,
    )


def binding_kinetics(t_s: float, cfu: float, k_on: float = 1e-6, k_off: float = 1e-2, theta0: float = 0.0) -> float:
    """theta(t) for d(theta)/dt = k_on C (1 - theta) - k_off theta, closed form.

    k_on in 1/((CFU/mL) s), k_off in 1/s; Kd = k_off / k_on (defaults give the
    1e4 CFU/mL of BindingModel). Exact for any t_s."""
    _check(t_s >= 0 and cfu >= 0 and k_on > 0 and k_off > 0, "t_s, cfu >= 0 and k_on, k_off > 0 required")
    _check(0.0 <= theta0 <= 1.0, "theta0 must be in [0, 1]")
    rate = k_on * cfu + k_off
    theta_eq = k_on * cfu / rate
    return theta_eq + (theta0 - theta_eq) * math.exp(-rate * t_s)


# --------------------------------------------------------------------------- fitting
def _z_array(f: np.ndarray, rs: float, rct: float, q: float, sigma: float, n: float) -> np.ndarray:
    w = 2.0 * np.pi * f
    zw = sigma / np.sqrt(w) * (1.0 - 1j)
    y = 1.0 / (rct + zw) + q * w ** n * np.exp(1j * np.pi * n / 2.0)
    return rs + 1.0 / y


_LOG_LO = np.log([1e-3, 1e-3, 1e-15, 1e-6])
_LOG_HI = np.log([1e9, 1e9, 1e-1, 1e9])
_N_LO, _N_HI = 0.3, 1.0


def _unpack(x: np.ndarray) -> Tuple[float, float, float, float, float]:
    e = np.exp(np.clip(x[:4], _LOG_LO, _LOG_HI))
    return float(e[0]), float(e[1]), float(e[2]), float(e[3]), float(np.clip(x[4], _N_LO, _N_HI))


def _levenberg_marquardt(x0: np.ndarray, resid, iters: int = 120) -> Tuple[np.ndarray, float]:
    x = x0.copy()
    r = resid(x)
    cost = float(r @ r)
    lam = 1e-2
    for _ in range(iters):
        jac = np.empty((r.size, x.size))
        for k in range(x.size):
            h = 1e-6 * max(1.0, abs(x[k]))
            xp, xm = x.copy(), x.copy()
            xp[k] += h
            xm[k] -= h
            jac[:, k] = (resid(xp) - resid(xm)) / (2.0 * h)
        a, g = jac.T @ jac, jac.T @ r
        improved = False
        for _try in range(12):
            try:
                step = np.linalg.solve(a + lam * np.diag(np.diag(a) + 1e-12), -g)
            except np.linalg.LinAlgError:
                lam *= 10.0
                continue
            xn = x + step
            rn = resid(xn)
            cn = float(rn @ rn)
            if cn < cost:
                converged = (cost - cn) <= 1e-14 * max(cost, 1e-30)
                x, r, cost, lam = xn, rn, cn, max(lam / 5.0, 1e-9)
                improved = True
                break
            lam *= 5.0
        if not improved or converged:
            break
    return x, cost


def fit_randles(
    freqs: Sequence[float], z: Sequence[complex]
) -> Tuple[EisParams, float]:
    """Least-squares fit of the modified Randles circuit to a spectrum.

    Levenberg-Marquardt on (ln Rs, ln Rct, ln Q, ln sigma, n) from several
    starting points, minimising the relative complex residual
    |Z_model - Z_meas| / |Z_meas|. Returns (EisParams with Cdl from the Brug
    relation, rms relative residual). Needs no scipy."""
    f = np.asarray(freqs, dtype=float)
    zm = np.asarray(z, dtype=complex)
    _check(f.size == zm.size and f.size >= 6, "need >= 6 points and matching freqs/z")
    order = np.argsort(f)
    f, zm = f[order], zm[order]
    scale = 1.0 / np.abs(zm)

    def resid(x: np.ndarray) -> np.ndarray:
        d = (_z_array(f, *_unpack(x)) - zm) * scale
        return np.concatenate([d.real, d.imag])

    rs0 = max(float(zm.real[-1]), 1e-2)
    rlow = float(zm.real[0])
    rct0 = max(0.6 * (rlow - rs0), 1e-2 * rs0 + 1.0)
    k = int(np.argmax(-zm.imag))
    w_pk = 2.0 * np.pi * float(f[k])
    starts = []
    for n0 in (0.9, 0.75):
        for sig0 in (0.05 * rct0, 0.2 * rct0):
            for qscale in (1.0, 0.3, 3.0):
                q0 = qscale / (rct0 * w_pk ** n0)
                starts.append(np.array([math.log(rs0), math.log(rct0), math.log(q0), math.log(sig0), n0]))
    best_x, best_c = None, math.inf
    for x0 in starts:
        x, c = _levenberg_marquardt(x0, resid)
        if c < best_c:
            best_x, best_c = x, c
    rs, rct, q, sigma, n = _unpack(best_x)
    params = EisParams(rs=rs, rct=rct, cdl=cdl_from_q(q, rs, rct, n), sigma=sigma, n=n)
    return params, math.sqrt(best_c / (2.0 * f.size))


# --------------------------------------------------------------------------- assay
@dataclass(frozen=True)
class EisResult:
    cfu: float
    seed: int
    noise_pct: float
    incubation_s: Optional[float]
    theta: float
    freqs: Tuple[float, ...]
    baseline_true: EisParams
    post_true: EisParams
    baseline_spectrum: Tuple[dict, ...]  # measured (noisy)
    post_spectrum: Tuple[dict, ...]
    baseline_fit: EisParams
    post_fit: EisParams
    baseline_fit_rms: float
    post_fit_rms: float
    z_ref_baseline: dict
    z_ref_post: dict
    d_z_ref_pct: float
    d_phase_ref_deg: float
    d_rct_pct: float
    d_cdl_pct: float
    d_rs_pct: float
    d_sigma_pct: float
    threshold_pct: float
    detected: bool
    estimated_cfu: Optional[float]
    calibration: Calibration
    timeline: Tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "phase": "result",
            "cfu": self.cfu,
            "seed": self.seed,
            "noise_pct": self.noise_pct,
            "incubation_s": self.incubation_s,
            "theta": self.theta,
            "ref_freq_hz": REF_FREQ_HZ,
            "freqs": list(self.freqs),
            "baseline": {
                "params_true": self.baseline_true.to_dict(),
                "params_fit": self.baseline_fit.to_dict(),
                "fit_rms": self.baseline_fit_rms,
                "spectrum": [dict(p) for p in self.baseline_spectrum],
                "z_ref": dict(self.z_ref_baseline),
            },
            "post": {
                "params_true": self.post_true.to_dict(),
                "params_fit": self.post_fit.to_dict(),
                "fit_rms": self.post_fit_rms,
                "spectrum": [dict(p) for p in self.post_spectrum],
                "z_ref": dict(self.z_ref_post),
            },
            "delta": {
                "z_ref_pct": self.d_z_ref_pct,
                "phase_ref_deg": self.d_phase_ref_deg,
                "rct_pct": self.d_rct_pct,
                "cdl_pct": self.d_cdl_pct,
                "rs_pct": self.d_rs_pct,
                "sigma_pct": self.d_sigma_pct,
            },
            "detection": {
                "detected": self.detected,
                "threshold_pct": self.threshold_pct,
                "estimated_cfu": self.estimated_cfu,
                "lod_cfu": self.calibration.lod_cfu(),
            },
            "calibration": self.calibration.to_dict(),
            "timeline": [dict(t) for t in self.timeline],
        }


def _pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0


class EisAssay:
    """Phase-by-phase EIS assay: idle -> baseline -> exposure -> binding ->
    measure2 -> compare -> result, with seeded measurement noise.

    incubation_s=None assumes the binding step ran to equilibrium (the
    calibration regime); a number uses `binding_kinetics` for that contact
    time instead."""

    def __init__(
        self,
        base: EisParams = EisParams(),
        model: BindingModel = BindingModel(),
        calibration: Calibration = Calibration(),
        freqs: Optional[Sequence[float]] = None,
        k_on: float = 1e-6,
        k_off: float = 1e-2,
    ) -> None:
        self.base = base
        self.model = model
        self.calibration = calibration
        self.freqs: Tuple[float, ...] = tuple(freqs) if freqs is not None else tuple(log_sweep())
        self.k_on = k_on
        self.k_off = k_off

    def _measure(self, params: EisParams, rng, noise_pct: float) -> List[dict]:
        s = noise_pct / 100.0
        out = []
        for f in self.freqs:
            z = impedance(f, params)
            if s > 0:
                m = abs(z)
                z = complex(z.real + rng.gauss(0.0, s * m), z.imag + rng.gauss(0.0, s * m))
            out.append(_point(f, z))
        return out

    def _ref_point(self, spec: Sequence[dict]) -> dict:
        return dict(min(spec, key=lambda p: abs(math.log(p["freq"] / REF_FREQ_HZ))))

    def run(
        self,
        cfu: float,
        seed: int = 0,
        noise_pct: float = 1.0,
        incubation_s: Optional[float] = None,
        exposure_s: float = 60.0,
    ) -> EisResult:
        _check(cfu >= 0, "cfu must be >= 0")
        _check(noise_pct >= 0, "noise_pct must be >= 0")
        _check(incubation_s is None or incubation_s >= 0, "incubation_s must be >= 0")
        rngs = RngStreams(seed)
        clock = SimClock()
        timeline: List[dict] = []

        def mark(phase: str, theta: float, dt: float = 0.0) -> None:
            clock.advance(dt)
            timeline.append({"phase": phase, "t_s": clock.t_s, "theta": theta})

        mark("idle", 0.0)
        base_spec = self._measure(self.base, rngs.get("eis", "baseline"), noise_pct)
        mark("baseline", 0.0, 60.0)
        mark("exposure", 0.0, exposure_s)
        if incubation_s is None:
            theta = coverage(cfu, self.model.kd)
            t_bind = 1800.0
        else:
            theta = binding_kinetics(incubation_s, cfu, self.k_on, self.k_off)
            t_bind = incubation_s
        mark("binding", theta, t_bind)
        post_true = params_at_coverage(self.base, theta, self.model)
        post_spec = self._measure(post_true, rngs.get("eis", "post"), noise_pct)
        mark("measure2", theta, 60.0)

        fit_freqs = self.freqs
        base_fit, base_rms = fit_randles(fit_freqs, [complex(p["re"], p["im"]) for p in base_spec])
        post_fit, post_rms = fit_randles(fit_freqs, [complex(p["re"], p["im"]) for p in post_spec])
        mark("compare", theta, 1.0)

        zb, zp = self._ref_point(base_spec), self._ref_point(post_spec)
        d_rct = _pct(post_fit.rct, base_fit.rct)
        detected = d_rct > self.calibration.threshold_pct
        est = self.calibration.estimate_cfu(d_rct)
        mark("result", theta, 1.0)
        return EisResult(
            cfu=float(cfu),
            seed=int(rngs.seed),
            noise_pct=float(noise_pct),
            incubation_s=incubation_s,
            theta=theta,
            freqs=self.freqs,
            baseline_true=self.base,
            post_true=post_true,
            baseline_spectrum=tuple(base_spec),
            post_spectrum=tuple(post_spec),
            baseline_fit=base_fit,
            post_fit=post_fit,
            baseline_fit_rms=base_rms,
            post_fit_rms=post_rms,
            z_ref_baseline=zb,
            z_ref_post=zp,
            d_z_ref_pct=_pct(zp["mag"], zb["mag"]),
            d_phase_ref_deg=zp["phase_deg"] - zb["phase_deg"],
            d_rct_pct=d_rct,
            d_cdl_pct=_pct(post_fit.cdl, base_fit.cdl),
            d_rs_pct=_pct(post_fit.rs, base_fit.rs),
            d_sigma_pct=_pct(post_fit.sigma, base_fit.sigma) if base_fit.sigma > 0 else 0.0,
            threshold_pct=self.calibration.threshold_pct,
            detected=detected,
            estimated_cfu=est,
            calibration=self.calibration,
            timeline=tuple(timeline),
        )
