# Bandage 3D EIS twin: scene architecture

Browser-side visualisation of the EIS biosensor twin described in
[`bandage_twin.md`](bandage_twin.md). Everything is client-side; the backend is
only used by the optional cross-check (`GET /api/twin/eis`).

## Files (`frontend/src/twin3d/`)

| File | Role |
|---|---|
| `types.ts` | The contract: `EisParams`, `AssayState`, `AssayPhase`, `PartKey`, `BandageScene`. Its header comment holds the circuit equations and defaults. |
| `eis.ts` | Pure TypeScript model: `impedance`, `spectrum`, `logSweep`, `coverage`, `paramsAtCoverage`, Levenberg-Marquardt `fitRandles`, calibration/LOD, and `AssayController` (phase sequencer producing `AssayState`). Port of `digital_twin/bandage/impedance.py`. |
| `eis.selfcheck.ts`, `eis.dump.ts` | Node self-check; JSON dump used by `tests/test_eis_ts_parity.py` (run with `node --experimental-strip-types`). |
| `useAssay.ts` | React hook: owns an `AssayController`, drives it from `requestAnimationFrame` (paused while the tab is hidden), exposes `state` and `controls`. |
| `EisPanel.tsx` | Workflow stepper, controls, Nyquist/Bode plots, parameter table, circuit diagram, detection card. |
| `BandageTwin3D.tsx` | React wrapper around the scene: view/toggle toolbar, selection, `window.bandageScene` in dev builds. |
| `bandageScene.ts` | Scene factory `createBandageScene`: renderer, camera, controls, picking, pins/HUD, telemetry-to-visual mapping, render loop. |
| `scene/model.ts` | Procedural bandage: backing film, adhesive, pad, three-electrode cell, PCB, chip, crystal, coin cell, LED. |
| `scene/inset.ts` | Macro inset: graphene, PBASE, aptamers, bound and free E. coli, Warburg dome. |
| `scene/effects.ts` | Excitation ribbon, current dots, droplet bacteria, Warburg particles, guide lines. |
| `scene/ctx.ts`, `scene/util.ts` | Build context (`reg`, `pickable`, `anchor`), `statusOf`, geometry/texture helpers. |
| `backendCheck.ts`, `BackendCheckPanel.tsx` | Python-vs-browser cross-check (exports `checkAgainstBackend` and the `BackendCheck` component). The component file is not called `BackendCheck.tsx` because that collides with `backendCheck.ts` on case-insensitive file systems. |

`components/BandageTwinView.tsx` composes the page: `useAssay` state feeds both
`BandageTwin3D` and `EisPanel`, and the selected `PartKey` is shared, so
selecting in the 3D view highlights the table row and circuit element, and
vice versa. Entry points: the "Bandage 3D Twin" tab in `App.tsx` and the
standalone `bandage-twin.html` + `src/bandage-main.tsx` (a second Vite input).

## Data flow

`AssayController.tick(dt)` -> `getState(): AssayState` -> React state ->
`scene.update(state)`. The scene copies the state into target values and eases
its displayed values toward them each frame, so discontinuities (phase jumps,
skips) animate smoothly. Non-finite values fall back to the previous display
value. When `state.stale` is set, motion freezes.

## PartKey to sensor parameter

| PartKey | Physical object in the scene | Sensor parameter / readout |
|---|---|---|
| `rct` | working-electrode graphene disc and hex lattice; inset substrate | Rct (charge transfer), dRct% |
| `cdl` | ring around the working electrode; inset rim | Cdl (double-layer capacitance), dCdl% |
| `rs` | sample droplet on the pad | Rs (solution resistance) |
| `warburg` | halo and diffusing particles over the electrode; inset dome | sigma (diffusion) |
| `coverage` | aptamers and bound bacteria in the macro inset | theta (aptamer occupancy) |
| `zmag` | AFE / potentiostat chip | abs(Z) at 100 Hz |
| `phase` | reference capacitors, resistors and crystal | phase at 100 Hz |
| `deltaZ` | status LED | dZ % vs baseline, detection status |
| `battery` | coin cell | none (power / measuring activity only) |

## Telemetry to visual mapping (`glowFor` in `bandageScene.ts`)

| Part | Driven by | Visual |
|---|---|---|
| `rct` | dRct% | emissive glow rises from 0 to full over 0..60 % |
| `cdl` | dCdl% | glow dims as Cdl falls (0.75 at 0 %, clamped 0.1..0.85) |
| `rs` | Rs / 120 ohm | glow 0.25 at nominal, brighter with larger Rs |
| `warburg` | sigma / sigma0 | pulsing glow and particle density |
| `coverage` | theta | glow and number of bound bacteria in the inset (round(theta x max)) |
| `zmag`, `phase`, `deltaZ` | `statusOf(key, state)`: a dZ magnitude over 10 % is a warning | nominal / warning / critical colour and blink; the LED blinks while a measurement runs |
| `battery` | measuring flag | slow pulse during measurement |

Phase-specific animation (`phaseTargets`): droplet size and free bacteria follow
exposure and binding; the excitation ribbon and current dots run during the two
measurement phases.

## Contract rules

`types.ts` is the single source of truth shared by scene, model and panel; do
not rename or reshape existing exports. A new `PartKey` must be added to
`PART_KEYS` and to `LABEL` / `glowFor` / `fmtValue` in `bandageScene.ts`
together. The circuit equations and defaults in the `types.ts` header must stay
in sync with `impedance.py`; `tests/test_eis_ts_parity.py` enforces this to a
relative error of 1e-6 on spectra, sensor parameters, calibration, LOD and
detection. The sequencer's binding step is normalised to end exactly at
Langmuir equilibrium, so the browser result matches the backend's equilibrium
assay.

## Limits

Parameters are illustrative, not fitted. The scene is procedural (no CAD
geometry), the calibration line (54.8 %/decade, -136 %) is fitted to the model and shared by
the browser and the API, and `window.bandageScene`
exists only in dev builds.
