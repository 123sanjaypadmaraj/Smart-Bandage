/**
 * Macro "zoom-in" inset floating above the working electrode:
 * graphene sheet -> PBASE linker -> aptamer strands -> E. coli (bound + free) + Warburg diffusion.
 * Local origin = centre of the substrate top surface.
 */
import * as THREE from 'three';
import { MAX_BOUND, type BuildCtx, type Component, type Frame } from './ctx';
import { clamp, fadePointsMaterial, glowTexture, hexRingGeometry, labelSprite, merge, mulberry32, noiseTexture, setGlowTheme, TAU } from './util';

export interface Inset extends Component {
  labels: THREE.Group;
}

const N_APT = 120;
const N_VARIANTS = 8;
const N_FREE = 30;
const N_WARB = 240;
const APT_BASE_Y = 0.13;
const R_IN = 1.5;

function bacteriumGeometry(): THREE.BufferGeometry {
  // 1 : 2.2 width/length ratio: r = 0.1, total length = 0.44
  const g = new THREE.CapsuleGeometry(0.1, 0.24, 6, 16);
  g.rotateZ(Math.PI / 2);
  return g;
}

function flagellaGeometry(rng: () => number): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];
  const n = 7;
  for (let i = 0; i < n; i++) {
    const along = -0.2 + (i / (n - 1)) * 0.4;
    const ang = rng() * TAU;
    const dir = new THREE.Vector3(i === 0 ? -1 : (rng() - 0.5) * 0.6, Math.sin(ang), Math.cos(ang)).normalize();
    const start = new THREE.Vector3(i === 0 ? -0.22 : along, Math.sin(ang) * 0.098, Math.cos(ang) * 0.098);
    const perp = new THREE.Vector3(0, -dir.z, dir.y).normalize();
    const len = i === 0 ? 0.6 : 0.28 + rng() * 0.18;
    const pts: THREE.Vector3[] = [];
    for (let k = 0; k <= 8; k++) {
      const t = k / 8;
      pts.push(start.clone().addScaledVector(dir, t * len).addScaledVector(perp, Math.sin(t * 9 + i) * 0.028 * t));
    }
    parts.push(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 18, 0.0055, 4, false));
  }
  return merge(parts);
}

function aptamerGeometry(variant: number, rng: () => number): { geo: THREE.BufferGeometry; height: number } {
  const H = 0.48 + 0.03 * variant;
  const turns = 1.1 + rng() * 1.3;
  const wob = 0.05 + rng() * 0.07;
  const phi = rng() * TAU;
  const pts: THREE.Vector3[] = [];
  const n = 13;
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const r = wob * Math.sin(t * Math.PI) * (0.5 + 0.6 * Math.sin(t * 5 + variant));
    const a = phi + t * turns * TAU;
    pts.push(new THREE.Vector3(r * Math.cos(a), H * t, r * Math.sin(a)));
  }
  return { geo: new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 44, 0.014, 5, false), height: H };
}

