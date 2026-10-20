# Bandage digital twin

`digital_twin/bandage/` is a digital twin of the **bandage design itself** (the
physical device) sitting on a simulated wound, plus a redesigned simulation
engine underneath it. The earlier DT-1..DT-6 modules model the *patient*; nothing
described what is between the wound and the electrode. This does.

It is **additive**: none of the existing `digital_twin/*.py` modules or their
tests were changed. The backend runs it as a third `/simulation` engine beside
scenario playback and DT-6's `DigitalTwinDevice` (see "Running it live" below)
without changing either of those.

## What is modeled

```
wound bed ──analytes──▶ layer stack ──▶ transducer ──▶ AFE / ADC ──▶ RawMeasurement
   ▲                       ▲                 ▲              ▲
 events              hydration,          binding,        contact, link,
 (infection,         fouling             probe/enzyme    battery
 antibiotic …)                           ageing
```

| Piece | File | Physics |
|---|---|---|
| Design spec | `design.py` | Frozen, validated, JSON round-trippable, content-hashed (`digest()`). Layers, electrodes + transducers, electronics, power, adhesive, sample interval. Presets: `standard`, `low_power`, `fast_response`. |
| Wound bed | `wound_bed.py` | Bacterial load ⇄ inflammation as a coupled ODE (RK4); moisture/perfusion by exact OU relaxation; timed virulence/antibiotic effects. Emits analyte concentrations (marker nM, glucose mM, pH, temperature). |
| Transport | `transport.py` | 1-D layered diffusion, finite volume + backward Euler (unconditionally stable). Pad hydration changes diffusivity; fouling slows the membrane. |
| Transducers | `transducer.py` | Aptamer binding kinetics (closed-form) + probe loss; Michaelis–Menten enzyme + activity loss; Nernstian pH + reference drift. Temperature-dependent. |
| Electrode | `electrode.py` | Stack + transducer + saturating fouling per electrode. |
| Electronics | `electronics.py` | AFE white + 1/f noise scaled by electrode size, per-unit gain/offset, ADC quantization, noise growth with poor contact; duty-cycled coulomb-counted battery with radio retries; BLE link; adhesive/contact. |
| Twin | `twin.py` | Orchestrator: `advance`, `schedule`, `sample`/`step`/`run`, `truth()`, `snapshot`/`restore`/`fork`, bench rig. |
| Analysis | `analysis.py` | Sensitivity, noise, LOD, t90, wear life, battery life → `compare()` / `format_table()`. |
| Adapter | `adapter.py` | `BandageTwinSensor`: one channel as a `SensorInterface`. |

## Engine design (what changed and why)

| Problem in DT-1..DT-4 | Now |
|---|---|
| Global `random` in `state.py`, `device_physics.py`, `observation.py` → runs aren't reproducible (and `test_battery_drains_faster_with_poor_link_quality` in DT-2 is flaky for this reason) | Named `RngStreams` derived from one seed by hashing. Same seed → identical run; adding a component never shifts another's noise; the seed is recorded even when not supplied. |
| `time.monotonic()` / `datetime.now()` inside the twin | `SimClock`; the twin never reads a wall clock. |
| Forward-Euler (DT-1) makes results depend on the caller's step | Every process is integrated exactly (closed-form OU/binding/relaxation), implicitly (diffusion), or with RK4 (wound ODE), inside sub-steps ≤ `max_substep_s`. Reading at 1 s or 10 min gives the same physics. |
| A dropped-in event can land mid-step and be applied late | `advance()` splits at every scheduled event and effect expiry. |
| Three unrelated `WoundState` models | One wound bed, feeding the device through physical analyte concentrations rather than a made-up signal gain. |
| `DigitalTwinEngine` replays scripted checkpoints — no state | The twin is state-based; scripts become scheduled events. |
| No way to branch or resume | `snapshot()` is plain JSON (clock, physics, RNG, pending events, the design); `fork()` runs "what if" from the same instant. |
| Measurement only | Every frame also carries ground truth (true concentrations, noise-free reading, sensor health) so detection/calibration can be scored. |

## Using it

```python
from digital_twin.bandage import BandageTwin, standard_design

twin = BandageTwin(standard_design(), seed=7)
twin.schedule(24 * 3600, "infection", severity=0.7, duration_h=72)
frames = twin.run(4 * 24 * 3600)            # 4 simulated days, one frame / 30 s
frames[-1].readings["CH-01"].raw_signal     # what the device would report
frames[-1].truth.channels["CH-01"]          # what was really there

treated = twin.fork()                       # branch from "now"
treated.schedule(treated.t_s, "antibiotic", potency=10, duration_h=48)
```

