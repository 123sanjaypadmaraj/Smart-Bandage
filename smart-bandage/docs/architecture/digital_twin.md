# Digital twin — architecture notes

Companion to `docs/architecture/overview.md` (see its status-table rows for
each `digital_twin/` module's one-line status). This file is where the
digital twin's own design and testing strategy live, the way
`data_dictionary.md` is the companion doc for `common/schemas/`.

## Why a digital twin, and why it's separate from the simulator

Every scenario in `simulator/scenarios/scenarios.py` is a script: a fixed
checkpoint array that `simulator/sensors/scenario_sensor.py` eases its
displayed signal toward, indexed by read count. That's enough to replay a
*shape*, but there's nothing underneath the checkpoints that responds to
time the way a real wound does — two runs at different read rates play back
identical checkpoint sequences instead of covering different amounts of
physiological time, and nothing on the checkpoint path can answer "what
happens if a device reads twice as often" or "what does this wound do if
nobody polls it for an hour," and nothing ties one channel's signal to
another's the way real electrodes on the same wound bed would move
together.

`digital_twin/` is that underneath: one shared hidden state per device,
advanced a tick at a time, read (never scripted) by every channel. It
doesn't replace the simulator/scenario path — `simulator/scenarios.py` and
its tests stay exactly as they are, pinned; the twin is an opt-in
alternative wherever a `SensorInterface` or a `MultiChannelSensor` is
built, and hardware stays out of scope until it physically exists (see
`docs/architecture/overview.md`'s invariants).

## How the pieces fit together, after consolidation

The seven DT tickets were built across several concurrent worktrees and
didn't all converge on the same state model the first time — three
different "what drives a reading" implementations existed at once for a
while (DT-1's own state vector, DT-4's own state vector, and DT-3's engine
not using either one, just replaying the old scripted scenarios on a
seeded clock instead). This section describes where that landed, not the
path that got there — see git history (in particular the merge commit
that folds DT-3/DT-4/DT-6 in and retires DT-1) for the blow-by-blow.

```
WoundState (state.py)  --step()-->  shared, mean-reverting hidden state
        |
        |  read by
        v
ChannelProfile transfer function (observation.py) --> RawMeasurement
        ^
        |  advances alongside WoundState, coupled to it
        |
device_physics.py: battery / electrode fouling / BLE link quality
```

- **`digital_twin/state.py` — `WoundState`.** The one physiological state
  model. Four latent variables — `inflammation`, `bacterial_load`,
  `moisture`, `perfusion` — each a bounded, mean-reverting random walk
  (an exact Ornstein-Uhlenbeck transition, not an Euler step) toward its
  own `*_target` field. Retargeting a field (`state.bacterial_load_target
  = 0.9`) is *the* way anything scripts this state forward — nothing else
  ever sets a value field directly.
- **`digital_twin/device_physics.py`.** Battery drain, electrode fouling,
  and BLE link quality as the same kind of continuous, state-coupled
  process (a poor link burns extra battery; a wetter, more colonized wound
  fouls its electrode faster) instead of a fixed schedule.
- **`digital_twin/observation.py` — `DigitalTwinDevice`.** One instance
  per simulated device: owns the shared `WoundState`, the battery/link
  processes, and one `ElectrodeFoulingProcess` + `ChannelProfile` per
  channel. `ChannelProfile` is the per-channel half of the transfer
  function — how many `raw_signal` units a channel moves per unit of each
  `WoundState` variable — so one rise in `inflammation`/`bacterial_load`
  nudges every sensitive channel at once, correlated, the cross-channel
  coupling a scripted per-channel checkpoint array can't produce.
  `true_signal(channel_id)` is the clean, no-fouling-no-noise ground truth
  for whoever needs to score the real (noisy, fouled) reading against it —
  deliberately not part of any `RawMeasurement`, so it can never leak onto
  a real device's contract.
- **`digital_twin/perturbations.py` + `digital_twin/profiles.py` —
  `TwinProfile`.** A named starting `WoundState` plus an ordered list of
  `Perturbation`s (`InfectionOnset`, `DressingDisturbance`) applied once
  per simulated tick, each retargeting a `WoundState` field the same way a
  hand-written script would. Six profiles are registered: three static
  risk-level baselines (`healthy_baseline`, `diabetic_slow_healing`,
  `immunocompromised_high_risk`) and three scripted-onset scenarios
  (`normal_healing`, `complicated_infection`, `chronic_wound`).
  `profiles.simulate()` drives a real `DigitalTwinDevice` through a
  profile for a given duration, yielding real `(WoundState, RawMeasurement)`
  pairs — a backtest of a named clinical story, not a standalone summary.
- **`digital_twin/engine.py` — `DigitalTwinEngine`.** A single-channel
  `DigitalTwinDevice`, ticked on a counter instead of `time.monotonic()`,
  seeded end to end (every random draw in `state.py`/`device_physics.py`/
  `simulator/faults/faults.py`'s `ProbabilisticDropout` goes through one
  `random.Random` the engine owns) so the same seed reproduces a run
  bit-for-bit, including `RawMeasurement.timestamp` (laid out from a fixed
  epoch rather than `datetime.now()`). `scenario=` names a
  `digital_twin.profiles` profile. `engine.run(n)` bulk-generates a day or
  more of trajectory in one call with no sleeping.
- **`digital_twin/adapter.py` — `DigitalTwinSensor`.** Wraps a
  `DigitalTwinEngine` behind `SensorInterface`, so
  `simulator/sensors/multi_channel_sensor.py:MultiChannelSensor` can opt a
  device into `engine="digital_twin"` per channel without anything
  downstream (backend, processing) knowing the difference.
- **`backend/app/simulation.py`.** `DeviceSimulation`'s twin-backed mode
  seeds a `DigitalTwinDevice` directly (not through the
  `SensorInterface`/`MultiChannelSensor` path — a live simulated device
  already owns its own tick loop) from `digital_twin.profiles`' registry,
  applies that profile's perturbations every tick the same way
  `engine.py`/`profiles.simulate()` do, and records a
  `TwinGroundTruthSnapshot` via `DigitalTwinDevice.true_signal()` for the
  dev-only ground-truth overlay (`GET /simulation/twin/{device_id}`,
  gated off in production).

One consequence worth being explicit about: `digital_twin/engine.py` and
`backend/app/simulation.py` are two independent callers of the same
`DigitalTwinDevice`/`digital_twin.profiles` machinery, not one built on
the other — a profile behaves the same (same perturbation schedule, same
transfer functions) whether it's driving a live WS-streamed simulation or
a bulk-generated CI trajectory, but each owns its own tick loop and its
own elapsed-time bookkeeping. If that duplication starts drifting (e.g.
one adds a feature to how perturbations are timed and the other doesn't),
that's the seam to fold into one shared tick loop.

## What DT-1 was, and why it isn't here

DT-1 proved the core idea — a hidden state, Euler-integrated one tick at a
time, feeding an observable through a transfer function — on a single
manufactured example: `bacterial_load` under logistic growth,
`infection_biomarker()` reading it out. It was built in its own worktree
concurrently with DT-2, which independently built the real
`WoundState`/`DigitalTwinDevice` model above and got wired into
`observation.py`, `device_physics.py`, and the backend; DT-1's own
`DeviceState`/`BacterialLoadParams` never was. Once DT-3/DT-4/DT-6 all
landed built against DT-2's `WoundState`, keeping DT-1's model around as a
second, unwired "hidden state" alongside the real one was pure duplication
— confusing to anyone reading `digital_twin/` for the first time, and
nothing exercised it except its own tests. It was retired outright (not
renamed-and-kept) in the consolidation commit; its logistic-growth
approach lives on only as an idea DT-2's OU mean-reversion deliberately
does differently (mean reversion needs no separate "recovery" dynamics —
retargeting `bacterial_load_target` down models an immune response
clearing an infection the same mechanism that models one starting).

## Testing strategy (DT-7)

Three layers, same shape DT-7 established, re-pointed at the consolidated
model:

**Unit tests on hand-picked examples** (`tests/test_digital_twin_dt2.py`,
`tests/test_digital_twin_dt3.py`, `tests/test_digital_twin_profiles.py`)
prove the mechanics: cross-channel coupling, fouling/battery/link-quality
coupling to `WoundState`, seeded determinism (same seed → identical
trajectory including timestamps; different seeds diverge), a profile's
perturbations visibly moving the trajectory, `MultiChannelSensor`'s opt-in
staying opt-in.

**Property-based tests (Hypothesis)**, in
`tests/test_digital_twin_dt7_properties.py`, search the input space
instead of a fixed set of examples and assert invariants that have to hold
for *any* input:

- every `WoundState` field stays inside its documented bounds after any
  sequence of `step()` calls, for any `*_target` and any `dt_seconds`
  sequence — including a target already pinned at a field's ceiling
- `step(dt_seconds<=0)` is a no-op
- `DigitalTwinEngine` reproduces any registered profile's trajectory
  bit-for-bit given the same seed, and diverges given a different one

When Hypothesis finds a counterexample it shrinks it down to the smallest
input that still fails, rather than handing back the original (often much
larger) random draw.

**A seeded regression trajectory**
(`test_seeded_engine_trajectory_matches_pinned_statistics`) guards against
what a property test can't: a rearranged transfer function that still
satisfies every bound above but produces a quietly different curve. It
runs one fixed `DigitalTwinEngine` (a hardcoded seed and profile) and
asserts its `raw_signal` summary statistics (first, last, max, mean)
against hardcoded expected values. A deliberate change to the dynamics
needs a deliberate update to those numbers (rerun the same engine
config, copy in the new values, say why in the commit); an accidental
change fails it.

### Hypothesis profiles

`conftest.py` registers two settings profiles and picks one via the
`HYPOTHESIS_PROFILE` env var:

- `default` (unset locally) — 50 examples per property test, keeps a bare
  `pytest` fast for everyday iteration.
- `ci` — 500 examples, no per-example deadline (so a slower/shared CI
  runner can't turn a correct property into a flaky failure).

`scripts/run_checks.sh` and `.github/workflows/ci.yml` both export
`HYPOTHESIS_PROFILE=ci` and pass `--hypothesis-show-statistics`, so every
CI run and every local pre-push check gets the deeper search, and its
per-test example counts show up in the log instead of only surfacing on
failure.

## What's next (DT-5)

`digital_twin.profiles.simulate()` and `DigitalTwinDevice.true_signal()`
are the two building blocks DT-5's ground-truth/validation harness needs —
a full pipeline run over a long twin trajectory, scored against known
truth, extending the existing `ml/evaluation/scenario_backtest.py`
backtest pattern. Not built yet as its own module; `backend/app/
simulation.py`'s `TwinGroundTruthSnapshot`/`_record_ground_truth` is the
live, single-snapshot-at-a-time version of the same idea.
