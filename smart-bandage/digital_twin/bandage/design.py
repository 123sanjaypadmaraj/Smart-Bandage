"""
BandageDesign: a declarative description of the *physical* smart bandage.

The wound-state twins (DT-1/2/4) model the patient. Nothing so far described
the device: what layers sit between the wound and the electrode, what each
electrode is made of, what the electronics and battery are. This module is
that description. It is pure data -- frozen, validated, JSON round-trippable
and hashable to a digest -- and `BandageTwin` builds its physics from it, so
"what if the membrane were thinner / the battery smaller / the aptamer weaker"
is a one-line `dataclasses.replace` and a rerun, not a code change.

Stack, wound side to skin side:

    wound bed
      | contact layer   (hydrogel / mesh; wets with exudate)
      | absorbent pad    (hydrates over `hydration_tau_s`, so it diffuses
      |                   better once wet)
      | electrode membrane (per electrode: permselective / antifouling)
      | electrode surface  (aptamer / enzyme / ion-selective film)
    adhesive backing + electronics + battery

Units: lengths um, diffusivities um^2/s, concentrations nM (mM for glucose),
currents nA, time s.

IMPORTANT -- provenance of the numbers: every default below is an
*illustrative, literature-order-of-magnitude* value chosen so the twin behaves
plausibly, NOT a value fitted to a measured device (docs/calibration is still
empty and no physical bandage exists yet). The twin's job right now is
relative design comparison and pipeline testing; once real bench data
exists, replace these and check them against `analysis.characterize()`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, ClassVar, Dict, Optional, Tuple, Union

ANALYTES = ("pathogen_marker", "glucose", "pH")

# Diffusivity of each analyte relative to the layers' reference values
# (which are quoted for a small neutral solute): a protein-sized pathogen
# marker crawls, protons move fast.
ANALYTE_DIFFUSIVITY_SCALE: Dict[str, float] = {
    "pathogen_marker": 0.3,
    "glucose": 1.0,
    "pH": 3.0,
}


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


@dataclass(frozen=True)
class LayerSpec:
    name: str
    thickness_um: float
    diffusivity_dry_um2_s: float
    diffusivity_wet_um2_s: float
    # time for this layer to reach the wound's moisture level; None = the
    # layer's diffusivity doesn't depend on hydration (a membrane, say)
    hydration_tau_s: Optional[float] = None

    def __post_init__(self) -> None:
        _check(self.thickness_um > 0, f"layer {self.name!r}: thickness_um must be > 0")
        _check(self.diffusivity_dry_um2_s > 0, f"layer {self.name!r}: dry diffusivity must be > 0")
        _check(
            self.diffusivity_wet_um2_s >= self.diffusivity_dry_um2_s,
            f"layer {self.name!r}: wet diffusivity must be >= dry (wetting can't slow diffusion)",
        )
        _check(
            self.hydration_tau_s is None or self.hydration_tau_s > 0,
            f"layer {self.name!r}: hydration_tau_s must be > 0",
        )


@dataclass(frozen=True)
class AptamerSpec:
    """Electrochemical aptamer sensor (signal-on): occupancy theta follows
    d(theta)/dt = k_on*C*(1-theta) - k_off*theta, k_off = Kd*k_on, and the
    current is I0*(1 + signal_gain*theta)."""

    kind: ClassVar[str] = "aptamer"
    unit: ClassVar[str] = "nA"
    kd_nM: float = 100.0
    k_on_per_nM_s: float = 5e-4
    signal_gain: float = 0.9
    j0_nA_per_mm2: float = 120.0
    # SAM / probe loss: fraction of probes lost per day at 37 C
    desorption_per_day: float = 0.03
    fouling_signal_loss: float = 0.5

    def __post_init__(self) -> None:
        _check(self.kd_nM > 0 and self.k_on_per_nM_s > 0, "aptamer: Kd and k_on must be > 0")
        _check(self.signal_gain > 0, "aptamer: signal_gain must be > 0")
        _check(self.j0_nA_per_mm2 > 0, "aptamer: j0 must be > 0")
        _check(0 <= self.desorption_per_day < 1, "aptamer: desorption_per_day must be in [0, 1)")
        _check(0 <= self.fouling_signal_loss <= 1, "aptamer: fouling_signal_loss must be in [0, 1]")


@dataclass(frozen=True)
class EnzymaticSpec:
    """Amperometric oxidase sensor: Michaelis-Menten response with an
    enzyme that loses activity over wear time."""

    kind: ClassVar[str] = "enzymatic"
    unit: ClassVar[str] = "nA"
    j_max_nA_per_mm2: float = 200.0
    km_mM: float = 6.0
    reference_conc_mM: float = 4.0  # concentration that reads as 100 a.u. on a fresh sensor
    activity_loss_per_day: float = 0.04
    fouling_signal_loss: float = 0.4

    def __post_init__(self) -> None:
        _check(self.j_max_nA_per_mm2 > 0 and self.km_mM > 0, "enzymatic: j_max and Km must be > 0")
        _check(self.reference_conc_mM > 0, "enzymatic: reference_conc_mM must be > 0")
        _check(0 <= self.activity_loss_per_day < 1, "enzymatic: activity_loss_per_day must be in [0, 1)")
        _check(0 <= self.fouling_signal_loss <= 1, "enzymatic: fouling_signal_loss must be in [0, 1]")


@dataclass(frozen=True)
class PotentiometricSpec:
    """Ion-selective (pH) electrode: Nernstian slope, temperature-scaled,
    plus reference-electrode drift."""

    kind: ClassVar[str] = "potentiometric"
    unit: ClassVar[str] = "mV"
    nominal_mV: float = 250.0
    reference_ph: float = 7.4
    slope_mV_per_ph: float = 59.16  # at 25 C; scales with absolute temperature
    drift_mV_per_h: float = 0.05
    fouling_slope_loss: float = 0.25

    def __post_init__(self) -> None:
        _check(self.nominal_mV > 0 and self.slope_mV_per_ph > 0, "potentiometric: nominal and slope must be > 0")
        _check(0 <= self.fouling_slope_loss <= 1, "potentiometric: fouling_slope_loss must be in [0, 1]")


TransducerSpec = Union[AptamerSpec, EnzymaticSpec, PotentiometricSpec]
_TRANSDUCER_KINDS = {cls.kind: cls for cls in (AptamerSpec, EnzymaticSpec, PotentiometricSpec)}


@dataclass(frozen=True)
class ElectrodeSpec:
    channel_id: str
    sensor_type: str  # same free-form vocabulary as common/schemas/device.py:SensorChannel
    analyte: str
    transducer: TransducerSpec
    area_mm2: float = 1.0
    membrane: Optional[LayerSpec] = None  # electrode-specific layer on top of the shared stack
    antifouling: float = 0.0  # 0 (bare) .. 1 (fouling-proof)

    def __post_init__(self) -> None:
        _check(1 <= len(self.channel_id) <= 12, "channel_id must be 1-12 chars (Phase 7 wire packet field)")
        _check(self.analyte in ANALYTES, f"analyte must be one of {ANALYTES}")
        _check(self.area_mm2 > 0, "area_mm2 must be > 0")
        _check(0 <= self.antifouling <= 1, "antifouling must be in [0, 1]")
        expected = {"pathogen_marker": "aptamer", "glucose": "enzymatic", "pH": "potentiometric"}
        _check(
            self.transducer.kind == expected[self.analyte],
            f"analyte {self.analyte!r} needs a {expected[self.analyte]!r} transducer, got {self.transducer.kind!r}",
        )


@dataclass(frozen=True)
class ElectronicsSpec:
    """Analog front end + ADC. Input-referred noise is quoted in the native
    unit (nA for amperometric channels, mV for potentiometric); the twin
    normalizes each channel so its nominal reading is 100 a.u., so a larger
    electrode (bigger nominal current) has proportionally lower relative
    noise -- the reason area is a design lever."""

    noise_nA_rms: float = 0.8
    flicker_nA_rms: float = 0.5
    noise_mV_rms: float = 0.15
    flicker_mV_rms: float = 0.10
    flicker_tau_s: float = 120.0
    adc_bits: int = 12
    full_scale_au: float = 400.0
    gain_error_std: float = 0.01  # unit-to-unit, fixed per manufactured device
    offset_std_au: float = 0.5

    def __post_init__(self) -> None:
        _check(2 <= self.adc_bits <= 24, "adc_bits must be 2..24")
        _check(self.full_scale_au > 0 and self.flicker_tau_s > 0, "full_scale_au and flicker_tau_s must be > 0")
        for f in ("noise_nA_rms", "flicker_nA_rms", "noise_mV_rms", "flicker_mV_rms", "gain_error_std", "offset_std_au"):
            _check(getattr(self, f) >= 0, f"{f} must be >= 0")


@dataclass(frozen=True)
class PowerSpec:
    capacity_mAh: float = 50.0
    sleep_uA: float = 8.0
    measure_mA: float = 1.2
    measure_s: float = 4.0
    ble_tx_mA: float = 8.0
    ble_tx_s: float = 0.15
    ble_retry_gain: float = 1.5  # extra radio on-time at zero link quality

    def __post_init__(self) -> None:
        _check(self.capacity_mAh > 0, "capacity_mAh must be > 0")
        for f in ("sleep_uA", "measure_mA", "measure_s", "ble_tx_mA", "ble_tx_s", "ble_retry_gain"):
            _check(getattr(self, f) >= 0, f"{f} must be >= 0")


@dataclass(frozen=True)
class AdhesiveSpec:
    """How well the dressing stays put. `adhesion` scales how much a
    disturbance (a peeled edge, a knock) degrades electrode contact; contact
    then recovers as the dressing re-seals."""

    adhesion: float = 0.7  # 0 (falls off) .. 1 (unshiftable)
    contact_recovery_tau_s: float = 900.0

    def __post_init__(self) -> None:
        _check(0 <= self.adhesion <= 1, "adhesion must be in [0, 1]")
        _check(self.contact_recovery_tau_s > 0, "contact_recovery_tau_s must be > 0")


def _default_layers() -> Tuple[LayerSpec, ...]:
    return (
        LayerSpec("contact_hydrogel", 80.0, 30.0, 120.0, hydration_tau_s=600.0),
        LayerSpec("absorbent_pad", 200.0, 20.0, 150.0, hydration_tau_s=1800.0),
    )


def _default_electrodes() -> Tuple[ElectrodeSpec, ...]:
    membrane = LayerSpec("antifouling_membrane", 30.0, 40.0, 40.0)
    return (
        ElectrodeSpec("CH-01", "pathogen_channel_1", "pathogen_marker", AptamerSpec(), 1.0, membrane, 0.3),
        ElectrodeSpec("CH-02", "glucose", "glucose", EnzymaticSpec(), 1.0, membrane, 0.3),
        ElectrodeSpec("CH-03", "pH", "pH", PotentiometricSpec(), 1.0, None, 0.0),
    )


@dataclass(frozen=True)
class BandageDesign:
    name: str = "standard-3ch"
    layers: Tuple[LayerSpec, ...] = field(default_factory=_default_layers)
    electrodes: Tuple[ElectrodeSpec, ...] = field(default_factory=_default_electrodes)
    electronics: ElectronicsSpec = field(default_factory=ElectronicsSpec)
    power: PowerSpec = field(default_factory=PowerSpec)
    adhesive: AdhesiveSpec = field(default_factory=AdhesiveSpec)
    sample_interval_s: float = 30.0

    def __post_init__(self) -> None:
        _check(len(self.layers) >= 1, "a design needs at least one layer between wound and electrode")
        _check(len(self.electrodes) >= 1, "a design needs at least one electrode")
        ids = [e.channel_id for e in self.electrodes]
        _check(len(set(ids)) == len(ids), f"duplicate channel_id in {ids}")
        _check(self.sample_interval_s > 0, "sample_interval_s must be > 0")
        _check(
            self.power.measure_s + self.power.ble_tx_s < self.sample_interval_s,
            "measure_s + ble_tx_s must fit inside sample_interval_s (the device can't sample faster than it measures)",
        )

    def electrode(self, channel_id: str) -> ElectrodeSpec:
        for e in self.electrodes:
            if e.channel_id == channel_id:
                return e
        raise KeyError(f"no electrode {channel_id!r} in design {self.name!r}")

    def with_changes(self, **changes: Any) -> "BandageDesign":
        return replace(self, **changes)

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        for e_dict, e in zip(d["electrodes"], self.electrodes):
            e_dict["transducer"]["kind"] = e.transducer.kind
        return d

    def digest(self) -> str:
        """Stable content hash: two designs with the same numbers have the
        same digest, whatever the object identity."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def from_dict(d: dict) -> "BandageDesign":
        def layer(x: Optional[dict]) -> Optional[LayerSpec]:
            return None if x is None else LayerSpec(**x)

        def electrode(x: dict) -> ElectrodeSpec:
            t = dict(x["transducer"])
            transducer = _TRANSDUCER_KINDS[t.pop("kind")](**t)
            return ElectrodeSpec(
                channel_id=x["channel_id"],
                sensor_type=x["sensor_type"],
                analyte=x["analyte"],
                transducer=transducer,
                area_mm2=x["area_mm2"],
                membrane=layer(x.get("membrane")),
                antifouling=x["antifouling"],
            )

        return BandageDesign(
            name=d["name"],
            layers=tuple(layer(x) for x in d["layers"]),
            electrodes=tuple(electrode(x) for x in d["electrodes"]),
            electronics=ElectronicsSpec(**d["electronics"]),
            power=PowerSpec(**d["power"]),
            adhesive=AdhesiveSpec(**d["adhesive"]),
            sample_interval_s=d["sample_interval_s"],
        )