Events: `infection`, `antibiotic`, `debridement`, `disturbance` (a knock/peel),
`electrode_fault`. Hold electrodes in a standard solution with
`twin.hold_bench(pathogen_marker=100.0)`.

Compare designs:

```
python scripts/twin_design_report.py
python scripts/twin_design_report.py --export standard > my_design.json   # edit, then:
python scripts/twin_design_report.py --preset standard --design my_design.json
```

Sensitivity and t90 are *measured* on the twin (a fresh sensor held in a
solution and then stepped), so they include the full stack. Noise, LOD, wear
life and battery life are computed from the design's own numbers.

## Running it live (backend `/simulation`)

`POST /simulation/start` with `"bandage_design": "<preset>"` (list them with
`GET /simulation/bandage-designs`; optional `seed`, and `time_scale` as for
DT-6) runs a `BandageTwin` inside `backend/app/simulation.py`'s
`DeviceSimulation`. It is mutually exclusive with `patient_profile` (400 if
both are set) and, like DT-6, rejects `/simulation/scenario` (409).

* **Channels** come from the design's electrodes (`CH-01` aptamer, `CH-02`
  glucose, `CH-03` pH for every preset), not from the request's `channels`;
  `/start` registers them, adding any a pre-existing device row lacks.
* **Each tick** advances the one twin by wall-clock dt x `time_scale`, then
  `sample()`s every electrode at that instant -- the same shared-instant
  guarantee `read_in_cycle`/`BandageTwinSensor` give one-channel callers, done
  directly because the simulation loop already reads all channels per tick.
  Faulting channels (battery, delamination, lifted dressing, lost BLE packet)
  surface as `DEVICE_ERROR` alerts exactly like the other engines.
