"""
DT-3: the digital twin engine -- a tick loop decoupled from wall-clock time.

ScenarioSensor (simulator/sensors/scenario_sensor.py) paces every reading
off `time.monotonic() - self._t0`, which is exactly right for a live demo
(one reading per real second) but wrong for two things that don't want to
wait on a wall clock at all:

  - CI: a test asserting on "24 hours of electrode fouling" shouldn't spend
    24 hours -- or even 24 seconds of sleeping -- producing it.
  - Reproducibility: an unseeded run draws from the global `random` module,
    so no two runs (even at the same wall-clock pace) trace the same path.
    A regression test that wants to pin an exact trajectory has nothing to
    pin to.

Consolidation note: this originally reused simulator/scenarios.py's 7
scripted scenario configs on a tick counter -- reproducible, but not
actually twin-driven (no WoundState, no cross-channel coupling, no device
physics). It's rebuilt here on top of a single-channel
digital_twin/observation.py:DigitalTwinDevice instead, so `scenario=` now
names a digital_twin/profiles.py clinical profile (e.g.
`"healthy_baseline"`, `"complicated_infection"`) and every reading is the
real state -> RawMeasurement transfer function DT-2 built, not a checkpoint
replay. `DigitalTwinDevice(seed=...)` is what makes this reproducible
(digital_twin/state.py, digital_twin/device_physics.py, and
simulator/faults/faults.py:ProbabilisticDropout all accept the resulting
`random.Random` instead of drawing from the global `random` module).

Not a SensorInterface implementation itself -- SensorInterface speaks one
read per call, paced by whatever's calling read_measurement(). See
digital_twin/adapter.py for the thin wrapper that lets an engine stand in
for a ScenarioSensor behind that contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Union

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.observation import DigitalTwinDevice
from digital_twin.profiles import get_profile
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError

_DEFAULT_SCENARIO = "healthy_baseline"

# Fixed epoch a run's timestamps are laid out from, so two runs with the
# same seed produce identical RawMeasurement.timestamp values too, not just
# identical signal values -- see DigitalTwinDevice's `start_time` param.
_DEFAULT_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


class DigitalTwinEngine:
    """A single-channel `DigitalTwinDevice`, ticked on a counter instead of
    `time.monotonic()`, with every random draw seeded.

    One tick == one `dt_seconds` slice of simulated time. Whether those
    ticks are paced by a wall clock (a demo calling `.tick()` once a
    second) or fired back-to-back in a loop (`.run()` generating a day of
    trajectory in one call), the engine itself never sleeps or reads the
    clock, so both look identical to it.
    """

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        scenario: str = _DEFAULT_SCENARIO,
        sensor_type: Optional[str] = None,
        dt_seconds: float = 1.0,
        seed: Optional[int] = None,
        battery_start_pct: float = 100.0,
        battery_drain_pct_per_hour: float = 2.0,
    ) -> None:
        if dt_seconds <= 0:
            raise ValueError("dt_seconds must be > 0")
        self.device_id = device_id
        self.channel_id = channel_id
        self.dt_seconds = dt_seconds
        self.battery_start_pct = battery_start_pct
        self.battery_drain_pct_per_hour = battery_drain_pct_per_hour
        # digital_twin/profiles.py profiles are keyed to a sensor_type they
        # were designed against (see TwinProfile.sensor_type); a caller can
        # still override it explicitly for a channel meant to read
        # differently from its profile's default.
        self._scenario_name = scenario
        self._profile = get_profile(scenario)
        self.sensor_type = sensor_type or self._profile.sensor_type
        self.seed = seed
        self._tick_count = 0
        self._running = False
        self._device = self._build_device()

    def _build_device(self) -> DigitalTwinDevice:
        return DigitalTwinDevice(
            self.device_id,
            channels={self.channel_id: self.sensor_type},
            state=self._profile.initial_state(),
            battery_start_pct=self.battery_start_pct,
            battery_drain_pct_per_hour=self.battery_drain_pct_per_hour,
            seed=self.seed,
            start_time=_DEFAULT_EPOCH,
        )

    @property
    def scenario_name(self) -> str:
        return self._scenario_name

    @property
    def elapsed_seconds(self) -> float:
        return self._tick_count * self.dt_seconds

    def set_scenario(self, scenario: str) -> None:
        """Switch to a different digital_twin/profiles.py profile without
        losing the running session's seed -- the tick counter and device
        state reset to that profile's own starting point, exactly like
        ScenarioSensor.set_scenario()."""
        self._scenario_name = scenario
        self._profile = get_profile(scenario)
        self.sensor_type = self._profile.sensor_type
        self._tick_count = 0
        self._device = self._build_device()
        if self._running:
            self._device.start_measurement()

    def reset(self, seed: Optional[int] = None) -> None:
        """Rewind to tick 0 and reseed. The same seed reproduces the exact
        same run again; a different one (or None, for a fresh
        non-reproducible run) starts a new trajectory."""
        self.seed = seed
        self._tick_count = 0
        self._device = self._build_device()
        if self._running:
            self._device.start_measurement()

    def start(self) -> None:
        self._running = True
        self._device.start_measurement()

    def stop(self) -> None:
        self._running = False
        self._device.stop_measurement()

    def tick(self) -> RawMeasurement:
        """Advance one `dt_seconds` slice and return its reading. Raises
        SensorDisconnectedError / CommsTimeoutError on the same conditions
        DigitalTwinDevice.read_channel() would -- link quality, not a
        scripted read index, decides when.

        Applies this scenario's profile perturbations (if any) first, at
        the elapsed simulated day this tick starts on -- the same thing
        digital_twin/profiles.py:simulate() and backend/app/simulation.py
        do for a live/backtested run of the same profile, so all three
        agree on when e.g. an InfectionOnset actually fires.
        """
        if not self._running:
            raise RuntimeError("call start() before tick()")
        elapsed_days = self.elapsed_seconds / 86400.0
        for perturbation in self._profile.perturbations:
            perturbation.apply(self._device.state, elapsed_days)
        try:
            reading = self._device.read_channel(self.channel_id, dt_seconds=self.dt_seconds)
        finally:
            self._tick_count += 1
        return reading

    def run(
        self, n_ticks: int, stop_on_fault: bool = False
    ) -> List[Union[RawMeasurement, Exception]]:
        """Bulk-generate `n_ticks` of trajectory in one call -- the "hours
        or days of trajectory in seconds" path `.tick()` alone doesn't give
        you. Starts the engine if it isn't already running.

        A tick whose link check raises contributes its exception instead of
        a reading (matching MultiChannelSensor.read_all()'s per-channel
        isolation) and the run continues, unless `stop_on_fault` says to
        stop there instead -- useful for "generate until it disconnects".
        """
        if not self._running:
            self.start()
        results: List[Union[RawMeasurement, Exception]] = []
        for _ in range(n_ticks):
            try:
                results.append(self.tick())
            except (SensorDisconnectedError, CommsTimeoutError) as exc:
                results.append(exc)
                if stop_on_fault:
                    break
        return results

    def get_status(self) -> DeviceStatus:
        return self._device.get_status(self.channel_id)

    @property
    def true_signal(self) -> float:
        """Ground truth for this engine's one channel -- see
        DigitalTwinDevice.true_signal()."""
        return self._device.true_signal(self.channel_id)
