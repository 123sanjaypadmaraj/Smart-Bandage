# 3D Digital Twin — How It Achieves Realism (Reference Guide)

> **Audience:** any Claude session (or human) that needs to understand, modify, or reproduce the 3D engine
> view in this project as an example of a realistic, data-driven Three.js scene.
> **Source of truth:** `public/js/twin3d.js` (1359 lines). Line numbers below refer to that file and may drift
> after edits — search for the function/identifier name if a number no longer matches.
> **Read this first:** there are **no 3D asset files** (no `.glb`, `.obj`, textures or HDRIs). Every part is
> generated procedurally in code. Realism comes from (1) physically based materials + image-based lighting,
> (2) a detailed procedural model, (3) real kinematics, and (4) live telemetry driving the visuals.

---

## Table of contents

1. [What the feature is](#1-what-the-feature-is)
2. [Files and how they connect](#2-files-and-how-they-connect)
3. [Coordinate system and config constants](#3-coordinate-system-and-config-constants)
4. [Module state (`S`) and telemetry ingestion](#4-module-state-s-and-telemetry-ingestion)
5. [Renderer setup](#5-renderer-setup)
6. [Image-based lighting (environment map)](#6-image-based-lighting-environment-map)
7. [Direct lights and shadows](#7-direct-lights-and-shadows)
8. [Camera, controls and preset views](#8-camera-controls-and-preset-views)
9. [Materials (PBR values)](#9-materials-pbr-values)
10. [Procedural engine model, part by part](#10-procedural-engine-model-part-by-part)
11. [Real kinematics (slider-crank)](#11-real-kinematics-slider-crank)
12. [Four-stroke combustion flash](#12-four-stroke-combustion-flash)
13. [Propeller: custom lofted blade geometry](#13-propeller-custom-lofted-blade-geometry)
14. [Pedestal, ground shadow and rings](#14-pedestal-ground-shadow-and-rings)
15. [Telemetry-driven surface state](#15-telemetry-driven-surface-state)
16. [Engine shake (vibration)](#16-engine-shake-vibration)
17. [Flow particles](#17-flow-particles)
18. [X-ray and explode modes](#18-x-ray-and-explode-modes)
19. [Picking and interaction](#19-picking-and-interaction)
20. [UI layer (pins, rail, detail panel, HUD, sparkline)](#20-ui-layer)
21. [The render loop and performance controls](#21-the-render-loop-and-performance-controls)
22. [Robustness: fallback, context loss, staleness, teardown](#22-robustness)
23. [Boot sequence and public API](#23-boot-sequence-and-public-api)
24. [Accessibility and reduced motion](#24-accessibility-and-reduced-motion)
25. [Why it looks realistic — summary checklist](#25-why-it-looks-realistic--summary-checklist)
26. [How to make it even more realistic](#26-how-to-make-it-even-more-realistic)
27. [Recipes: common modifications](#27-recipes-common-modifications)
28. [Gotchas and things not to break](#28-gotchas-and-things-not-to-break)

---

## 1. What the feature is

A 3D "digital twin" of a **horizontally-opposed (boxer) six-cylinder aero piston engine**, shown on the
dashboard. It is part of a Smart India Hackathon demo (real-time engine health monitoring for MALE UAVs
using simulated telemetry). The model is procedural and is animated from the **same live telemetry** as
the rest of the dashboard:

| Telemetry key | Visual effect |
|---|---|
| `rpm` | Crankshaft/propeller angular speed (slow-motion scaled), flow particle speed, propeller blur disc |
| `cht` (cylinder head temp) | Barrel/head/fin thermal glow (black-body ramp) |
| `egt` (exhaust gas temp) | Exhaust header glow |
| `vibration` | Whole-engine shake amplitude |
| `fuelFlow`, `manifoldPressure` | Fuel / intake particle density and speed |
| `oilPressure` | Oil-circuit particle speed and density |
| every sensor | Nominal / warning / critical glow on its own component |

**Status (nominal/warning/critical) always comes from the backend payload.** The sensor "bands" fetched
from `/api/meta` are used only to *scale visuals* (e.g. how hot "hot" looks). Do not compute status in the
front end.

## 2. Files and how they connect

| File | Role |
|---|---|
| `public/js/twin3d.js` | The entire 3D twin: scene, model, animation, picking, UI wiring, fallback |
| `public/index.html` | Contains the DOM the script binds to: `#twinStage`, `#twinCanvasHost`, `#twinPins`, `#twinRail`, `#twinDetail`, `#twinHud`, toolbar buttons (`#twinXray`, `#twinExplode`, `#twinFlow`, `#twinLabels`, `#twinSpin`, `#twinSlow`, `#twinSlowLabel`), `#twinLoading`, plus `data-view` buttons |
| `public/css/style.css` | Styling for `.twin-*` classes (pins, rail rows, HUD, overlay, status colours) |
| `public/img/engine-photo.png` | Reference photo the model was designed after |
| `server.js` | Serves `/api/meta` (sensor bands) and static files incl. `/vendor/three/...` |
| `docs/ARCHITECTURE.md`, `README.md`, `CHANGELOG.md` | Mention the twin at a higher level |

Three.js is loaded through an **import map** in `index.html` (~line 232):

```html
<script type="importmap">{"imports":{"three":"/vendor/three/build/three.module.js","three/addons/":"/vendor/three/examples/jsm/"}}</script>
<script type="module" src="js/twin3d.js"></script>
```

So the script uses bare specifiers (`import * as THREE from 'three'`) and Three.js is self-hosted under
`/vendor/three` (no CDN at runtime). Imports used (lines 26-29):

```js
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
```

The dashboard's other code communicates with the twin through a **global**: `window.dtState`
(`.latest.engines`, `.selectedEngineId`, `.stale`) and the twin exposes `window.twin3d` (see §23).

## 3. Coordinate system and config constants

Lines 24, 33-39:

- **Crank axis = X**, propeller at **+X**, **up = Y**, the two cylinder banks lie along **+Z / −Z**.
- `STATION_X = [-1.05, 0, 1.05]` — three cylinder stations along the crank (× 2 banks = 6 cylinders).
- `BOXER_OFFSET = 0.12` — opposed cylinders are staggered by one rod width so their rods don't collide
  on the shared crank pin region (real boxer engines do this).
- `THROW_ANGLE = [0, 4π/3, 2π/3]` — 120° crank-throw phasing.
- `THROW_R = 0.22` (crank radius), `ROD_L = 0.85` (connecting-rod length), `CRANK_PLANE_PIN_HALF = 0.075`.
- `TAU = 2π`.

`PARTS` (lines 41-51) is the catalogue of 9 selectable components, each with a number, name and a
plain-English `blurb` used in the detail panel. Keys match sensor keys:
`cht, egt, oilPressure, oilTemp, fuelFlow, manifoldPressure, vibration, rpm, batteryVoltage`.
`PART_ORDER = Object.keys(PARTS)` fixes the iteration order everywhere.

`STATUS_RGB` (lines 54-58) maps status → `THREE.Color`: nominal `#34d399`, warning `#ffb700`, critical `#ff3b30`.

## 4. Module state (`S`) and telemetry ingestion

Lines 61-99. `S` is a single module-level state object:

- `sensors` — bands from `/api/meta` (may be `null` until loaded).
- `target` — latest raw readings. `disp` — **smoothed** readings actually used to render.
- `status` — latest statuses from the backend. `history` — last 60 values per sensor (for the sparkline).
- `selected`, `hover` — interaction state.
- `xray`, `explode` (eased 0..1), `explodeOn`, `flow`, `labels`, `autoRotate` — toggles.
- `slow` — slow-motion divisor (default 60 → "1:60"). `theta` — unbounded crank angle in radians.
- `lastUpdate`, `engineTail` (aircraft tail number), `stale`.

`ingestEngine(eng)` (line 87) is **defensive**: it ignores non-objects, copies `eng.tail`, `eng.statuses`,
stamps `S.lastUpdate`, and for each `eng.readings` entry that is a finite number it updates `S.target[key]`
and pushes into a 60-sample ring in `S.history`. Garbled/missing fields never throw.

`fmt(key, v)` formats values (0 decimals for rpm, 2 for vibration & battery voltage, else 1; `--` if not
finite). `STATUS_FLAG` gives **non-colour cues** (`▲` warning, `✖` critical) for accessibility.

`paintRow` / `makeRailRow` (lines 110-138) build and paint a readout-rail row; they are shared by the 3D
UI and the no-WebGL fallback.

## 5. Renderer setup

Lines 145-159:

```js
renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));   // crisp but capped at 2x
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;                    // soft shadow edges
renderer.toneMapping = THREE.ACESFilmicToneMapping;                  // filmic highlight rolloff
renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;                    // correct gamma
```

Why each matters for realism:
- **ACES Filmic tone mapping** compresses bright specular highlights smoothly instead of clipping — this is
  the single biggest change from "flat CG" to "photographic".
- **sRGB output** keeps colours and gradients perceptually right.
- **PCF soft shadows** avoid hard, aliased shadow edges.
- `alpha: true` lets the page background show through.
- If `new WebGLRenderer` throws, `installFallback` runs (see §22).

## 6. Image-based lighting (environment map)

Lines 161-175. Metals are **almost entirely defined by what they reflect**, so a metallic material with no
environment looks black/dull. The scene uses a procedurally built environment:

```js
const pmrem = new THREE.PMREMGenerator(renderer);
function buildEnvironment() {
  const room = new RoomEnvironment();          // built-in synthetic studio room (no HDRI file)
  const target = pmrem.fromScene(room, 0.04);  // prefiltered mipmapped radiance env map
  if (room.dispose) room.dispose();
  if (envTarget) envTarget.dispose();
  envTarget = target;
  scene.environment = target.texture;
}
buildEnvironment();
scene.environmentIntensity = 0.75;
```

Key design points:
- `RoomEnvironment` needs **no external image** — it's a small procedural scene of softboxes; PMREM converts it
  into a roughness-aware reflection map so rough metals get blurry reflections and polished chrome gets sharp ones.
- The environment lives in a **render target**, which is destroyed when the GL context is lost, so it is built
  by a function that the `webglcontextrestored` handler can call again (§22).
- `environmentIntensity = 0.75` tames reflections so the dark UI theme still reads.

## 7. Direct lights and shadows

Lines 196-208:

```js
const key = new THREE.DirectionalLight(0xffffff, 2.2);   // main light, casts shadows
key.position.set(6, 9, 6);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, near: 1, far: 30 });
key.shadow.bias = -0.0004;        // fight shadow acne
key.shadow.normalBias = 0.03;     // fight peter-panning on curved surfaces
const rim = new THREE.DirectionalLight(0x5b8cff, 1.1);   // cool blue rim from behind
rim.position.set(-7, 3, -6);
scene.add(new THREE.HemisphereLight(0xbfd0ff, 0x1a1208, 0.35));  // sky/ground fill
```

Three-point-ish setup: **warm/neutral key + cool rim + hemisphere fill**. The blue rim matches the UI accent
colour (`#5b8cff`) and separates dark parts from the dark background. The shadow frustum (±8) is tightly
fitted to the engine so 2048² resolution is spent where it matters.

## 8. Camera, controls and preset views

Lines 177-194. `PerspectiveCamera(32, ...)` — a **narrow 32° FOV** (telephoto-ish) reduces perspective
distortion, which makes mechanical objects look like product photography.

Preset views (`VIEWS`): `iso`, `front`, `side`, `top`, `rear`, each `{ pos, target }`. `top` uses `z = 0.01`
(not exactly 0) to avoid the gimbal degeneracy of looking straight down.

`OrbitControls`: damping on (`0.08`), `minDistance 4`, `maxDistance 26`, `maxPolarAngle 0.96π`,
`autoRotateSpeed 1.2`.

Camera moves use a **tween** (`camTween`, lines 286-296, 313-319): smoothstep easing
`e = t*t*(3-2t)` over 0.9 s (0.01 s if `prefers-reduced-motion`). `ui.onZoom(factor)` (lines 338-347) tweens the
camera along its view vector — used to pull back when Explode is toggled.

## 9. Materials (PBR values)

All surfaces are `MeshStandardMaterial` (metal/rough workflow). Shared palette (lines 598-606):

| Name | Colour | metalness | roughness | Used for |
|---|---|---|---|---|
| `alu` | `#a9afb8` | 0.92 | 0.34 | Aluminium castings (crankcase, sump) |
| `black` | `#16171b` | 0.55 | 0.5 | Anodised/black parts (magnetos, alternator body) |
| `chrome` | `#d4d8de` | 1.0 | 0.14 | Pushrod tubes, port flanges, dipstick, fuel line |
| `red` | `#c41e1e` | 0.1 | 0.45 | Ignition leads, spin marker |
| `rubber` | `#0b0b0d` | 0 | 0.85 | Hoses, boots, terminals |
| `steel` | `#8a8f98` | 1.0 | 0.3 | Bolts, rings, clamps |
| `oilOrange` | `#d98a1f` | 0.4 | 0.5 | Oil cap |

Realism rules baked into these numbers:
- **Metals have `metalness ≈ 1`** and their colour is the *reflection tint*. Non-metals (rubber, red plastic)
  have `metalness ≈ 0`.
- **Roughness varies by part**: polished chrome ≈ 0.14, cast aluminium ≈ 0.34, rubber ≈ 0.85. Uniform
  roughness is the most common "CG look" mistake.
- **Colour variation between adjacent parts** (barrel `#24272c`, fins `#2c3036`, head `#1b1d21`, rocker cover
  `#e6e9ee`) prevents a monotone blob and makes assemblies readable.

### Material factory (lines 549-556)

```js
function makeMaterialFactory(parts) {
  return function mat(partKey, params, flags = {}) {
    const m = new THREE.MeshStandardMaterial({ envMapIntensity: 1, ...params });
    m.userData = { ...flags };            // e.g. { thermal: 1 } or { statusGain: 1.2 }
    if (partKey) parts[partKey].mats.push(m);   // registers material for telemetry glow
    return m;
  };
}
```

Materials created through `M(partKey, ...)` are **registered to a telemetry part** so `applySurfaceState`
can drive their `emissive` colour. Materials created with plain `new THREE.MeshStandardMaterial` (chrome,
red, rubber, steel, oilOrange) are static and never glow.

`userData.thermal` = how strongly that material shows heat glow; `userData.statusGain` = how strongly it
shows status glow.

## 10. Procedural engine model, part by part

Everything is built in `buildEngine(root)` (line 559). Helpers:

- `mesh(geom, material, parent, partKey, {pos, rot, cast})` — creates a mesh, sets shadows
  (`castShadow = cast`, `receiveShadow = true`), and if `partKey` is given, tags `userData.part` and adds it to
  `pickables` and `parts[partKey].meshes`. Pass `partKey = null` for decorative bits that shouldn't be
  clickable. Small detail parts pass `cast: false` to save shadow-map cost.
- `assembly(name, explodeVec, partKey)` — creates a top-level `Group` registered for **Explode mode** with its
  displacement vector.
- `anchorOn(partKey, group, pos, normal)` — places an invisible `Object3D` anchor and a facing normal used by
  the UI pins (label placement + back-face hiding).

Realistic primitives used: `RoundedBoxGeometry` (bevelled edges catch light — a major realism cue),
`CylinderGeometry` (barrels, fins, bolts), `LatheGeometry` (spinner), `TubeGeometry` along
`CatmullRomCurve3` (hoses, pipes, ignition leads), `TorusGeometry` (clamps), and a custom
`BufferGeometry` (propeller blade).

### 10.1 Crankcase — key `vibration` (lines 608-647)
- Two split halves (±Z) each a `RoundedBoxGeometry(3.9, 1.15, 0.5)`; **cylinder mounting pads** per station;
  **18 through-bolt heads** per half (9 × top/bottom) — small steel cylinders.
- A thin dark **seam** box between the halves (`caseSeamMat`), hidden when exploded.
- Rear **accessory case** + two **magnetos** (cylinder + flange + rubber terminal); their world-space
  terminals are stored in `magTerminals` for ignition leads.
- **Nose casting** behind the propeller flange.
- Anchor for pin #7.

### 10.2 Oil sump & filter — key `oilPressure` (lines 649-664)
- Wet-sump `RoundedBox` plus **9 cooling ribs**, drain plug (hexagonal cylinder, 6 segments), blue spin-on
  **oil filter** and pump, **dipstick** (chrome, slightly tilted) with an orange cap.

### 10.3 Oil cooler — key `oilTemp` (lines 666-680)
- Core block + **16 thin aluminium fins**; two rubber **hoses** as `TubeGeometry` along splines
  (`oilHoseA/B`). These same curves later feed the oil-flow particles.

### 10.4 Alternator & belt — key `batteryVoltage` (lines 682-707)
- Body + end cap + **12 cooling slots** around the perimeter, a **pulley that spins** (`altPulley`) with a red
  marker so its rotation is visible, and **two straight belt runs** built as boxes oriented with
  `quaternion.setFromUnitVectors(...)` between crank pulley (r 0.5) and alternator pulley (r 0.2).

### 10.5 Cylinders — key `cht` (lines 709-770)
For each of 3 stations × 2 banks (6 total), inside a `holder` group rotated ±90° about X and an `inner`
group with `scale.z = side` (so port sides face down on both banks):
- Barrel (`CylinderGeometry` r 0.29) + **7 cooling fins** (slightly decreasing radius).
- Head core (`RoundedBox`) + **6 head fins**, **rocker cover** (bright `#e6e9ee`) with 4 bolt heads.
- **Two chrome pushrod tubes**, a rubber **spark-plug boot**, chrome exhaust flange and steel intake flange
  (with `Object3D` markers `ex` / `inl` used later to start exhaust/intake curves).
- A hidden **combustion flash sphere** (additive `MeshBasicMaterial`), visible only in X-ray (§12).
- Materials flagged `thermal` so they can glow with CHT.

### 10.6 Crankshaft, pistons, rods (lines 772-817)
- `rot` group spins about X. Main journal, per-throw **crank webs** (two discs), a **counterweight** (a
  half-cylinder created with `thetaStart/thetaLength`, then `rotateZ`) and the **crank pin** placed at
  `THROW_R` from the axis at that throw's angle.
- For each cylinder a **piston group** (piston, 3 steel ring bands, wrist pin) is parented **directly to
  `root`** (not the exploding cylinder assembly) so pistons/rods stay put when cylinders explode away. A
  **connecting rod** (tapered cylinder) and **big-end** cylinder are separate meshes recomputed each frame.

### 10.7 Propeller and flange (lines 819-848) — see §13 for the blade.
- Crank pulley (r 0.5), flange (r 0.62) with **6 bolts**, **spinner** from `LatheGeometry` using
  `r = 0.66*sqrt(1 - u^1.7)*(1 - 0.05u)` (a smooth ogive), two blades (second one rotated by π),
  and a **motion-blur disc** (`CircleGeometry(3.05)`, additive-free `MeshBasicMaterial`, `depthWrite:false`)
  whose opacity rises with shaft speed to suggest blur without a post-process.

### 10.8 Intake manifold — key `manifoldPressure` (lines 850-864)
- Horizontal plenum tube + **6 runners** as `TubeGeometry` along `CatmullRomCurve3` to each cylinder;
  curves stored as `intakeCurves` for flow particles.

### 10.9 Fuel servo & line — key `fuelFlow` (lines 866-880)
- Servo body, throttle body cylinder, **fuel filter** (large chrome cylinder), flange, fuel divider block,
  and a chrome fuel line (`fuelCurve`) ending at a fitting.

### 10.10 Exhaust — key `egt` (lines 882-909)
- For each cylinder, a header tube from the port's **world position** (`c.ex.getWorldPosition`, after
  `root.updateMatrixWorld(true)`) sweeping down and rearward, joining a **collector pipe per bank** with a
  flared tail and a **clamp torus**. Curves extended to `x=-3.15` are stored in `exhaustCurves` for particles.
- Materials flagged `thermal: 1.2` so exhaust glows brightest.

### 10.11 Ignition leads (lines 911-923)
- Six thin (r 0.017) **red** `TubeGeometry` leads from each cylinder's plug boot area to the matching magneto
  terminal, following a 7-point spline. Static (not tied to telemetry).

## 11. Real kinematics (slider-crank)

Header comment (lines 19-22) and `animate()` (lines 944-978). For every piston each frame:

```js
const phi = theta + c.beta;                         // crank angle relative to this cylinder
const s = THROW_R * Math.cos(phi)
        + Math.sqrt(ROD_L*ROD_L - THROW_R*THROW_R*Math.sin(phi)**2);   // piston-pin distance from crank axis
p.g.position.set(c.xc, 0, c.side * s);              // piston moves along the bank axis (±Z)

const ang = theta + c.pinAng;                       // crank-pin angle
tmpP.set(c.xc, THROW_R*Math.sin(ang), THROW_R*Math.cos(ang));   // crank-pin position
tmpQ.set(c.xc, 0, c.side * s);                                   // piston-pin position
p.rod.position.copy(tmpP).add(tmpQ).multiplyScalar(0.5);         // rod midpoint
p.rod.quaternion.setFromUnitVectors(yAxis, tmpQ.clone().sub(tmpP).normalize()); // rod orientation
p.bigEnd.position.copy(tmpP);
```

Why it reads as real: the rod **angulates** (not a rigid slide), the piston velocity has the true
non-sinusoidal shape, and because `pinAng = beta` for +Z bank and `beta + π` for −Z bank, **opposed pistons
mirror each other** (boxer). The crankshaft group rotates with `rot.rotation.x = -theta`; the alternator
pulley spins at 2.5× (`altPulley.rotation.x = -theta * 2.5`, from the pulley ratio 0.5/0.2).

## 12. Four-stroke combustion flash

Lines 971-976. Only visible in **X-ray**:

```js
const cam = c.side === 1 ? 0 : 1;                       // bank offset of 360° in the 720° cycle
const psi = (((theta + c.beta + cam*TAU) % (2*TAU)) + 2*TAU) % (2*TAU);   // 0..4π
const burn = psi < Math.PI ? Math.exp(-psi * 1.7) : 0;  // sharp bang just after TDC, decays fast
c.flash.visible = xray;
c.flashMat.opacity = xray ? burn * 0.85 : 0;
```

Each cylinder fires once per **720°** (two revolutions), staggered by throw phase and bank.
The HUD "CYCLE nnn°" readout uses the same 0-720° mapping (§20).

## 13. Propeller: custom lofted blade geometry

`bladeGeometry()` (lines 986-1030) builds a blade with no model file:
- 16 radial **stations** from hub `R0 = 0.55` to tip `R1 = 3.0`, each an 18-point **lens-shaped airfoil ring**.
- `chord` tapers with a sine bulge (`0.36 + 0.3·sin(π·min(1,1.15u+0.1)) − 0.16u`), `thickness = chord·(0.17 − 0.09u)`.
- **Twist** decreases from 38° at the root to 6° at the tip (`38 − 32·u^0.8`) — real propellers are twisted.
- Asymmetric **camber** (`1.25×` on one face, `0.7×` on the other).
- **Vertex colours**: dark grey blade with **yellow tip** (`u > 0.94`) like real tip-marking paint. Material uses
  `vertexColors: true`, `side: DoubleSide`.
- Index winding builds quads between rings; a tip cap fan closes the end; `computeVertexNormals()` gives smooth shading.

## 14. Pedestal, ground shadow and rings

`buildPedestal` (lines 1032-1054):
- A dark disc platform (`CylinderGeometry(7.4, 7.6, 0.16, 96)`, metalness 0.6, roughness 0.55) that receives shadows.
- Three thin blue **`RingGeometry`** rings (radius 3, 5, 7, opacities 0.25/0.16/0.10) — a "hologram stage" look
  that also gives scale.
- A large `PlaneGeometry` with **`ShadowMaterial({opacity: 0.4})`** — invisible except where the engine's
  shadow falls, grounding the model without a visible floor plane.

## 15. Telemetry-driven surface state

`applySurfaceState(model, time)` (lines 516-546), run every frame:

1. **Thermal glow** (only `cht` and `egt`):
   - `cht`: `thermal = norm('cht', disp.cht, 90) ** 2.6`
   - `egt`: `thermal = norm('egt', disp.egt, 600) ** 3.2 * 0.9`
   - High exponents keep a healthy engine looking cold and make glow appear only near the limits.
   - `norm(key, value, floor)` maps a reading to 0..1 between `floor` and the sensor's `highCrit`
     (fallback `nominal[1]*1.3`). Returns 0 if bands are unavailable.
   - `heatColor(h)` black-body-ish ramp: `h<0.5` dull red (0.55,0.05,0) → `h≥0.5` toward orange/yellow-white.
2. **Status glow**: nominal = 0; warning = `0.16 + 0.1·sin(3.2t)` (breathing); critical = `0.34 + 0.24·sin(9t)` (strobe),
   tinted with `STATUS_RGB[status]`, scaled by `userData.statusGain`.
3. **Interaction accent**: selected `0.22 + 0.06·sin(4t)`, hovered `0.14`, tinted blue `(0.36,0.55,1)`.
4. All three are **summed into `material.emissive`**, per registered material, each frame.

Because glow is driven through `emissive` on the standard PBR material, heat looks like light emitted by the
metal itself and still receives normal shading on top.

## 16. Engine shake (vibration)

`applyShake` (lines 485-495). Vibration is mm/s RMS (nominal ≈ 1, critical ≈ 4.2):

```js
const a = Math.max(0, vibration - 1.4) * 0.0085 + vibration * 0.0009;
group.position.set(sin(t*61.3)*a*0.5, sin(t*47.1+1.3)*a, sin(t*53.7+2.1)*a*0.8);
group.rotation.x = sin(t*39.9) * a * 0.12;
```

Incommensurate frequencies (61.3, 47.1, 53.7, 39.9) keep the motion from looking periodic. The amplitude
is intentionally near-invisible at nominal so a healthy engine looks stable. Disabled when stale or under
reduced motion. Applied to the `engine` group only (the pedestal and lights stay still).

## 17. Flow particles

`buildFlows` (lines 1056-1097). Four `THREE.Points` clouds (additive blending, `depthWrite:false`,
`sizeAttenuation:true`) that ride the same splines the pipes are built from:

| Flow | Curves | Colour | Count | Speed driver | Density driver |
|---|---|---|---|---|---|
| Intake air | `intakeCurves` | `#7cc7ff` | 260 | rpm | manifoldPressure/100 |
| Exhaust | `exhaustCurves` | `#ff8a3d` | 260 | rpm | egt/760 |
| Fuel | `fuelCurve` | `#ffd24a` | 60 | fuelFlow/15 | fuelFlow/15 |
| Oil | `oilCurves` (hose A + reversed hose B) | `#ffa53a` | 70 | oilPressure/55 | oilPressure/60 |

Per frame: each particle advances `u += speed*dt*3.2 / curveLength`; at `u≥1` it respawns on a random curve.
**Density** is implemented by parking particles with index above `count*density` at `y = -999` (off-screen)
instead of resizing buffers. Speed is divided by `S.slow` so particles stay consistent with the slow-motion
crank. Particles are hidden when Flow is off, telemetry is stale, or explode > 0.05. `frustumCulled = false`
because the bounding sphere of a dynamically rewritten buffer would be wrong.

## 18. X-ray and explode modes

**X-ray** (lines 925-934, 944-946): `xrayMats` holds `[material, ghostOpacity]` pairs for the crankcase and
cylinder barrels/fins/heads (0.16-0.3). `setXray(on)` toggles `transparent`, `opacity`, `depthWrite` and sets
`needsUpdate = true`. It only runs when the state changes (`xrayState`). In X-ray the combustion flash spheres
also become visible.

**Explode** (lines 951-956): `S.explode` is eased (`1 - exp(-dt/0.28)`) toward 0/1. Each `assembly` group is
translated by `dir * explode`; crankcase halves split ±0.75 along Z; the seam is hidden below 0.05. Pistons and
rods are parented to `root` on purpose so they stay with the crank. The blur disc fades out as explode rises.

## 19. Picking and interaction

Lines 226-257:
- `Raycaster` against `model.pickables` (only meshes that were given a `partKey`), `recursive = false`.
- `pointermove` updates a normalised pointer; hover is resolved once per frame in `pick()`.
- Click vs drag: `pointerdown` records the position; on `pointerup` a move > 5 px is treated as an **orbit drag**
  and ignored, otherwise the hovered part is selected (or deselected if clicked again).
- Cursor toggles `grab`/`pointer`. Hover state is shared with DOM pins and rail rows so all three highlight together.

## 20. UI layer

`buildUi` (lines 1105-1309):

- **Pins** (`#twinPins`): one button per part, positioned each frame by projecting its 3D `anchor` to screen
  (`v.project(camera)` → pixel `translate`). Hidden when the part faces away (`cdir.dot(normal) > -0.15`),
  is behind the camera (`v.z < 1`), or off-screen. `tabIndex = -1` because the **rail** duplicates them for keyboard use.
  Each pin's chip shows `flag + label + value + unit`.
- **Rail** (`#twinRail`): keyboard-reachable buttons, one per sensor, with `aria-pressed` and `aria-label`.
- **Toolbar**: view buttons (`[data-view]`) call `api.onView`; toggles (`X-ray`, `Explode`, `Flow`, `Labels`,
  `Orbit`) flip `S` flags and mirror `active` + `aria-pressed`. Explode also zooms out 1.3× so parts stay in frame.
- **Slow-motion slider**: log-scaled, `S.slow = round(12 * (400/12)^(v/100))` → 1:12 … 1:400; default value 39 ≈ 1:60.
  Rationale (also in the tooltip): real shaft speed (~5000 RPM) aliases on screen, so the twin runs in slow motion.
  On-screen angular speed: `omega = (rpm * TAU / 60) / S.slow`.
- **Detail panel** (`#twinDetail`): title, status badge, value, sensor label, blurb, nominal/warn/crit **band text**
  (`bandText`), close button, and a **sparkline** canvas (`drawSpark`) that draws the last 60 samples, shades the
  nominal band green, and colours the line by status; y-range auto-fits with 10% padding.
- **HUD** (`#twinHud`): `● TWIN SYNCED` / `▲ TELEMETRY STALE` / `▲ NO LINK`, tail number, `SHAFT n RPM`,
  `CYCLE nnn°` (crank angle mod 720), `LINK AGE n.ns`. Updated at most every **250 ms** (text doesn't need 60 fps DOM writes).

## 21. The render loop and performance controls

`init()` (lines 259-419):
- **Resize**: `ResizeObserver` on the stage; `resize()` also re-reads `devicePixelRatio` (browser zoom/monitor change).
- **Loop gating** (`syncLoop`): the rAF loop runs only if the twin isn't disposed, the stage is **on screen**
  (`IntersectionObserver`), the **tab is visible** (`visibilitychange`), the GL context isn't lost, and fewer than
  5 consecutive frame failures occurred. Otherwise `cancelAnimationFrame` is called — no wasted GPU/CPU.
  When resuming, `lastT` is reset so paused time isn't integrated.
- **`tick(forcedDt)`** — one frame; `forcedDt` lets tests or hidden tabs step manually. `dt` is clamped to 0.1 s.
  Order each frame: ease displayed readings (`k = 1 - exp(-dt/0.7)`, ~0.7 s time constant) → ease explode →
  camera tween → controls update → pick → integrate `theta` → `model.animate` → shake → surface state →
  flows → UI frame → `renderer.render`.
- **Smoothing** of readings means telemetry updates (e.g. once per second) don't cause visual jumps.
- **Error containment**: `loop()` wraps `tick()` in try/catch; after 5 consecutive failures it stops the loop and
  shows an overlay message. Sensor readouts remain functional.

## 22. Robustness

- **No WebGL / build failure** → `installFallback(stage, msg)` (lines 444-482): adds `.twin-fallback`, empties the
  canvas host, shows an error note, and renders the sensor rail as static (non-button) rows. It installs a
  `window.twin3d` with the same API (`update`, `resetHistory`, `setStale`, `tick`, `dispose`) so callers don't
  need to branch. `boot()` (line 1339) catches init exceptions and calls it too.
- **GL context lost** (lines 377-391): `preventDefault()` on `webglcontextlost` so the browser can restore;
  overlay shown; loop paused. On `webglcontextrestored`, `buildEnvironment()` is re-run (the PMREM target was
  lost), overlay hidden, resize and loop resumed.
- **Stale telemetry** (`S.stale`): rpm forced to 0, shake off, flow hidden — **a stalled feed must not look like a
  running engine**. HUD switches to `▲ TELEMETRY STALE`.
- **Teardown** (`dispose`, lines 394-417): idempotent; stops loop, removes listeners/observers, disposes
  controls, **every geometry, material and texture** found by traversing the scene, the env target, PMREM
  generator and renderer; removes DOM; clears `window.twin3d`. Hooked to `pagehide` when the page isn't being
  put in bfcache (`!e.persisted`).
- **`/api/meta` down** (lines 1314-1358): 6 s abort timeout; the twin boots without bands (glow scale = 0,
  values still shown) and retries up to 3 times at 10 s intervals; on success `refreshFromState()` repaints.

## 23. Boot sequence and public API

Boot (line 1350): `loadMeta().then(() => { boot(); retry-if-needed })`. Inside `init()`, if
`window.dtState.latest.engines` already exists, it immediately calls `window.twin3d.update(...)` with the
currently selected engine (`dtState.selectedEngineId`, else the first), and applies `dtState.stale` if set.
The `#twinLoading` element is removed once ready.

`window.twin3d` API:

| Method | Purpose |
|---|---|
| `update(eng)` | Feed one engine snapshot: `{ tail, statuses, readings }` |
| `resetHistory()` | Clear sparkline history (e.g. when switching engines) |
| `setStale(bool)` | Freeze/unfreeze the mechanism when the feed stalls |
| `tick(dt)` | Step one frame manually (tests / hidden tab) |
| `dispose()` | Full teardown |
| `fallback: true` | Present only when running in no-WebGL fallback mode |

Expected `eng` shape:

```js
{
  id: 'engine-1',
  tail: 'VT-XYZ',
  statuses: { cht: 'nominal', egt: 'warning', ... },          // from backend
  readings: { rpm: 4800, cht: 182.4, egt: 690, oilPressure: 48, ... }
}
```

## 24. Accessibility and reduced motion

- Canvas host has `role="img"` and a descriptive `aria-label`; live readouts live in the keyboard-reachable rail.
- Status is never colour-only: `▲` / `✖` prefixes and text badges.
- `prefers-reduced-motion`: camera tweens become ~instant, shake disabled, auto-orbit disabled.
- Overlay uses `role="status"`; toggle buttons maintain `aria-pressed`.

## 25. Why it looks realistic — summary checklist

1. **PBR metal/rough materials with per-part values** (chrome 0.14 vs aluminium 0.34 vs rubber 0.85). (§9)
2. **Image-based lighting** via `RoomEnvironment` + PMREM so metals have things to reflect. (§6)
3. **ACES Filmic tone mapping + sRGB output** for photographic highlight rolloff. (§5)
4. **Key + rim + hemisphere lighting** with tuned soft shadows (2048², bias + normalBias). (§7)
5. **Bevelled edges** (`RoundedBoxGeometry`) that catch highlights. (§10)
6. **High detail count**: fins, bolts, ribs, clamps, hoses, leads, pulleys, flanges — hundreds of small meshes. (§10)
7. **Curved organic parts** from splines (`TubeGeometry` + `CatmullRomCurve3`). (§10)
8. **Real slider-crank motion** with angulating rods and boxer mirroring. (§11)
9. **Custom airfoil-lofted, twisted propeller with painted tips.** (§13)
10. **Narrow-FOV camera** (32°) like product photography. (§8)
11. **Ground-contact shadow** on a `ShadowMaterial` plane. (§14)
12. **Emissive black-body glow** tied to real telemetry, with high exponents so healthy = cold. (§15)
13. **Smoothed readings** (0.7 s time constant) — no jumpy values. (§21)
14. **Subtle shake** with incommensurate frequencies. (§16)
15. **Additive particle flows** on the actual pipe splines. (§17)
16. **Motion-blur disc** faking blur with no post-processing. (§10.7)

## 26. How to make it even more realistic

Not implemented yet (ideas, roughly by impact/cost):

- Replace `RoomEnvironment` with a real **HDRI** (`RGBELoader` + `PMREMGenerator.fromEquirectangular`).
- Add **normal/roughness maps** (brushed metal, casting grain, heat-tinted exhaust) — everything is currently uniform.
- **Ambient occlusion** (`SSAOPass`/`GTAOPass`) in `EffectComposer` to darken crevices between fins.
- **Bloom** (`UnrealBloomPass`) so thermal/critical glow blooms — note this requires switching the render call to the composer and re-checking tone mapping/colour space.
- `MeshPhysicalMaterial` with **clearcoat** on painted/anodised parts.
- **Contact shadows / baked AO** on the pedestal.
- **Decals/text** (part numbers, data plates, warning stickers) via canvas textures.
- Real **GLB model** exported from CAD, keeping the same `PARTS` keys and `userData.part` tags so all telemetry hooks keep working.

## 27. Recipes: common modifications

**Add a new telemetry-driven part**
1. Add an entry to `PARTS` with the same key as the sensor (adds pin, rail row and detail automatically).
2. In `buildEngine`, create materials with `M('<key>', {...}, {thermal?/statusGain?})`, build an `assembly(...)`
   and meshes with `partKey = '<key>'`, then call `anchorOn('<key>', group, pos, normal)`.
3. Every key in `PART_ORDER` **must** have an anchor, or its pin won't place (`if (!part.anchor) continue`).

**Add a thermal glow to another part**
- Add a branch in `applySurfaceState` for its key computing `thermal`, and pass `{ thermal: <gain> }` when creating the material.

**Change look of a metal**
- Adjust `metalness` (keep ≥ 0.85 for bare metal) and `roughness` (lower = sharper reflections).

**Add another flow**
- Call `make(curves, colorHex, count, size, speedFn, densityFn)` inside `buildFlows` with `THREE.Curve` objects and functions of the smoothed readings `d`.

**Tune brightness**
- `renderer.toneMappingExposure`, `scene.environmentIntensity`, key light intensity (2.2), rim (1.1), hemisphere (0.35).

## 28. Gotchas and things not to break

- **Never compute status client-side** — it comes from the backend; `/api/meta` bands only scale visuals.
- Keep pistons/rods parented to `root`, not to the exploding cylinder assemblies.
- Rebuild the environment on `webglcontextrestored` — render targets don't survive context loss.
- Keep `frustumCulled = false` on particle `Points` whose buffers are rewritten.
- Don't change `pointer` click logic's 5 px drag threshold without checking orbit-vs-select behaviour.
- The stale-feed freeze is a deliberate safety cue; don't remove it for "nicer looking" idle animation.
- Materials registered via `M('<key>', ...)` get their `emissive` overwritten every frame; if you want a
  static emissive on such a material, it will be clobbered — use an unregistered material instead.
- `dispose()` traverses and disposes everything; new resources created outside the scene graph
  (extra render targets, composers, textures) must be disposed manually there.
- Three.js is self-hosted at `/vendor/three` and mapped via an import map — keep addon import paths under
  `three/addons/...` consistent with that map.
- Slow-motion is essential: at real RPM the crank would alias into a strobing blur.
- The header comment in `twin3d.js` describes the design intent; keep it in sync with any behaviour changes.
