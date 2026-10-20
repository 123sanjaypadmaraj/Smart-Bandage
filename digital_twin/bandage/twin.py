"""
BandageTwin: a digital twin of a specific bandage design on a simulated wound.

    wound bed --(analytes)--> layer stack --> transducer --> AFE/ADC --> RawMeasurement
        ^                                          ^            ^
     events                                   fouling/ageing   contact, link, battery

Everything is driven by `advance(dt_s)`; the twin never reads a wall clock.
`advance` is the engine's one entry point for time, and it guarantees:

  * dt-independence: time is split into sub-steps no longer than
    `max_substep_s`, and additionally split exactly at every scheduled event
    and effect expiry, so advancing 600 s in one call, in 600 calls, or in
    uneven chunks gives statistically the same trajectory (bit-identical in
    the deterministic parts) -- the caller's read cadence is not a modeling
    parameter.
  * reproducibility: all randomness comes from named `RngStreams`; the same
    (design, seed, events) gives the same run on any machine.
  * state that can move: `snapshot()` is a plain JSON-able dict holding
    everything (clock, physics, RNG streams, pending events, the design
    itself); `restore()`/`from_snapshot()`/`fork()` resume or branch from it
    exactly -- run "what if we'd given antibiotics at hour 30?" from the same
    instant without re-simulating the first 30 hours.

Unlike the sensor-only simulators, each frame carries *ground truth* (the true
wound state, the true local concentration at each electrode, the noise-free
reading) beside the measurement, so a detection or calibration algorithm can
be scored against what really happened.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.bandage.design import BandageDesign, standard_design
from digital_twin.bandage.electrode import Electrode
from digital_twin.bandage.electronics import AnalogFrontEnd, ContactProcess, LinkProcess, PowerModel
from digital_twin.bandage.kernel import Event, EventQueue, RngStreams, SimClock, ou_exact
from digital_twin.bandage.transport import LayerHydration
from digital_twin.bandage.wound_bed import Analytes, WoundBed, WoundBedParams
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError

DEFAULT_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
EVENT_KINDS = ("infection", "antibiotic", "debridement", "disturbance", "electrode_fault")

_CONTACT_LIFT = 0.15  # below this the electrode has lifted off the skin
_LINK_DROPOUT_BELOW = 0.3
_SNAPSHOT_VERSION = 1

Reading = Union[RawMeasurement, Exception]


@dataclass(frozen=True)
class ChannelTruth:
    local_concentration: float  # at the electrode, in the analyte's unit
    wound_concentration: float  # at the wound face
    ideal_au: float  # noise-free, unquantized reading
    sensor_health: float  # probe fraction / enzyme activity / reference integrity, 0..1
    fouling: float


@dataclass(frozen=True)
class Truth:
    bacterial_load: float
    inflammation: float
    moisture: float
    perfusion: float
    analytes: Analytes
    contact: float
    link_quality: float
    battery_pct: float
    channels: Dict[str, ChannelTruth]


@dataclass
class TwinFrame:
    t_s: float
    timestamp: datetime
    truth: Truth
    readings: Dict[str, Reading] = field(default_factory=dict)


class BandageTwin:
    def __init__(
        self,
        design: Optional[BandageDesign] = None,
        seed: Optional[int] = None,
        wound: Optional[WoundBedParams] = None,
        start_time: Optional[datetime] = None,
        max_substep_s: float = 10.0,
        device_id: str = "TWIN",
    ) -> None:
        if max_substep_s <= 0:
            raise ValueError("max_substep_s must be > 0")
        self.device_id = device_id
        self.design = design or standard_design()
        self.wound_params = wound or WoundBedParams()
        self.start_time = start_time or DEFAULT_EPOCH
        self.max_substep_s = max_substep_s

        self.streams = RngStreams(seed)
        self.clock = SimClock()
        self.events = EventQueue()
        self.wound = WoundBed(self.wound_params, self.streams.get("wound"))
        self.hydration = LayerHydration(self.design.layers, initial=self.wound.moisture)
        wound0 = self.wound.analytes()
        self.electrodes: Dict[str, Electrode] = {
            e.channel_id: Electrode(e, self.design.layers, wound0) for e in self.design.electrodes
        }
        self.afe = AnalogFrontEnd(self.design.electronics, self.design.electrodes, self.streams)
        self.power = PowerModel(self.design.power, self.design.sample_interval_s)
        self.link = LinkProcess(self.streams.get("link", "quality"))
        self.contact = ContactProcess(self.design.adhesive)
        self._thermistor_noise = 0.0
        # bench mode: {analyte: concentration} held at the wound face, replacing the wound bed
        self.bench: Optional[Dict[str, float]] = None
        self._read_this_frame: set = set()

    # ------------------------------------------------------------------ time
    @property
    def seed(self) -> int:
        return self.streams.seed

    @property
    def t_s(self) -> float:
        return self.clock.t_s

    @property
    def timestamp(self) -> datetime:
        return self.start_time + timedelta(seconds=self.clock.t_s)

    def schedule(self, at_s: float, kind: str, **params: float) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}; expected one of {EVENT_KINDS}")
        if at_s < self.clock.t_s:
            raise ValueError(f"cannot schedule an event in the past (t={self.clock.t_s}, at_s={at_s})")
        if kind == "electrode_fault" and params.get("channel_id") not in self.electrodes:
            raise ValueError(f"electrode_fault needs channel_id in {list(self.electrodes)}")
        self.events.schedule(Event(at_s=at_s, kind=kind, params=dict(params)))

    def advance(self, dt_s: float) -> None:
        if dt_s < 0:
            raise ValueError(f"dt_s must be >= 0, got {dt_s}")
        target = self.clock.t_s + dt_s
        self._dispatch_due()
        while self.clock.t_s < target - 1e-9:
            stop = target
            next_event = self.events.next_time()
            if next_event is not None and next_event < stop:
                stop = next_event
            boundary = self.wound.next_boundary_s(self.clock.t_s)
            if boundary is not None and boundary < stop:
                stop = boundary
            self._integrate(max(0.0, stop - self.clock.t_s))
            self._dispatch_due()
        if dt_s > 0:
            self._read_this_frame.clear()

    def _integrate(self, span_s: float) -> None:
        if span_s <= 1e-12:
            return
        n = max(1, math.ceil(span_s / self.max_substep_s - 1e-12))
        h = span_s / n
        for _ in range(n):
            self._substep(h)

    def _substep(self, h: float) -> None:
        t = self.clock.t_s
        self.wound.step(t, h)
        analytes = self._wound_analytes()
        self.hydration.step(h, analytes.moisture)
        self.link.step(h)
        self.contact.step(h)
        self.power.step(h, self.link.value)
        self.afe.step(h)
        self._thermistor_noise = ou_exact(
            self._thermistor_noise, 0.0, 30.0, 0.1, h, self.streams.get("thermistor")
        )
        for electrode in self.electrodes.values():
            electrode.step(h, analytes, self.wound.bacterial_load, self.hydration.level)
        self.clock.advance(h)

    def _dispatch_due(self) -> None:
        for ev in self.events.pop_due(self.clock.t_s):
            if self.wound.apply_event(ev.kind, ev.params, self.clock.t_s):
                continue
            if ev.kind == "disturbance":
                self.contact.disturb(float(ev.params.get("severity", 0.7)))
            elif ev.kind == "electrode_fault":
                self.electrodes[ev.params["channel_id"]].failed = True

    # --------------------------------------------------------------- observe
    def _wound_analytes(self) -> Analytes:
        a = self.wound.analytes()
        if self.bench is None:
            return a
        return Analytes(
            pathogen_marker=self.bench.get("pathogen_marker", a.pathogen_marker),
            glucose=self.bench.get("glucose", a.glucose),
            pH=self.bench.get("pH", a.pH),
            temperature_C=37.0,
            moisture=1.0,  # a standard solution: fully wet
        )

    def _ideal_au(self, electrode: Electrode, temp_c: float) -> float:
        native = electrode.native_reading(temp_c)
        return self.afe.to_au(native, electrode.transducer.nominal(), electrode.unit, self.contact.value)

    def truth(self) -> Truth:
        a = self._wound_analytes()
        channels = {
            cid: ChannelTruth(
                local_concentration=e.local_concentration,
                wound_concentration=a.get(e.spec.analyte),
                ideal_au=self._ideal_au(e, a.temperature_C),
                sensor_health=e.transducer.health,
                fouling=e.fouling,
            )
            for cid, e in self.electrodes.items()
        }
        return Truth(
            bacterial_load=self.wound.bacterial_load,
            inflammation=self.wound.inflammation,
            moisture=self.wound.moisture,
            perfusion=self.wound.perfusion,
            analytes=a,
            contact=self.contact.value,
            link_quality=self.link.value,
            battery_pct=self.power.percent,
            channels=channels,
        )

    def _check_available(self, cid: str) -> None:
        if self.power.depleted:
            raise SensorDisconnectedError("battery depleted")
        if self.electrodes[cid].failed:
            raise SensorDisconnectedError(f"electrode {cid} delaminated")
        if self.contact.value < _CONTACT_LIFT:
            raise SensorDisconnectedError("electrode contact lost (dressing lifted)")
        if self.link.value < _LINK_DROPOUT_BELOW:
            p = 0.8 * (_LINK_DROPOUT_BELOW - self.link.value) / _LINK_DROPOUT_BELOW
            if self.streams.get("link", "dropout").random() < p:
                raise CommsTimeoutError("BLE packet lost (poor link)")

    def read_channel(self, channel_id: str, ideal: bool = False) -> RawMeasurement:
        """One measurement at the current instant. `ideal=True` skips faults,
        noise and quantization (bench characterization)."""
        if channel_id not in self.electrodes:
            raise KeyError(f"unknown channel_id {channel_id!r} for design {self.design.name!r}")
        electrode = self.electrodes[channel_id]
        temp = self._wound_analytes().temperature_C
        if not ideal:
            self._check_available(channel_id)
        ideal_au = self._ideal_au(electrode, temp)
        if ideal:
            value = ideal_au
        else:
            value = self.afe.sample(
                channel_id, ideal_au, electrode.transducer.nominal(), electrode.unit, self.contact.value
            )
        skin_temp = temp - 1.0 + self._thermistor_noise
        return RawMeasurement(
            device_id=self.device_id,
            channel_id=channel_id,
            timestamp=self.timestamp,
            raw_signal=round(value, 4),
            temperature=round(skin_temp, 2),
            battery=max(0, min(100, round(self.power.percent))),
        )

    def sample(self) -> TwinFrame:
        """Read every channel now. A channel whose fault fires contributes its
        exception instead of a reading and the others carry on -- the same
        per-channel isolation as MultiChannelSensor.read_all()."""
        readings: Dict[str, Reading] = {}
        for cid in self.electrodes:
            try:
                readings[cid] = self.read_channel(cid)
            except (SensorDisconnectedError, CommsTimeoutError) as exc:
                readings[cid] = exc
        return TwinFrame(t_s=self.clock.t_s, timestamp=self.timestamp, truth=self.truth(), readings=readings)

    def step(self, dt_s: Optional[float] = None) -> TwinFrame:
        """advance() then sample(). Default step is the design's sample interval."""
        self.advance(self.design.sample_interval_s if dt_s is None else dt_s)
        return self.sample()

    def run(self, duration_s: float, dt_s: Optional[float] = None) -> List[TwinFrame]:
        dt = self.design.sample_interval_s if dt_s is None else dt_s
        if dt <= 0:
            raise ValueError("dt_s must be > 0")
        return [self.step(dt) for _ in range(int(duration_s // dt))]

    def status(self, channel_id: str) -> DeviceStatus:
        e = self.electrodes[channel_id]
        quality = max(0.0, min(1.0, self.contact.value * (1.0 - 0.9 * e.fouling)))
        error = None
        if self.power.depleted:
            error = "battery depleted"
        elif e.failed:
            error = f"electrode {channel_id} delaminated"
        return DeviceStatus(
            connected=not (self.power.depleted or e.failed or self.contact.value < _CONTACT_LIFT),
            battery=max(0, min(100, round(self.power.percent))),
            signal_quality=round(quality, 4),
            last_error=error,
        )

    # ------------------------------------------------- read-cycle bookkeeping
    def read_in_cycle(self, channel_id: str) -> RawMeasurement:
        """For callers that poll one channel at a time (SensorInterface): the
        twin advances one sample interval when a channel is asked for a second
        time, so N channels read once each share the same instant."""
        if channel_id in self._read_this_frame:
            self.advance(self.design.sample_interval_s)
        self._read_this_frame.add(channel_id)
        return self.read_channel(channel_id)

    # --------------------------------------------------------------- bench rig
    def hold_bench(self, **analytes: float) -> None:
        """Bench test rig: hold the electrodes in a standard solution with the
        given wound-face concentrations (e.g. pathogen_marker=200.0),
        replacing the wound bed's own values. The layers are soaked through
        immediately (it's a solution, not a wound)."""
        unknown = set(analytes) - {"pathogen_marker", "glucose", "pH"}
        if unknown:
            raise ValueError(f"unknown analytes {sorted(unknown)}")
        self.bench = dict(analytes)
        self.hydration.level = [1.0] * len(self.hydration.level)

    def release_bench(self) -> None:
        self.bench = None

    # ---------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        return {
            "version": _SNAPSHOT_VERSION,
            "design": self.design.to_dict(),
            "wound_params": self.wound_params.__dict__.copy(),
            "start_time": self.start_time.isoformat(),
            "max_substep_s": self.max_substep_s,
            "device_id": self.device_id,
            "seed": self.streams.seed,
            "t_s": self.clock.t_s,
            "rng": self.streams.get_state(),
            "events": self.events.get_state(),
            "wound": self.wound.get_state(),
            "hydration": self.hydration.get_state(),
            "electrodes": {cid: e.get_state() for cid, e in self.electrodes.items()},
            "afe": self.afe.get_state(),
            "power": self.power.get_state(),
            "link": self.link.get_state(),
            "contact": self.contact.get_state(),
            "thermistor_noise": self._thermistor_noise,
            "bench": None if self.bench is None else dict(self.bench),
            "read_this_frame": sorted(self._read_this_frame),
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot())

    @classmethod
    def from_snapshot(cls, snap: dict) -> "BandageTwin":
        if snap.get("version") != _SNAPSHOT_VERSION:
            raise ValueError(f"unsupported snapshot version {snap.get('version')!r}")
        twin = cls(
            design=BandageDesign.from_dict(snap["design"]),
            seed=snap["seed"],
            wound=WoundBedParams(**snap["wound_params"]),
            start_time=datetime.fromisoformat(snap["start_time"]),
            max_substep_s=snap["max_substep_s"],
            device_id=snap["device_id"],
        )
        twin.restore(snap)
        return twin

    def restore(self, snap: dict) -> None:
        """Load a snapshot taken from a twin of the *same design*."""
        if BandageDesign.from_dict(snap["design"]).digest() != self.design.digest():
            raise ValueError("snapshot was taken from a different bandage design")
        self.streams.seed = snap["seed"]
        self.streams.set_state(snap["rng"])
        self.clock.t_s = snap["t_s"]
        self.events.set_state(snap["events"])
        self.wound.set_state(snap["wound"])
        self.hydration.set_state(snap["hydration"])
        for cid, state in snap["electrodes"].items():
            self.electrodes[cid].set_state(state)
        self.afe.set_state(snap["afe"])
        self.power.set_state(snap["power"])
        self.link.set_state(snap["link"])
        self.contact.set_state(snap["contact"])
        self._thermistor_noise = snap["thermistor_noise"]
        self.bench = None if snap["bench"] is None else dict(snap["bench"])
        self._read_this_frame = set(snap["read_this_frame"])

    def fork(self) -> "BandageTwin":
        """An independent twin continuing from exactly this instant."""
        return BandageTwin.from_snapshot(copy.deepcopy(self.snapshot()))
