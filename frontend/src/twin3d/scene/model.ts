/**
 * Procedural smart-bandage strip: backing film, adhesive, wound pad, three-electrode cell,
 * polyimide PCB with copper traces, and the reader electronics. No asset files.
 */
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { EXPLODE, L, type BuildCtx, type Component, type Frame } from './ctx';
import { hexRingGeometry, merge, mulberry32, noiseTexture, poreTexture, segBox, slabGeometry, TAU, textTexture, weaveTexture } from './util';
import { buildInset } from './inset';
import { buildEffects } from './effects';

export interface BandageModel {
  root: THREE.Group;
  groups: { adh: THREE.Group; pad: THREE.Group; pcb: THREE.Group; elec: THREE.Group; back: THREE.Group; inset: THREE.Group };
  components: Component[];
  setExplode(e: number): void;
  ledLight: THREE.PointLight;
  /** world-space guide lines between the electrode and the macro inset (segments, updated by effects) */
  insetGuide: THREE.LineSegments;
  insetLabels: THREE.Group;
  /** current inset lift (for camera targets) */
  insetCenter(out: THREE.Vector3): THREE.Vector3;
}

interface MkOpts {
  pos?: [number, number, number];
  rot?: [number, number, number];
  cast?: boolean;
  receive?: boolean;
  name?: string;
}

function mk(geo: THREE.BufferGeometry, mat: THREE.Material, parent: THREE.Object3D, o: MkOpts = {}): THREE.Mesh {
  const m = new THREE.Mesh(geo, mat);
  if (o.pos) m.position.set(...o.pos);
  if (o.rot) m.rotation.set(...o.rot);
  m.castShadow = o.cast ?? true;
  m.receiveShadow = o.receive ?? true;
  if (o.name) m.name = o.name;
  parent.add(m);
  return m;
}

function rbox(w: number, h: number, d: number, r: number, seg = 3): THREE.BufferGeometry {
  return new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.001, h / 2 - 0.001, d / 2 - 0.001));
}