export function buildInset(ctx: BuildCtx, group: THREE.Group, seed: number): Inset {
  const rng = mulberry32(seed);
  const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const m4 = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const e = new THREE.Euler();
  const sc = new THREE.Vector3();
  const pv = new THREE.Vector3();

  /* substrate (graphene-coated working electrode, magnified) */
  const subMat = new THREE.MeshPhysicalMaterial({ color: 0x15161b, metalness: 0.6, roughness: 0.35, iridescence: 0.6, iridescenceIOR: 1.8, iridescenceThicknessRange: [180, 520], clearcoat: 0.25 });
  ctx.reg('rct', subMat, 0.5);
  const sub = new THREE.Mesh(new THREE.CylinderGeometry(R_IN, R_IN + 0.05, 0.12, 72), subMat);
  sub.position.y = -0.06;
  sub.receiveShadow = true;
  group.add(sub);
  ctx.pickable(sub, 'rct');

  // double-layer rim
  const rimMat = new THREE.MeshPhysicalMaterial({ color: 0x2a4fb8, roughness: 0.25, clearcoat: 1, transparent: true, opacity: 0.75 });
  ctx.reg('cdl', rimMat, 1.4, { base: 0x0a1a44 });
  const rim = new THREE.Mesh(new THREE.TorusGeometry(R_IN + 0.02, 0.04, 12, 96), rimMat);
  rim.rotation.x = Math.PI / 2;
  group.add(rim);
  ctx.pickable(rim, 'cdl');

  /* graphene hexagon lattice */
  const Rh = 0.09;
  const hexGeo = hexRingGeometry(Rh, Rh * 0.72);
  const hexMat = new THREE.MeshStandardMaterial({ color: 0x4a5168, metalness: 0.85, roughness: 0.3, side: THREE.DoubleSide });
  ctx.reg('rct', hexMat, 0.6);
  const hp: THREE.Vector3[] = [];
  const sx = 1.5 * Rh * 1.08;
  const sz = Math.sqrt(3) * Rh * 1.08;
  for (let cx = -R_IN; cx < R_IN; cx += sx) {
    const ci = Math.round((cx + R_IN) / sx);
    for (let cz = -R_IN; cz < R_IN; cz += sz) {
      const z = cz + (ci % 2 ? sz / 2 : 0);
      if (Math.hypot(cx, z) < R_IN - 0.1) hp.push(V(cx, 0.004, z));
    }
  }
  const hexes = new THREE.InstancedMesh(hexGeo, hexMat, hp.length);
  hp.forEach((p, i) => {
    m4.compose(p, q.identity(), sc.set(1, 1, 1));
    hexes.setMatrixAt(i, m4);
  });
  hexes.receiveShadow = true;
  group.add(hexes);

  /* site layout: first MAX_BOUND sites are bacterium slots, remaining are extra strands */
  const sites: { x: number; z: number; v: number }[] = [];
  const tryPlace = (rMax: number, minD: number): boolean => {
    for (let tries = 0; tries < 60; tries++) {
      const a = rng() * TAU;
      const r = Math.sqrt(rng()) * rMax;
      const x = Math.cos(a) * r;
      const z = Math.sin(a) * r;
      if (sites.every((s) => Math.hypot(s.x - x, s.z - z) >= minD)) {
        sites.push({ x, z, v: sites.length % N_VARIANTS });
        return true;
      }
    }
    return false;
  };
  while (sites.length < MAX_BOUND) if (!tryPlace(1.22, 0.4)) break;
  const nSlots = sites.length;
  while (sites.length < N_APT) if (!tryPlace(1.34, 0.13)) break;

  /* PBASE linkers: pyrene ring clusters lying on graphene + short upright linker */
  const pyGeo = hexRingGeometry(0.038, 0.026);
  const pbaseMat = new THREE.MeshStandardMaterial({ color: 0xff9f43, emissive: 0x7a3a00, emissiveIntensity: 0.5, roughness: 0.45, metalness: 0.1, side: THREE.DoubleSide });
  const pyr = new THREE.InstancedMesh(pyGeo, pbaseMat, sites.length * 4);
  const stubGeo = new THREE.CylinderGeometry(0.014, 0.014, 0.1, 6);
  stubGeo.translate(0, 0.08, 0);
  const stubs = new THREE.InstancedMesh(stubGeo, pbaseMat, sites.length);
  const off = [[-0.036, 0], [0.036, 0], [0, -0.063], [0, 0.063]];
  sites.forEach((s, i) => {
    const yaw = (i * 1.7) % TAU;
    q.setFromEuler(e.set(0, yaw, 0));
    for (let k = 0; k < 4; k++) {
      const c = Math.cos(yaw);
      const sn = Math.sin(yaw);
      m4.compose(pv.set(s.x + off[k][0] * c - off[k][1] * sn, 0.02, s.z + off[k][0] * sn + off[k][1] * c), q, sc.set(1, 1, 1));
      pyr.setMatrixAt(i * 4 + k, m4);
    }
    m4.compose(pv.set(s.x, 0.02, s.z), q.identity(), sc.set(1, 1, 1));
    stubs.setMatrixAt(i, m4);
  });
  group.add(pyr, stubs);

  /* aptamer strands: N_VARIANTS shared tube geometries, instanced with seeded variation */
  const aptMat = new THREE.MeshStandardMaterial({ color: 0x2dd4bf, emissive: 0x0b5f58, emissiveIntensity: 1, roughness: 0.35, metalness: 0.05 });
  ctx.reg('coverage', aptMat, 0.4, { base: 0x0b5f58 });
  const heights: number[] = [];
  const aptMeshes: THREE.InstancedMesh[] = [];
  for (let v = 0; v < N_VARIANTS; v++) {
    const { geo, height } = aptamerGeometry(v, rng);
    heights.push(height);
    const mine = sites.map((s, i) => ({ s, i })).filter((o) => o.s.v === v);
    const im = new THREE.InstancedMesh(geo, aptMat, Math.max(1, mine.length));
    im.count = mine.length;
    mine.forEach((o, k) => {
      const yaw = rng() * TAU;
      // slot strands keep scale 1 so the bacterium sits exactly on the tip
      const s = o.i < nSlots ? 1 : 0.85 + rng() * 0.3;
      q.setFromEuler(e.set(0, yaw, 0));
      m4.compose(pv.set(o.s.x, APT_BASE_Y, o.s.z), q, sc.set(1, s, 1));
      im.setMatrixAt(k, m4);
    });
    im.castShadow = true;
    group.add(im);
    aptMeshes.push(im);
    ctx.pickable(im, 'coverage');
  }

  /* E. coli: shared body + flagella geometry */
  const bodyGeo = bacteriumGeometry();
  const flagGeo = flagellaGeometry(rng);
  const skin = noiseTexture(seed + 9, 64, 24);
  skin.repeat.set(2, 2);
  const bactMat = new THREE.MeshPhysicalMaterial({ color: 0xb8d43a, roughness: 0.5, metalness: 0, sheen: 0.6, sheenColor: new THREE.Color(0xf0ff90), bumpMap: skin, bumpScale: 1.4, emissive: 0x1d2b00 });
  ctx.reg('coverage', bactMat, 0.25, { base: 0x1d2b00 });
  const flagMat = new THREE.MeshStandardMaterial({ color: 0xd9e37f, roughness: 0.6 });
  const freeMat = new THREE.MeshPhysicalMaterial({ color: 0xe4dc4a, roughness: 0.5, sheen: 0.6, sheenColor: new THREE.Color(0xfff6a0), bumpMap: skin, bumpScale: 1.2, emissive: 0x2b2600, emissiveIntensity: 0.6 });

  const bound = new THREE.InstancedMesh(bodyGeo, bactMat, MAX_BOUND);
  const boundFlag = new THREE.InstancedMesh(flagGeo, flagMat, MAX_BOUND);
  bound.castShadow = true;
  bound.count = 0;
  boundFlag.count = 0;
  // instance counts/matrices change every frame: never let three cache a stale (empty) bounds
  bound.frustumCulled = false;
  boundFlag.frustumCulled = false;
  bound.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0.6, 0), 2.2);
  const cc = new THREE.Color();
  const slots = sites.slice(0, nSlots).map((s) => ({
    x: s.x,
    z: s.z,
    y: APT_BASE_Y + heights[s.v] + 0.085,
    yaw: rng() * TAU,
    tilt: (rng() - 0.5) * 0.35,
    ph: rng() * TAU,
  }));
  slots.forEach((_, i) => {
    cc.setRGB(0.68 + rng() * 0.12, 0.8 + rng() * 0.1, 0.18 + rng() * 0.12);
    bound.setColorAt(i, cc);
  });
  group.add(bound, boundFlag);
  ctx.pickable(bound, 'coverage', 2);

  const free = new THREE.InstancedMesh(bodyGeo, freeMat, N_FREE);
  const freeFlag = new THREE.InstancedMesh(flagGeo, flagMat, N_FREE);
  free.count = 0;
  freeFlag.count = 0;
  free.frustumCulled = false;
  freeFlag.frustumCulled = false;
  const freeP = Array.from({ length: N_FREE }, () => ({
    x: (rng() - 0.5) * 2.1,
    z: (rng() - 0.5) * 2.1,
    ph: rng(),
    sp: 0.4 + rng() * 0.5,
    r: rng() * TAU,
  }));
  freeP.forEach((p, i) => {
    // keep inside the disc
    const d = Math.hypot(p.x, p.z);
    if (d > 1.2) {
      p.x *= 1.2 / d;
      p.z *= 1.2 / d;
    }
    cc.setRGB(0.85 + rng() * 0.1, 0.8 + rng() * 0.1, 0.22 + rng() * 0.15);
    free.setColorAt(i, cc);
  });
  group.add(free, freeFlag);

  /* Warburg diffusion particles + halo + glass dome */
  const warbGeo = new THREE.BufferGeometry();
  const wPos = new Float32Array(N_WARB * 3);
  const wFade = new Float32Array(N_WARB);
  warbGeo.setAttribute('position', new THREE.BufferAttribute(wPos, 3));
  warbGeo.setAttribute('aFade', new THREE.BufferAttribute(wFade, 1));
  const glow = glowTexture();
  const warbMat = fadePointsMaterial(glow, 0.11, 0xa78bfa);
  const warb = new THREE.Points(warbGeo, warbMat);
  warb.frustumCulled = false;
  group.add(warb);
  const wp = Array.from({ length: N_WARB }, () => ({
    x: (rng() - 0.5) * 2.6,
    z: (rng() - 0.5) * 2.6,
    u: rng(),
    sp: 0.06 + rng() * 0.08,
    ph: rng() * TAU,
  }));
  wp.forEach((p) => {
    const d = Math.hypot(p.x, p.z);
    if (d > 1.35) {
      p.x *= 1.35 / d;
      p.z *= 1.35 / d;
    }
  });
  const haloMat = new THREE.MeshBasicMaterial({ color: 0x8b5cf6, map: glow, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  ctx.reg('warburg', haloMat, 0.6, { mode: 'opacity' });
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(R_IN * 2.4, R_IN * 2.4), haloMat);
  halo.rotation.x = -Math.PI / 2;
  halo.position.y = 0.05;
  group.add(halo);
  const domeMat = new THREE.MeshPhysicalMaterial({ color: 0x8fb2ff, roughness: 0.1, clearcoat: 1, transparent: true, opacity: 0.05, depthWrite: false });
  ctx.reg('warburg', domeMat, 0.08, { mode: 'opacity' });
  const dome = new THREE.Mesh(new THREE.SphereGeometry(R_IN + 0.06, 48, 20, 0, TAU, 0, Math.PI / 2), domeMat);
  dome.scale.y = 1.35;
  group.add(dome);
  ctx.pickable(dome, 'warburg', 0);

  /* labels */
  const labels = new THREE.Group();
  const lab = (t: string, y: number) => {
    const s = labelSprite(t, 0.24);
    s.position.set(R_IN + 1.0, y, 0.2);
    labels.add(s);
  };
  lab('Graphene', -0.14);
  lab('PBASE linker', 0.16);
  lab('Aptamers', 0.5);
  lab('E. coli', 0.92);
  group.add(labels);

  /* anchors (parent = inset group so they follow explode) */
  ctx.anchor('coverage', group, V(0.35, 0.98, 0.55), V(0.2, 1, 0.5));
  ctx.anchor('warburg', group, V(-0.9, 1.4, -0.1), V(0, 0.6, 0.8));

  const dummy = new THREE.Object3D();
  let lastLight: boolean | null = null;

  return {
    labels,
    update(f: Frame) {
      if (f.light !== lastLight) {
        lastLight = f.light;
        setGlowTheme(warbMat, f.light, 0xa78bfa, 0x5b2fd6);
        setGlowTheme(haloMat, f.light, 0x8b5cf6, 0x6a3fe0);
      }
      const t = f.t;
      const amp = f.reduced ? 0 : 1;
      // bound bacteria: number visible = round(coverage * MAX_BOUND), smoothly popped in
      const boundF = clamp(f.disp.coverage, 0, 1) * MAX_BOUND;
      // below ~0.15 of one cell nothing is drawn, so a clean control (coverage -> 0) shows no bound bacteria
      const shown = boundF < 0.15 ? 0 : Math.min(nSlots, Math.ceil(boundF - 0.001));
      bound.count = shown;
      boundFlag.count = shown;
      for (let i = 0; i < shown; i++) {
        const s = slots[i];
        const pop = clamp(boundF - i, 0, 1);
        const w = amp * Math.sin(t * 2.3 + s.ph);
        dummy.position.set(s.x + 0.012 * w, s.y + 0.008 * Math.sin(t * 1.7 + s.ph * 2) * amp, s.z + 0.012 * Math.cos(t * 2.1 + s.ph));
        dummy.rotation.set(s.tilt * 0.6 + 0.05 * w, s.yaw + 0.06 * w, s.tilt * 0.3 + 0.04 * Math.cos(t * 1.9 + s.ph));
        dummy.scale.setScalar(0.2 + 0.8 * pop);
        dummy.updateMatrix();
        bound.setMatrixAt(i, dummy.matrix);
        boundFlag.setMatrixAt(i, dummy.matrix);
      }
      bound.instanceMatrix.needsUpdate = true;
      boundFlag.instanceMatrix.needsUpdate = true;

      // free bacteria drifting down through the sample toward the aptamer layer
      const cfu = f.state.cfuPerMl;
      const conc = cfu > 0 ? clamp(Math.log10(1 + cfu) / 5, 0.3, 1) : 0;
      const freeF = clamp(f.disp.free, 0, 1) * conc * N_FREE;
      const nFree = freeF < 0.15 ? 0 : Math.min(N_FREE, Math.ceil(freeF - 0.001));
      free.count = nFree;
      freeFlag.count = nFree;
      for (let i = 0; i < nFree; i++) {
        const p = freeP[i];
        const u = (t * 0.05 * p.sp + p.ph) % 1;
        const fade = clamp(Math.min(u * 6, (1 - u) * 6), 0, 1);
        const pop = clamp(freeF - i, 0, 1);
        dummy.position.set(p.x + 0.1 * Math.sin(t * 0.7 * p.sp + p.r), 1.75 - u * 1.05, p.z + 0.1 * Math.cos(t * 0.6 * p.sp + p.r * 2));
        dummy.rotation.set(t * 0.4 * p.sp + p.r, t * 0.3 * p.sp + p.r * 2, t * 0.5 * p.sp);
        dummy.scale.setScalar((0.2 + 0.8 * pop) * (0.3 + 0.7 * fade));
        dummy.updateMatrix();
        free.setMatrixAt(i, dummy.matrix);
        freeFlag.setMatrixAt(i, dummy.matrix);
      }
      free.instanceMatrix.needsUpdate = true;
      freeFlag.instanceMatrix.needsUpdate = true;

      // warburg: molecules diffusing toward the electrode surface; density follows sigma ratio
      const dens = clamp(0.3 + (f.sigmaRatio - 1) * 1.2, 0.2, 1);
      const nW = Math.floor(N_WARB * dens);
      warb.visible = f.live && f.flow;
      for (let i = 0; i < N_WARB; i++) {
        const p = wp[i];
        const u = (t * p.sp + p.ph / TAU) % 1;
        const active = i < nW ? 1 : 0;
        const fade = Math.sin(Math.PI * u) * active;
        const y = 1.7 - Math.pow(u, 0.85) * 1.55;
        wPos[i * 3] = p.x + 0.07 * Math.sin(t * 0.9 + p.ph * 3) * (1 - u * 0.5);
        wPos[i * 3 + 1] = y;
        wPos[i * 3 + 2] = p.z + 0.07 * Math.cos(t * 0.8 + p.ph * 2) * (1 - u * 0.5);
        wFade[i] = fade;
      }
      warbGeo.attributes.position.needsUpdate = true;
      warbGeo.attributes.aFade.needsUpdate = true;
    },
  };
}