* **Units**: the AFE's output *is* the device reading -- normalized a.u.
  (100 = a fresh sensor's nominal response), not a physical concentration --
  so the pipelines use identity calibration and store `unit: "a.u."`.
  Mapping back to nM / mM / pH needs an inverse transducer curve and is not
  done.
* **WS messages** on `/ws/devices/{device_id}` (shapes in
  `backend/app/schemas.py`, listed in `backend/app/routers/ws.py`):
  `twin_bandage_truth` = `{device_id, bandage_design, timestamp, truth}` with
  `truth = dataclasses.asdict(Truth)`, dev-only like DT-6's
  `twin_ground_truth` (which is untouched); `twin_eis` = `{device_id,
  channel_id, timestamp, eis}` with `eis = eis_from_twin(...).to_dict()` at
  1.5 % per-point noise (the calibration's baseline-noise SD), one per
  aptamer channel per tick, **not** dev-gated since it is what an EIS reader
  would actually report. The noise seed is derived from the twin's seed and a
  tick counter, so a seeded run is reproducible.

## Limits — read before quoting a number

* **The parameters are illustrative, not fitted.** Layer thicknesses,
  diffusivities, Kd, k_on, fouling rate, noise floor, battery current etc. are
  order-of-magnitude literature-style values chosen to behave plausibly. No
  physical bandage or calibration data exists yet (`docs/calibration/` is
  empty). Use the twin to **rank designs and to test the software pipeline**;
  don't quote its LOD/t90/battery as specifications. When real bench data
  exists, fit the design numbers to it and re-run `characterize()`.
* The wound bed is a small qualitative model, not a clinical healing model.
* The sensing film doesn't deplete local analyte (fine for a monolayer on
  ~1 mm²; would matter for a large enzymatic electrode).
* The live `/simulation` engine (above) starts every run from the design's
  default wound bed with no scheduled events; there is no API yet to
  `schedule()` an infection/antibiotic/disturbance on a running twin, pick
  `WoundBedParams`, or snapshot/fork it.
* Live `twin_eis` compares against the *clean* electrode (`EisParams()`,
  theta = 0), while the twin's aptamer starts equilibrated with the wound's
  baseline marker (~20 nM vs the aptamer's Kd 100 nM, theta ~0.17). So on the
  default, healing wound the reading starts at dRct ~ +27 %, `detected: true`,
  and decays as the marker falls (seed 1: +18 % at 24 h, +8.5 % at 72 h,
  below the 4.5 % threshold by ~1 week). The twin's nM-scale occupancy and
  the EIS model's CFU/mL calibration are two illustrative models joined at
  theta; `cfu_equivalent_per_ml` is an inverse-Langmuir mapping, not a count.
* A live run's readings are reproducible for a given `seed` and sequence of
  tick dts, but the dts come from the wall clock, so two live runs with the
  same seed won't match tick for tick.
* `RngStreams`/`SimClock`/`EventQueue` are general; migrating DT-2/DT-3 onto
  them (and retiring their global-`random` processes) is a separate change,
  deliberately not done here to avoid colliding with the consolidation branches.

## EIS biosensor model

`digital_twin/bandage/impedance.py` models the graphene-PBASE-aptamer working
electrode read by electrochemical impedance spectroscopy (EIS) against E. coli.
Pure functions plus a seeded `EisAssay` (noise from `RngStreams`, no global
`random`, no wall clock). The equations below are a shared contract with the
TypeScript port in `frontend/src/twin3d/`.

```
Z(w)  = Rs + 1 / ( 1/(Rct + Zw) + Q (jw)^n )       modified Randles, CPE for Cdl
Zw    = sigma / sqrt(w) * (1 - j)                  semi-infinite Warburg
Q     = Cdl^n (1/Rs + 1/Rct)^(1-n)                 Brug; Q = Cdl when n = 1
theta = C / (Kd + C)                               Langmuir, C in CFU/mL
Rct = Rct0 (1 + A_RCT theta)    Cdl = Cdl0 (1 - A_CDL theta)
Rs  = Rs0  (1 + A_RS  theta)    sigma = sigma0 (1 + A_SIGMA theta)
dRct% = (Rct_post - Rct_base)/Rct_base * 100 ;  dRct% = slope*log10(CFU) + intercept
detected  <=>  dRct% > k_sigma * noise_SD
```

| Parameter | Default | Unit |
|---|---|---|
| Rs0 (solution resistance) | 120 | ohm |
| Rct0 (charge transfer) | 2000 | ohm |
| Cdl0 (double layer) | 2.0 | uF |
| sigma0 (Warburg) | 150 | ohm s^-1/2 |
| n (CPE exponent) | 0.92 | - |
| Kd | 1e4 | CFU/mL |
| A_RCT / A_CDL / A_RS / A_SIGMA | 1.6 / 0.35 / 0.04 / 0.5 | - |
| Sweep | 0.1 Hz - 100 kHz, 10 pts/decade (61 points) | Hz |
| Reference frequency for the single-number readout | 100 | Hz |
| Calibration slope / intercept | 54.8 / -136 | % per decade / % |
| Baseline noise SD, k_sigma (threshold 4.5 %, LOD about 367 CFU/mL) | 1.5, 3 | %, - |

**Assay sequence** (`EisAssay.run`, phases `idle -> baseline -> exposure ->
binding -> measure2 -> compare -> result`): measure the clean electrode
(baseline spectrum), expose it to the sample, let the aptamers bind
(equilibrium theta, or `binding_kinetics(t)` when an `incubation_s` is given),
measure again, fit the Randles circuit to both noisy spectra (`fit_randles`,
Levenberg-Marquardt on log-parameters, no scipy), compare the fitted Rct/Cdl/Rs/
sigma and |Z| at 100 Hz, and apply the detection threshold and calibration.

**Entry points:** `impedance`, `spectrum`, `log_sweep`, `coverage`,
`params_at_coverage`, `binding_kinetics`, `fit_randles`, `EisAssay`,
`eis_from_twin(twin, "CH-01")` (maps the twin's aptamer-electrode occupancy to a
spectrum, read-only) and `GET /api/twin/eis?cfu=&seed=&noise_pct=&incubation_s=`
(public, stateless; 422 outside cfu 0..1e9).

**Limits.** All circuit values and binding coefficients are illustrative, not
fitted to bench data. The default calibration (54.8 %/decade, -136 %) is
`Calibration.fit_to_model()` over 1e2..1e5 CFU/mL; the model saturates
(dRct% -> 160 %), so `estimated_cfu` is unreliable above ~1e5 CFU/mL. Detection uses the
fitted Rct; the noise model is independent per-point Gaussian, ignoring
drift, electrode-to-electrode spread and non-specific binding.
