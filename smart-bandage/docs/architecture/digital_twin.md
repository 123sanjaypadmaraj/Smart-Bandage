# Digital twin — architecture notes

Companion to `docs/architecture/overview.md` (see its "What's real vs.
scaffolded" table for `digital_twin/state.py`'s one-line status). This file
is where the digital twin's own design and testing strategy live, the way
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
nobody polls it for an hour."

`digital_twin/` is that underneath: a small hidden state vector,
Euler-integrated forward one tick at a time instead of read off a
pre-authored array. It doesn't replace the simulator/scenario path — DT-1
through DT-7 build and test it standalone; wiring it into
`simulator`/`processing` so a live device can actually run on it is a later
ticket (see `digital_twin/__init__.py`).

## The state vector (DT-1)

`digital_twin/state.py`'s `DeviceState` — four fields, each `0..1`:

| Field | Meaning | Dynamics as of DT-1 |
|---|---|---|
| `bacterial_load` | fraction of local carrying capacity | logistic growth (real) |
| `inflammation` | — | zero derivative (placeholder) |
| `healing_stage_progress` | — | zero derivative (placeholder) |
| `moisture` | — | zero derivative (placeholder) |

`DeviceState` is a frozen dataclass; `DigitalTwinState.tick(dt_seconds)`
replaces its held instance each call rather than mutating fields in place,
so a snapshot handed to a caller (logging, a test assertion) can't change
out from under them once a later tick advances the twin.

`bacterial_load` is the one channel proven end-to-end: `dB/dt = growth_rate
* B * (1 - B / carrying_capacity)` (the standard bounded-population logistic
model — slow to start, fastest at `B == carrying_capacity / 2`, saturating
at `carrying_capacity` instead of diverging), stepped with a first-order
forward Euler update `x[t+dt] = x[t] + dt * dx/dt(x[t])`, then
`infection_biomarker()` turns that hidden load into the observable
biomarker on the same `0..100` scale the rest of the simulator's channels
use. The other three fields hold their initial value (`_zero_derivative`)
so the vector's *shape* is final now — later DT tickets wire real dynamics
onto them without changing what a caller of `DeviceState` already depends
on.

Euler integration is only first-order accurate: `tick()` rejects a negative
`dt_seconds` outright, and clamps `bacterial_load` back into
`[0, carrying_capacity]` after each step as a defensive measure — continuous-time
logistic growth never crosses those bounds (the derivative vanishes at
both), but a large enough `dt_seconds` against a fast `growth_rate` can make
a single Euler step overshoot past them before the clamp catches it.

## Testing strategy (DT-7)

`tests/test_digital_twin_dt1.py` proves the mechanics on hand-picked
examples: state persists and accumulates across ticks instead of being
indexed by read count, and the same channel evolved over a finer `dt`
tracks closer to the analytic logistic curve. Hand-picked examples can't
tell us those bounds *always* hold, or catch a quiet change to the dynamics
that still happens to satisfy every bound. DT-7 adds two more layers on top,
in `tests/test_digital_twin_dt7_properties.py`:

**Property-based tests (Hypothesis)** search the input space instead of a
fixed set of examples — random `growth_rate`/`carrying_capacity`/initial
load/`dt` sequences — and assert the invariants that have to hold for
*any* input, not just the ones in `test_digital_twin_dt1.py`:

- `bacterial_load` stays inside `[0, carrying_capacity]` after every tick
- `bacterial_load` is monotonically non-decreasing (growth_rate >= 0, so
  the derivative is never negative in-range)
- `tick(dt_seconds=0.0)` is a no-op
- `tick()` rejects any negative `dt_seconds`
- the three not-yet-dynamic channels hold their initial value — and, since
  that value stays put, never leave `[0, 1]` — under any dt sequence
- `infection_biomarker()` stays inside `[0, 100]` for any bacterial_load,
  midpoint, and steepness

When Hypothesis finds a counterexample it shrinks it down to the smallest
input that still fails, rather than handing back the original (often much
larger) random draw.

**A seeded regression trajectory** guards against what a property test
can't: a rearranged formula that still satisfies every bound above but
produces a quietly different curve. `test_seeded_trajectory_matches_pinned_statistics`
runs one fixed, reproducible trajectory — stdlib `random.Random` with a
hardcoded seed, not Hypothesis's own example generator, which makes no
promise that a given seed reproduces the same examples across library
versions — and asserts its summary statistics (first/mid/final
`bacterial_load`, the mean and max across the run, and the same over
`infection_biomarker`) against hardcoded expected values. A deliberate
change to the dynamics needs a deliberate update to those numbers (rerun
the trajectory, copy in the new values, say why in the commit); an
accidental change fails it.

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
