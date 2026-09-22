"""
Digital twin of the smart bandage *design* (see docs/architecture/bandage_twin.md).

Additive to the DT-1..DT-6 modules in the parent package, which are untouched:
this subpackage has its own kernel (seeded streams, simulated clock, event
queue, snapshots) and its own wound bed, so nothing here depends on their
global-`random` processes or wall-clock reads.
"""
from digital_twin.bandage.adapter import BandageTwinSensor, sensors_for
from digital_twin.bandage.analysis import characterize, compare, format_table
from digital_twin.bandage.design import (
    PRESETS,
    AdhesiveSpec,
    AptamerSpec,
    BandageDesign,
    ElectrodeSpec,
    ElectronicsSpec,
    EnzymaticSpec,
    LayerSpec,
    PotentiometricSpec,
    PowerSpec,
    fast_response_design,
    low_power_design,
    standard_design,
)
from digital_twin.bandage.eis_adapter import EisReading, eis_from_twin
from digital_twin.bandage.impedance import (
    BindingModel,
    Calibration,
    EisAssay,
    EisParams,
    EisResult,
    binding_kinetics,
    coverage,
    fit_randles,
    impedance,
    log_sweep,
    params_at_coverage,
    spectrum,
)
from digital_twin.bandage.kernel import Event, RngStreams, SimClock
from digital_twin.bandage.twin import BandageTwin, ChannelTruth, Truth, TwinFrame
from digital_twin.bandage.wound_bed import Analytes, WoundBedParams

__all__ = [
    "AdhesiveSpec", "Analytes", "AptamerSpec", "BandageDesign", "BandageTwin", "BandageTwinSensor",
    "ChannelTruth", "ElectrodeSpec", "ElectronicsSpec", "EnzymaticSpec", "Event", "LayerSpec",
    "PRESETS", "PotentiometricSpec", "PowerSpec", "RngStreams", "SimClock", "Truth", "TwinFrame",
    "WoundBedParams", "characterize", "compare", "fast_response_design", "format_table",
    "low_power_design", "sensors_for", "standard_design",
    "BindingModel", "Calibration", "EisAssay", "EisParams", "EisReading", "EisResult",
    "binding_kinetics", "coverage", "eis_from_twin", "fit_randles", "impedance", "log_sweep",
    "params_at_coverage", "spectrum",
]