# -- presets ----------------------------------------------------------------
def standard_design() -> BandageDesign:
    """Pathogen aptamer + glucose + pH on a hydrogel/absorbent-pad stack."""
    return BandageDesign()


def low_power_design() -> BandageDesign:
    """Same electrodes, sampled every 5 min on a smaller cell -- the
    'wear it for a week' variant."""
    base = standard_design()
    return replace(
        base,
        name="low-power-3ch",
        sample_interval_s=300.0,
        power=replace(base.power, capacity_mAh=30.0),
    )


def fast_response_design() -> BandageDesign:
    """Thin stack, bigger pathogen electrode, faster sampling: quicker and
    quieter at the price of a thinner pad and a shorter battery life."""
    base = standard_design()
    thin = (
        LayerSpec("contact_hydrogel", 40.0, 30.0, 120.0, hydration_tau_s=300.0),
        LayerSpec("absorbent_pad", 100.0, 20.0, 150.0, hydration_tau_s=900.0),
    )
    electrodes = tuple(
        replace(e, area_mm2=2.0) if e.analyte == "pathogen_marker" else e for e in base.electrodes
    )
    return replace(base, name="fast-response-3ch", layers=thin, electrodes=electrodes, sample_interval_s=15.0)


PRESETS = {
    "standard": standard_design,
    "low_power": low_power_design,
    "fast_response": fast_response_design,
}