export function buildBandage(ctx: BuildCtx, seed = 11): BandageModel {
  const rng = mulberry32(seed);
  const root = ctx.root;
  const gAdh = new THREE.Group();
  const gPad = new THREE.Group();
  const gPcb = new THREE.Group();
  const gElec = new THREE.Group();
  const gBack = new THREE.Group();
  const gInset = new THREE.Group();
  gAdh.name = 'layer-adhesive';
  gPad.name = 'layer-pad-electrodes';
  gPcb.name = 'layer-pcb';
  gElec.name = 'layer-electronics';
  gBack.name = 'layer-backing';
  gInset.name = 'macro-inset';
  root.add(gAdh, gPad, gPcb, gElec, gBack, gInset);
  const components: Component[] = [];
  const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

  /* ---------------------------------------------------------------- adhesive */
  const poreBump = poreTexture(seed + 1);
  poreBump.repeat.set(3, 1.2);
  const adhMat = new THREE.MeshStandardMaterial({ color: 0xe6bd9c, roughness: 0.86, metalness: 0, bumpMap: poreBump, bumpScale: 1.2 });
  ctx.ghost(adhMat, 0.22);
  mk(slabGeometry(L.len, L.wid, L.adhH, 0.55, 0.02), adhMat, gAdh, { name: 'adhesive' });
  // pore pattern: small dark dimples scattered over the adhesive border
  const poreGeo = new THREE.CircleGeometry(0.03, 8);
  poreGeo.rotateX(-Math.PI / 2);
  const poreMat = new THREE.MeshStandardMaterial({ color: 0xa7724f, roughness: 0.95, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
  ctx.ghost(poreMat, 0.22);
  const pores = new THREE.InstancedMesh(poreGeo, poreMat, 900);
  pores.castShadow = false;
  pores.receiveShadow = true;
  const m4 = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const sc = new THREE.Vector3();
  const pv = new THREE.Vector3();
  let np = 0;
  while (np < 900) {
    const x = (rng() - 0.5) * (L.len - 0.5);
    const z = (rng() - 0.5) * (L.wid - 0.5);
    const inPad = x > L.padX0 - 0.1 && x < L.padX1 + 0.1 && Math.abs(z) < L.padHalfZ + 0.05;
    const inPcb = x > L.pcb.x0 - 0.05 && x < L.pcb.x1 + 0.05 && Math.abs(z) < L.pcb.halfZ + 0.05;
    if ((inPad || inPcb) && rng() > 0.15) continue;
    const s = 0.6 + rng() * 0.9;
    m4.compose(pv.set(x, L.adhH + 0.0015, z), q.identity(), sc.set(s, 1, s));
    pores.setMatrixAt(np++, m4);
  }
  gAdh.add(pores);

  /* -------------------------------------------------------------------- pad */
  const padBump = noiseTexture(seed + 2, 128, 48);
  padBump.repeat.set(2, 2);
  const padMat = new THREE.MeshStandardMaterial({ color: 0xf3e9d2, roughness: 0.92, metalness: 0, bumpMap: padBump, bumpScale: 2.2 });
  ctx.ghost(padMat, 0.3);
  const padW = L.padX1 - L.padX0;
  const padCx = (L.padX0 + L.padX1) / 2;
  const padH = L.padTop - L.adhH;
  mk(new RoundedBoxGeometry(padW, padH, L.padHalfZ * 2, 4, 0.1), padMat, gPad, { pos: [padCx, L.adhH + padH / 2, 0], name: 'wound-pad' });
  // fibrous look: many thin strands lying on the pad surface
  const fibreGeo = new THREE.CylinderGeometry(0.0075, 0.0075, 0.4, 4, 1);
  fibreGeo.rotateZ(Math.PI / 2);
  const fibreMat = new THREE.MeshStandardMaterial({ color: 0xfff7e3, roughness: 0.95 });
  ctx.ghost(fibreMat, 0.3);
  const NF = 1500;
  const fibres = new THREE.InstancedMesh(fibreGeo, fibreMat, NF);
  fibres.castShadow = false;
  const col = new THREE.Color();
  for (let i = 0; i < NF; i++) {
    const x = L.padX0 + 0.06 + rng() * (padW - 0.12);
    const z = (rng() - 0.5) * (L.padHalfZ * 2 - 0.12);
    const yaw = rng() * Math.PI;
    q.setFromEuler(new THREE.Euler((rng() - 0.5) * 0.15, yaw, (rng() - 0.5) * 0.12));
    const len = 0.5 + rng() * 1.1;
    m4.compose(pv.set(x, L.padTop + 0.001 + rng() * 0.009, z), q, sc.set(len, 1, 1));
    fibres.setMatrixAt(i, m4);
    const v = 0.86 + rng() * 0.14;
    fibres.setColorAt(i, col.setRGB(v, v * 0.96, v * 0.86));
  }
  gPad.add(fibres);

  /* ------------------------------------------------------------- electrodes */
  const yE = L.padTop;
  const eH = 0.035;
  // working electrode: graphene, dark, faintly iridescent
  const weMat = new THREE.MeshPhysicalMaterial({
    color: 0x17181d,
    metalness: 0.6,
    roughness: 0.35,
    iridescence: 0.6,
    iridescenceIOR: 1.8,
    iridescenceThicknessRange: [180, 520],
    clearcoat: 0.25,
    clearcoatRoughness: 0.4,
  });
  ctx.reg('rct', weMat, 1.0);
  const we = mk(new THREE.CylinderGeometry(L.we.r, L.we.r, eH, 64), weMat, gPad, { pos: [L.we.x, yE + eH / 2, L.we.z], name: 'working-electrode' });
  ctx.pickable(we, 'rct');
  // hex lattice (instanced hexagon rings) + flakes on the graphene surface
  const hexGeo = hexRingGeometry(0.05, 0.035);
  const hexMat = new THREE.MeshStandardMaterial({ color: 0x5c6378, metalness: 0.85, roughness: 0.3 });
  ctx.reg('rct', hexMat, 0.7);
  const hexPts: THREE.Vector3[] = [];
  const R = 0.05 * 1.12;
  for (let cx = -L.we.r; cx < L.we.r; cx += 1.5 * R) {
    const colIdx = Math.round((cx + L.we.r) / (1.5 * R));
    for (let cz = -L.we.r; cz < L.we.r; cz += Math.sqrt(3) * R) {
      const z = cz + (colIdx % 2 ? (Math.sqrt(3) * R) / 2 : 0);
      if (Math.hypot(cx, z) < L.we.r - 0.06) hexPts.push(V(L.we.x + cx, yE + eH + 0.0012, L.we.z + z));
    }
  }
  const hexes = new THREE.InstancedMesh(hexGeo, hexMat, hexPts.length);
  hexes.castShadow = false;
  hexPts.forEach((p, i) => {
    m4.compose(p, q.identity(), sc.set(1, 1, 1));
    hexes.setMatrixAt(i, m4);
  });
  gPad.add(hexes);
  const flakeGeo = new THREE.CylinderGeometry(0.06, 0.06, 0.004, 6);
  const flakeMat = new THREE.MeshPhysicalMaterial({ color: 0x8a92aa, metalness: 0.9, roughness: 0.22, iridescence: 1, iridescenceIOR: 2.0, iridescenceThicknessRange: [250, 700] });
  ctx.reg('rct', flakeMat, 0.5);
  const flakes = new THREE.InstancedMesh(flakeGeo, flakeMat, 60);
  flakes.castShadow = false;
  for (let i = 0; i < 60; i++) {
    const a = rng() * TAU;
    const r = Math.sqrt(rng()) * (L.we.r - 0.08);
    q.setFromEuler(new THREE.Euler((rng() - 0.5) * 0.25, rng() * TAU, (rng() - 0.5) * 0.25));
    const s = 0.5 + rng() * 1.1;
    m4.compose(pv.set(L.we.x + Math.cos(a) * r, yE + eH + 0.004 + rng() * 0.006, L.we.z + Math.sin(a) * r), q, sc.set(s, 1, s));
    flakes.setMatrixAt(i, m4);
  }
  gPad.add(flakes);

  // Ag/AgCl reference electrode
  const reMat = new THREE.MeshStandardMaterial({ color: 0xb9bdc4, metalness: 0.95, roughness: 0.28 });
  mk(new THREE.CylinderGeometry(L.re.r, L.re.r, eH, 40), reMat, gPad, { pos: [L.re.x, yE + eH / 2, L.re.z], name: 'reference-electrode' });
  mk(new THREE.TorusGeometry(L.re.r * 0.9, 0.012, 8, 40), reMat, gPad, { pos: [L.re.x, yE + eH, L.re.z], rot: [Math.PI / 2, 0, 0], cast: false });
  // carbon/graphite counter electrode: annular sector around the WE with a gap toward +x
  const ceShape = new THREE.Shape();
  const a0 = (L.ce.a * Math.PI) / 180;
  ceShape.absarc(0, 0, L.ce.r1, a0, TAU - a0, false);
  ceShape.absarc(0, 0, L.ce.r0, TAU - a0, a0, true);
  ceShape.closePath();
  const ceGeo = new THREE.ExtrudeGeometry(ceShape, { depth: eH - 0.008, bevelEnabled: true, bevelThickness: 0.004, bevelSize: 0.004, bevelSegments: 1, curveSegments: 40 });
  ceGeo.rotateX(-Math.PI / 2);
  ceGeo.translate(0, 0.004, 0);
  const ceMat = new THREE.MeshStandardMaterial({ color: 0x2c2d33, metalness: 0.25, roughness: 0.72 });
  mk(ceGeo, ceMat, gPad, { pos: [L.we.x, yE, L.we.z], name: 'counter-electrode' });

  // gold contact pads + printed silver traces
  const goldMat = new THREE.MeshStandardMaterial({ color: 0xd9a93a, metalness: 1, roughness: 0.25 });
  const goldGeo = rbox(0.3, 0.03, 0.3, 0.03);
  for (const gz of L.goldZ) mk(goldGeo, goldMat, gPad, { pos: [L.goldX, yE + 0.015, gz], cast: false });
  const inkMat = new THREE.MeshStandardMaterial({ color: 0xc4c8cf, metalness: 0.9, roughness: 0.3 });
  const cuMat = new THREE.MeshStandardMaterial({ color: 0xc87533, metalness: 1, roughness: 0.32 });
  const pinZ = [-0.3, 0, 0.3];
  const ceEnd = V(L.we.x + ((L.ce.r0 + L.ce.r1) / 2) * Math.cos(a0), yE + eH, ((L.ce.r0 + L.ce.r1) / 2) * Math.sin(a0));
  const starts = [
    [V(L.re.x + L.re.r - 0.02, yE + eH, L.re.z), V(-0.3, yE + eH, -0.62)], // RE
    [V(L.we.x + L.we.r - 0.03, yE + eH, 0), V(-1.0, yE + eH, 0), V(-0.2, yE + eH, 0)], // WE
    [ceEnd, V(-1.2, yE + eH, 0.52)], // CE
  ];
  const fullPaths: THREE.Vector3[][] = [];
  for (let i = 0; i < 3; i++) {
    const gz = L.goldZ[i];
    const pz = pinZ[i];
    const printed = [
      ...starts[i],
      V(L.goldX, yE + eH, gz),
      V(0.72, yE + eH + 0.003, gz + (pz - gz) * 0.15),
      V(0.84, yE + eH - 0.02, gz + (pz - gz) * 0.4),
      V(0.94, 0.17, gz + (pz - gz) * 0.85),
      V(1.02, L.pcb.y0 + L.pcb.t + 0.015, pz),
    ];
    const cuY = L.pcb.y0 + L.pcb.t + 0.015;
    const copper = [V(1.02, cuY, pz), V(1.14, cuY, pz), V(L.chip.x - 0.48, cuY, pz)];
    const cPrinted = new THREE.CatmullRomCurve3(printed, false, 'centripetal');
    mk(new THREE.TubeGeometry(cPrinted, 90, 0.026, 6, false), inkMat, gPad, { cast: false, name: 'printed-trace' });
    const cCopper = new THREE.CatmullRomCurve3(copper, false, 'catmullrom', 0.2);
    mk(new THREE.TubeGeometry(cCopper, 24, 0.03, 6, false), cuMat, gPcb, { cast: false, name: 'copper-trace' });
    fullPaths.push([...cPrinted.getSpacedPoints(70), ...cCopper.getSpacedPoints(16)]);
  }

  /* ---------------------------------------------- double-layer ring + droplet */
  const cdlMat = new THREE.MeshPhysicalMaterial({ color: 0x2a4fb8, metalness: 0.1, roughness: 0.25, clearcoat: 1, transparent: true, opacity: 0.72 });
  ctx.reg('cdl', cdlMat, 1.4, { base: 0x0a1a44 });
  const ring = mk(new THREE.TorusGeometry(L.we.r + 0.09, 0.05, 14, 72), cdlMat, gPad, {
    pos: [L.we.x, yE + 0.03, L.we.z],
    rot: [Math.PI / 2, 0, 0],
    cast: false,
    name: 'double-layer-ring',
  });
  ring.scale.z = 0.55;
  ctx.pickable(ring, 'cdl');

  const dropGeo = new THREE.SphereGeometry(1, 56, 24, 0, TAU, 0, Math.PI / 2);
  const dropMat = new THREE.MeshPhysicalMaterial({
    color: 0xffffff,
    transmission: 1,
    ior: 1.33,
    thickness: 0.5,
    roughness: 0.05,
    metalness: 0,
    clearcoat: 1,
    clearcoatRoughness: 0.03,
    attenuationColor: new THREE.Color(0x7fe3ff),
    attenuationDistance: 2.5,
    envMapIntensity: 1.2,
  });
  ctx.reg('rs', dropMat, 0.55);
  const drop = mk(dropGeo, dropMat, gPad, { pos: [L.we.x, L.padTop + 0.01, L.we.z], cast: false, name: 'sample-droplet' });
  drop.scale.set(0.001, 0.001, 0.001);
  drop.visible = false;
  ctx.pickable(drop, 'rs');
  components.push({
    update(f: Frame) {
      const s = f.disp.drop;
      drop.visible = s > 0.02;
      const wob = 1 + 0.025 * Math.sin(f.t * 2.1);
      drop.scale.set(s * 1.02, s * 0.38 * wob, s * 1.02);
    },
  });

  /* ------------------------------------------------------------------- PCB */
  const pcbW = L.pcb.x1 - L.pcb.x0;
  const pcbCx = (L.pcb.x0 + L.pcb.x1) / 2;
  const pcbMat = new THREE.MeshPhysicalMaterial({ color: 0xd68f1c, roughness: 0.42, metalness: 0.05, clearcoat: 0.35, clearcoatRoughness: 0.4, emissive: 0x3a1f00, emissiveIntensity: 0.25 });
  ctx.ghost(pcbMat, 0.5);
  mk(slabGeometry(pcbW, L.pcb.halfZ * 2, L.pcb.t, 0.18, 0.006), pcbMat, gPcb, { pos: [pcbCx, L.pcb.y0, 0], name: 'flex-pcb' });
  const pcbTop = L.pcb.y0 + L.pcb.t;
  // decorative copper: power rails, ground pour outlines, vias, fiducials
  const cuGeos: THREE.BufferGeometry[] = [];
  const line = (a: [number, number], b: [number, number], w = 0.04) => cuGeos.push(segBox(V(a[0], pcbTop + 0.006, a[1]), V(b[0], pcbTop + 0.006, b[1]), w, 0.012));
  line([2.2, -0.1], [2.75, -0.1]);
  line([2.75, -0.1], [2.85, 0]);
  line([2.2, 0.1], [2.6, 0.1]);
  line([2.6, 0.1], [2.6, 0.6]);
  line([1.5, 0.48], [1.5, 0.78]);
  line([1.75, 0.5], [1.75, 0.78]);
  line([2.0, 0.5], [2.0, 0.78]);
  line([1.5, -0.48], [1.5, -0.75]);
  line([1.75, -0.48], [1.75, -0.75]);
  line([2.0, -0.48], [2.0, -0.75]);
  line([2.24, -0.35], [2.4, -0.35]);
  line([2.4, -0.35], [2.55, -0.85], 0.035);
  line([2.15, 0.52], [2.15, 0.95]);
  line([2.15, 0.95], [2.9, 0.95]);
  line([2.55, 0.6], [2.55, 0.85], 0.035);
  line([4.05, 0.05], [4.3, 0.05], 0.05);
  const cuBits = new THREE.Mesh(merge(cuGeos), cuMat);
  cuBits.receiveShadow = true;
  gPcb.add(cuBits);
  // fiducials + mounting vias
  const viaGeo = new THREE.CylinderGeometry(0.035, 0.035, 0.008, 14);
  const viaMat = new THREE.MeshStandardMaterial({ color: 0xd9a93a, metalness: 1, roughness: 0.3 });
  const vias = [[1.05, -0.95], [1.05, 0.95], [4.25, -0.95], [4.25, 0.95], [2.9, -0.4], [2.9, 0.4], [1.2, 0.65], [1.2, -0.65]];
  for (const [x, z] of vias) mk(viaGeo, viaMat, gPcb, { pos: [x, pcbTop + 0.004, z], cast: false });

  /* ------------------------------------------------------------ electronics */
  const yB = pcbTop;
  // AFE / potentiostat / impedance analyser QFN
  const chipMat = new THREE.MeshStandardMaterial({ color: 0x131316, metalness: 0.3, roughness: 0.45 });
  ctx.reg('zmag', chipMat, 0.9);
  const chip = mk(rbox(0.9, 0.12, 0.9, 0.03), chipMat, gElec, { pos: [L.chip.x, yB + 0.06, L.chip.z], name: 'afe-chip' });
  ctx.pickable(chip, 'zmag');
  const markTex = textTexture(['AD5941', 'AFE'], { color: '#c9ccd4', dot: true });
  const markMat = new THREE.MeshBasicMaterial({ map: markTex, transparent: true, depthWrite: false, toneMapped: false });
  mk(new THREE.PlaneGeometry(0.72, 0.72), markMat, gElec, { pos: [L.chip.x, yB + 0.1215, L.chip.z], rot: [-Math.PI / 2, 0, 0], cast: false, receive: false });
  // QFN pin pads
  const pinMat = new THREE.MeshStandardMaterial({ color: 0xc9ccd2, metalness: 1, roughness: 0.35 });
  const pinGeo = new THREE.BoxGeometry(0.085, 0.02, 0.05);
  const pins = new THREE.InstancedMesh(pinGeo, pinMat, 32);
  pins.castShadow = false;
  let ip = 0;
  for (let side = 0; side < 4; side++) {
    for (let k = 0; k < 8; k++) {
      const t = -0.35 + k * 0.1;
      const ang = (side * Math.PI) / 2;
      const dx = Math.cos(ang) * 0.475;
      const dz = Math.sin(ang) * 0.475;
      const px = side % 2 === 0 ? dx : t;
      const pz = side % 2 === 0 ? t : dz;
      q.setFromEuler(new THREE.Euler(0, side % 2 === 0 ? 0 : Math.PI / 2, 0));
      m4.compose(pv.set(L.chip.x + px, yB + 0.012, L.chip.z + pz), q, sc.set(1, 1, 1));
      pins.setMatrixAt(ip++, m4);
    }
  }
  gElec.add(pins);

  // passives (0402-style), scaled up for legibility
  const capMat = new THREE.MeshStandardMaterial({ color: 0xb3895a, roughness: 0.55, metalness: 0.05 });
  const resMat = new THREE.MeshStandardMaterial({ color: 0x1b1b1e, roughness: 0.5, metalness: 0.1 });
  const endMat = new THREE.MeshStandardMaterial({ color: 0xcfd2d8, metalness: 1, roughness: 0.3 });
  ctx.reg('phase', capMat, 0.6);
  ctx.reg('phase', resMat, 0.6);
  const bodyGeo = rbox(0.2, 0.06, 0.1, 0.012, 2);
  const capGeo = new THREE.BoxGeometry(0.04, 0.066, 0.104);
  const passives: [number, number, number, boolean][] = [
    [1.5, -0.75, Math.PI / 2, true], [1.75, -0.75, Math.PI / 2, false], [2.0, -0.75, Math.PI / 2, true],
    [1.5, 0.78, Math.PI / 2, false], [1.75, 0.78, Math.PI / 2, true], [2.0, 0.78, Math.PI / 2, false],
    [2.4, 0.35, 0, true], [2.4, -0.3, 0, false], [2.4, 0.0, 0, true],
    [1.15, 0.5, Math.PI / 2, false], [1.15, -0.5, Math.PI / 2, true], [2.65, 0.45, 0, false],
  ];
  const passGroup = new THREE.Group();
  gElec.add(passGroup);
  for (const [x, z, rot, isCap] of passives) {
    const g = new THREE.Group();
    g.position.set(x, yB + 0.03, z);
    g.rotation.y = rot;
    const body = mk(bodyGeo, isCap ? capMat : resMat, g, { cast: true });
    ctx.pickable(body, 'phase');
    mk(capGeo, endMat, g, { pos: [-0.085, 0, 0], cast: false });
    mk(capGeo, endMat, g, { pos: [0.085, 0, 0], cast: false });
    passGroup.add(g);
  }
  // crystal
  const xtalMat = new THREE.MeshStandardMaterial({ color: 0xd7d9dd, metalness: 1, roughness: 0.22 });
  ctx.reg('phase', xtalMat, 0.5);
  const xtal = mk(rbox(0.46, 0.13, 0.2, 0.04), xtalMat, gElec, { pos: [2.55, yB + 0.065, -0.85], name: 'crystal' });
  ctx.pickable(xtal, 'phase');
  mk(new THREE.BoxGeometry(0.05, 0.01, 0.16), goldMat, gElec, { pos: [2.55 - 0.245, yB + 0.005, -0.85], cast: false });
  mk(new THREE.BoxGeometry(0.05, 0.01, 0.16), goldMat, gElec, { pos: [2.55 + 0.245, yB + 0.005, -0.85], cast: false });

  // BLE antenna: meander trace
  const ant: THREE.BufferGeometry[] = [];
  const antPts: THREE.Vector3[] = [V(2.9, pcbTop + 0.007, 0.95)];
  for (let x = 3.05; x < 4.3; x += 0.15) {
    const up = Math.round((x - 3.05) / 0.15) % 2 === 0;
    antPts.push(V(x, pcbTop + 0.007, up ? 0.82 : 1.02));
  }
  for (let i = 0; i < antPts.length - 1; i++) ant.push(segBox(antPts[i], antPts[i + 1], 0.035, 0.012));
  const antMesh = new THREE.Mesh(merge(ant), cuMat);
  gElec.add(antMesh);

  // 3V coin cell (CR2032 look): chamfered steel can, label disc, plastic holder and clips
  const cellMat = new THREE.MeshStandardMaterial({ color: 0xc5c9cf, metalness: 1, roughness: 0.28 });
  ctx.reg('battery', cellMat, 0.35, { base: 0x000000 });
  const cellR = L.cell.r;
  const prof = [
    new THREE.Vector2(0.0, 0.0), new THREE.Vector2(cellR - 0.06, 0.0), new THREE.Vector2(cellR, 0.05),
    new THREE.Vector2(cellR, 0.26), new THREE.Vector2(cellR - 0.05, 0.32), new THREE.Vector2(0.0, 0.32),
  ];
  const cell = mk(new THREE.LatheGeometry(prof, 64), cellMat, gElec, { pos: [L.cell.x, yB, L.cell.z], name: 'coin-cell' });
  ctx.pickable(cell, 'battery');
  const cellLabelMat = new THREE.MeshStandardMaterial({
    map: textTexture(['CR2032', '3V  +'], { color: '#1a1c22', size: 256 }),
    transparent: true,
    roughness: 0.4,
    metalness: 0.7,
    depthWrite: false,
  });
  ctx.reg('battery', cellLabelMat, 0.25);
  const cellLabel = mk(new THREE.CircleGeometry(cellR - 0.13, 48), cellLabelMat, gElec, { pos: [L.cell.x, yB + 0.3215, L.cell.z], rot: [-Math.PI / 2, 0, 0], cast: false });
  ctx.pickable(cellLabel, 'battery');
  const holderMat = new THREE.MeshStandardMaterial({ color: 0x1b1d22, roughness: 0.55, metalness: 0.1 });
  mk(new THREE.TorusGeometry(cellR + 0.05, 0.05, 12, 64), holderMat, gElec, { pos: [L.cell.x, yB + 0.06, L.cell.z], rot: [Math.PI / 2, 0, 0] });
  mk(new THREE.TorusGeometry(cellR + 0.05, 0.035, 10, 64), holderMat, gElec, { pos: [L.cell.x, yB + 0.24, L.cell.z], rot: [Math.PI / 2, 0, 0], cast: false });
  for (const sx of [-1, 1]) {
    mk(rbox(0.16, 0.035, 0.4, 0.015, 2), endMat, gElec, { pos: [L.cell.x + sx * (cellR - 0.02), yB + 0.33, 0], name: 'cell-clip' });
    mk(rbox(0.05, 0.36, 0.4, 0.015, 2), endMat, gElec, { pos: [L.cell.x + sx * (cellR + 0.1), yB + 0.18, 0], cast: false });
  }
  // smoked rounded cover over the cell
  const coverMat = new THREE.MeshPhysicalMaterial({ color: 0x8d97a8, roughness: 0.15, metalness: 0, clearcoat: 1, transparent: true, opacity: 0.12, depthWrite: false });
  const cover = new THREE.SphereGeometry(cellR + 0.02, 40, 12, 0, TAU, 0, Math.PI / 2);
  const coverMesh = mk(cover, coverMat, gElec, { pos: [L.cell.x, yB + 0.3, L.cell.z], cast: false });
  coverMesh.scale.set(1, 0.12, 1);

  // status LED
  const ledBodyMat = new THREE.MeshPhysicalMaterial({ color: 0xdfe7ee, roughness: 0.15, clearcoat: 1, transparent: true, opacity: 0.6, depthWrite: false });
  ctx.reg('deltaZ', ledBodyMat, 0.35);
  const ledBody = mk(rbox(0.22, 0.1, 0.16, 0.03), ledBodyMat, gElec, { pos: [2.55, yB + 0.05, 0.85], name: 'status-led' });
  ctx.pickable(ledBody, 'deltaZ');
  const ledCoreMat = new THREE.MeshStandardMaterial({ color: 0x0a2016, emissive: 0x34d399, emissiveIntensity: 1, roughness: 0.3 });
  ctx.reg('deltaZ', ledCoreMat, 1.7);
  const ledCore = mk(new THREE.SphereGeometry(0.058, 20, 12, 0, TAU, 0, Math.PI / 2), ledCoreMat, gElec, { pos: [2.55, yB + 0.06, 0.85], cast: false });
  ctx.pickable(ledCore, 'deltaZ');
  const ledLight = new THREE.PointLight(0x34d399, 0.2, 2.2, 2);
  ledLight.position.set(2.55, yB + 0.3, 0.85);
  gElec.add(ledLight);

  /* ---------------------------------------------------------------- backing */
  const weave = weaveTexture();
  weave.repeat.set(7, 7);
  const backMat = new THREE.MeshPhysicalMaterial({
    color: 0xe9f2f8,
    roughness: 0.22,
    metalness: 0,
    clearcoat: 0.9,
    clearcoatRoughness: 0.15,
    transparent: true,
    opacity: 0.26,
    depthWrite: false,
    bumpMap: weave,
    bumpScale: 0.35,
    ior: 1.45,
  });
  ctx.ghost(backMat, 0.07);
  mk(slabGeometry(L.len, L.wid, 0.05, 0.55, 0.016), backMat, gBack, { pos: [0, L.backY, 0], cast: false, name: 'backing-film' });
  // breathable micro-perforations
  const perfGeo = new THREE.CircleGeometry(0.024, 8);
  perfGeo.rotateX(-Math.PI / 2);
  const perfMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.22, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
  ctx.ghost(perfMat, 0.06);
  const perfPts: THREE.Vector3[] = [];
  for (let x = -L.len / 2 + 0.35, r = 0; x < L.len / 2 - 0.3; x += 0.15, r++) {
    for (let z = -L.wid / 2 + 0.3 + (r % 2 ? 0.065 : 0); z < L.wid / 2 - 0.25; z += 0.13) perfPts.push(V(x, L.backY + 0.0516, z));
  }
  const perf = new THREE.InstancedMesh(perfGeo, perfMat, perfPts.length);
  perf.castShadow = false;
  perf.receiveShadow = false;
  perfPts.forEach((p, i) => {
    m4.compose(p, q.identity(), sc.set(1, 1, 1));
    perf.setMatrixAt(i, m4);
  });
  gBack.add(perf);

  /* ---------------------------------------------------------------- anchors */
  ctx.anchor('rs', gPad, V(L.we.x + 0.45, L.padTop + 0.36, L.we.z - 0.35), V(0, 1, -0.2));
  ctx.anchor('rct', gPad, V(L.we.x - 0.15, yE + 0.06, L.we.z + 0.1), V(0, 1, 0));
  ctx.anchor('cdl', gPad, V(L.we.x + 0.05, yE + 0.08, L.we.z + L.we.r + 0.2), V(0, 0.6, 0.8));
  ctx.anchor('zmag', gElec, V(L.chip.x, yB + 0.14, L.chip.z), V(0, 1, 0));
  ctx.anchor('phase', gElec, V(2.55, yB + 0.14, -0.85), V(0, 0.7, -0.7));
  ctx.anchor('deltaZ', gElec, V(2.55, yB + 0.14, 0.85), V(0, 0.8, 0.6));
  ctx.anchor('battery', gElec, V(L.cell.x, yB + 0.36, L.cell.z), V(0, 1, 0));

  /* ------------------------------------------------------- inset + effects */
  const inset = buildInset(ctx, gInset, seed + 5);
  components.push(inset);
  const fx = buildEffects(ctx, root, { paths: fullPaths, insetGroup: gInset, padGroup: gPad });
  components.push(fx.component);

  function setExplode(e: number): void {
    gAdh.position.y = EXPLODE.adh * e;
    gPad.position.y = EXPLODE.pad * e;
    gPcb.position.y = EXPLODE.pcb * e;
    gElec.position.y = EXPLODE.elec * e;
    gBack.position.y = EXPLODE.back * e;
    gInset.position.set(L.we.x, L.insetY + EXPLODE.insetLift * e, L.we.z);
  }
  setExplode(0);

  return {
    root,
    groups: { adh: gAdh, pad: gPad, pcb: gPcb, elec: gElec, back: gBack, inset: gInset },
    components,
    setExplode,
    ledLight,
    insetGuide: fx.guide,
    insetLabels: inset.labels,
    insetCenter: (out) => out.set(gInset.position.x, gInset.position.y + 0.7, gInset.position.z),
  };
}

