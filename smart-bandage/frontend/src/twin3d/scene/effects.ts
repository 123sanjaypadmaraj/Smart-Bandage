/**
 * Animated overlays: AC excitation ribbon, current-response dots on the trace splines,
 * sample-droplet bacteria, Warburg halo + diffusing particles at the electrode,
 * and the guide lines linking the electrode to the macro inset.
 */
import * as THREE from 'three';
import { L, type BuildCtx, type Component, type Frame } from './ctx';
import { clamp, fadePointsMaterial, glowTexture, mulberry32, setGlowTheme, TAU } from './util';

export interface Effects {
  component: Component;
  guide: THREE.LineSegments;
}

const RIB_N = 180;
const RIB_R = 0.02;
const DROP_PTS = 70;
const MAIN_WARB = 70;
const DOTS_PER_PATH = 40;

export function buildEffects(
  ctx: BuildCtx,
  root: THREE.Group,
  o: { paths: THREE.Vector3[][]; insetGroup: THREE.Group; padGroup: THREE.Group },
): Effects {
  const rng = mulberry32(2024);
  const glow = glowTexture();

  /* ------------------------------------------------ AC excitation ribbon */
  const ribPos = new Float32Array(RIB_N * 4 * 3);
  const ribGeo = new THREE.BufferGeometry();
  ribGeo.setAttribute('position', new THREE.BufferAttribute(ribPos, 3));
  const idx: number[] = [];
  for (let i = 0; i < RIB_N - 1; i++) {
    for (let k = 0; k < 4; k++) {
      const a = i * 4 + k;
      const b = i * 4 + ((k + 1) % 4);
      idx.push(a, b, a + 4, b, b + 4, a + 4);
    }
  }
  ribGeo.setIndex(idx);
  const ribMat = new THREE.MeshBasicMaterial({ color: 0x7ee7ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false, side: THREE.DoubleSide });
  const ribbon = new THREE.Mesh(ribGeo, ribMat);
  ribbon.frustumCulled = false;
  ribbon.visible = false;
  ribbon.name = 'ac-excitation';
  root.add(ribbon);
  const RX0 = L.chip.x - 0.5;
  const RX1 = L.we.x + 0.15;
  const RY = 0.5;

  /* --------------------------------------------- current-response dots */
  const nDots = o.paths.length * DOTS_PER_PATH;
  const dotGeo = new THREE.BufferGeometry();
  const dPos = new Float32Array(nDots * 3);
  const dFade = new Float32Array(nDots);
  dotGeo.setAttribute('position', new THREE.BufferAttribute(dPos, 3));
  dotGeo.setAttribute('aFade', new THREE.BufferAttribute(dFade, 1));
  const dotMat = fadePointsMaterial(glow, 0.2, 0xff9f1a);
  const dots = new THREE.Points(dotGeo, dotMat);
  dots.frustumCulled = false;
  dots.visible = false;
  root.add(dots);
  const dotOff = Array.from({ length: nDots }, () => rng());

  /* ------------------------------------- droplet bacteria (additive points) */
  const bGeo = new THREE.BufferGeometry();
  const bPos = new Float32Array(DROP_PTS * 3);
  const bFade = new Float32Array(DROP_PTS);
  bGeo.setAttribute('position', new THREE.BufferAttribute(bPos, 3));
  bGeo.setAttribute('aFade', new THREE.BufferAttribute(bFade, 1));
  const bMat = fadePointsMaterial(glow, 0.075, 0x9acd32);
  const bPts = new THREE.Points(bGeo, bMat);
  bPts.frustumCulled = false;
  o.padGroup.add(bPts);
  const bSeed = Array.from({ length: DROP_PTS }, () => ({ a: rng() * TAU, r: Math.sqrt(rng()), h: rng(), ph: rng() * TAU, sp: 0.3 + rng() * 0.7 }));

  /* ------------------------ main-scene Warburg halo + diffusing particles */
  const haloMat = new THREE.MeshBasicMaterial({ color: 0x8b5cf6, map: glow, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  ctx.reg('warburg', haloMat, 0.55, { mode: 'opacity' });
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(2.8, 2.8), haloMat);
  halo.rotation.x = -Math.PI / 2;
  halo.position.set(L.we.x, L.padTop + 0.045, L.we.z);
  o.padGroup.add(halo);
  const wGeo = new THREE.BufferGeometry();
  const wPos = new Float32Array(MAIN_WARB * 3);
  const wFade = new Float32Array(MAIN_WARB);
  wGeo.setAttribute('position', new THREE.BufferAttribute(wPos, 3));
  wGeo.setAttribute('aFade', new THREE.BufferAttribute(wFade, 1));
  const wMat = fadePointsMaterial(glow, 0.09, 0xa78bfa);
  const wPts = new THREE.Points(wGeo, wMat);
  wPts.frustumCulled = false;
  o.padGroup.add(wPts);
  const wSeed = Array.from({ length: MAIN_WARB }, () => ({ a: rng() * TAU, r: 0.2 + Math.sqrt(rng()) * 0.85, ph: rng(), sp: 0.15 + rng() * 0.2 }));

  /* ----------------------------------------------------------- guide lines */
  const gPos = new Float32Array(8 * 2 * 3);
  const gGeo = new THREE.BufferGeometry();
  gGeo.setAttribute('position', new THREE.BufferAttribute(gPos, 3));
  const guide = new THREE.LineSegments(gGeo, new THREE.LineBasicMaterial({ color: 0x7aa2ff, transparent: true, opacity: 0.28, depthWrite: false }));
  guide.frustumCulled = false;
  root.add(guide);

  /* ----------------------------------------------------------------- state */
  let lastLight: boolean | null = null;
  const applyTheme = (light: boolean) => {
    setGlowTheme(ribMat, light, 0x7ee7ff, 0x0b6fb8);
    setGlowTheme(dotMat, light, 0xff9f1a, 0xd04a00);
    setGlowTheme(bMat, light, 0x9acd32, 0x4a7c00);
    setGlowTheme(wMat, light, 0xa78bfa, 0x5b2fd6);
    setGlowTheme(haloMat, light, 0x8b5cf6, 0x6a3fe0);
    (guide.material as THREE.LineBasicMaterial).color.setHex(light ? 0x2f5fd0 : 0x7aa2ff);
    (guide.material as THREE.LineBasicMaterial).opacity = light ? 0.45 : 0.28;
  };
  let ribAct = 0;
  let dotAct = 0;
  let wave = 0;
  let dotPhase = 0;

  const component: Component = {
    update(f: Frame) {
      if (f.light !== lastLight) {
        lastLight = f.light;
        applyTheme(f.light);
      }
      const away = f.explode > 0.05;
      const on = f.live && !away;

      /* excitation ribbon: chirp -- cycle count and speed rise with log-frequency sweep progress */
      const ribTarget = f.measuring && on ? 0.9 : 0;
      ribAct += (ribTarget - ribAct) * (1 - Math.exp(-f.dt / 0.25));
      ribMat.opacity = ribAct;
      ribbon.visible = ribAct > 0.02;
      if (f.live) wave += f.dt * (4 + 12 * f.sweep);
      if (ribbon.visible) {
        const cyc = 1.5 + 10 * f.sweep;
        for (let i = 0; i < RIB_N; i++) {
          const u = i / (RIB_N - 1);
          const env = Math.pow(Math.sin(Math.PI * u), 0.6);
          const ph = TAU * cyc * u - wave;
          const cx = RX0 + (RX1 - RX0) * u;
          const cy = RY - 0.1 * u * u + 0.13 * env * Math.sin(ph);
          const cz = 0.03 * env * Math.cos(ph);
          const j = i * 12;
          ribPos[j] = cx; ribPos[j + 1] = cy + RIB_R; ribPos[j + 2] = cz;
          ribPos[j + 3] = cx; ribPos[j + 4] = cy; ribPos[j + 5] = cz + RIB_R;
          ribPos[j + 6] = cx; ribPos[j + 7] = cy - RIB_R; ribPos[j + 8] = cz;
          ribPos[j + 9] = cx; ribPos[j + 10] = cy; ribPos[j + 11] = cz - RIB_R;
        }
        ribGeo.attributes.position.needsUpdate = true;
      }

      /* current response dots travelling electrode -> AFE */
      const dotTarget = f.measuring && on && f.flow ? 1 : 0;
      dotAct += (dotTarget - dotAct) * (1 - Math.exp(-f.dt / 0.25));
      dots.visible = dotAct > 0.02;
      if (f.live) dotPhase += f.dt * (0.1 + 0.4 * f.sweep);
      if (dots.visible) {
        let n = 0;
        for (let pi = 0; pi < o.paths.length; pi++) {
          const path = o.paths[pi];
          for (let k = 0; k < DOTS_PER_PATH; k++) {
            const gi = pi * DOTS_PER_PATH + k;
            const u = (k / DOTS_PER_PATH + dotOff[gi] * 0.02 + dotPhase + pi * 0.11) % 1;
            const fi = u * (path.length - 1);
            const i0 = Math.floor(fi);
            const p0 = path[i0];
            const p1 = path[Math.min(path.length - 1, i0 + 1)];
            const t = fi - i0;
            dPos[n * 3] = p0.x + (p1.x - p0.x) * t;
            dPos[n * 3 + 1] = p0.y + (p1.y - p0.y) * t + 0.03;
            dPos[n * 3 + 2] = p0.z + (p1.z - p0.z) * t;
            const b = Math.sin(Math.PI * u) * dotAct;
            dFade[n] = b;
            n++;
          }
        }
        dotGeo.attributes.position.needsUpdate = true;
        dotGeo.attributes.aFade.needsUpdate = true;
      }

      /* bacteria in the sample droplet */
      const cfu = f.state.cfuPerMl;
      const conc = cfu > 0 ? clamp(Math.log10(1 + cfu) / 5, 0.3, 1) : 0;
      const nB = Math.floor(DROP_PTS * clamp(f.disp.free, 0, 1) * conc);
      const drop = f.disp.drop;
      bPts.visible = drop > 0.3 && nB > 0 && f.live && f.flow;
      if (bPts.visible) {
        for (let i = 0; i < DROP_PTS; i++) {
          const s = bSeed[i];
          const a = s.a + f.t * 0.15 * s.sp;
          const r = s.r * 0.8 * drop;
          const h = (0.04 + s.h * 0.24 + 0.02 * Math.sin(f.t * s.sp * 1.3 + s.ph)) * drop;
          bPos[i * 3] = L.we.x + Math.cos(a) * r;
          bPos[i * 3 + 1] = L.padTop + 0.03 + h;
          bPos[i * 3 + 2] = L.we.z + Math.sin(a) * r;
          const v = i < nB ? 1 : 0;
          bFade[i] = v;
        }
        bGeo.attributes.position.needsUpdate = true;
        bGeo.attributes.aFade.needsUpdate = true;
      }

      /* warburg: halo pulse (opacity registered) + particles diffusing toward the surface */
      const dens = clamp(0.3 + (f.sigmaRatio - 1) * 1.2, 0.2, 1);
      wPts.visible = f.live && f.flow && !away;
      if (wPts.visible) {
        const nW = Math.floor(MAIN_WARB * dens);
        for (let i = 0; i < MAIN_WARB; i++) {
          const s = wSeed[i];
          const u = (f.t * s.sp + s.ph) % 1;
          const y = L.padTop + 0.06 + (1 - Math.pow(u, 0.85)) * 0.42;
          const a = s.a + Math.sin(f.t * 0.6 + s.ph * 6) * 0.15;
          wPos[i * 3] = L.we.x + Math.cos(a) * s.r * 0.75;
          wPos[i * 3 + 1] = y;
          wPos[i * 3 + 2] = L.we.z + Math.sin(a) * s.r * 0.75;
          const b = Math.sin(Math.PI * u) * (i < nW ? 1 : 0);
          wFade[i] = b;
        }
        wGeo.attributes.position.needsUpdate = true;
        wGeo.attributes.aFade.needsUpdate = true;
      }

      /* guide lines: WE ring -> inset rim (follows explode offsets) */
      const padY = o.padGroup.position.y + L.padTop + 0.03;
      const ip = o.insetGroup.position;
      for (let k = 0; k < 8; k++) {
        const a = (k / 8) * TAU;
        const c = Math.cos(a);
        const s = Math.sin(a);
        const r0 = L.we.r + 0.09;
        gPos[k * 6] = L.we.x + c * r0;
        gPos[k * 6 + 1] = padY;
        gPos[k * 6 + 2] = L.we.z + s * r0;
        gPos[k * 6 + 3] = ip.x + c * L.insetR;
        gPos[k * 6 + 4] = ip.y;
        gPos[k * 6 + 5] = ip.z + s * L.insetR;
      }
      gGeo.attributes.position.needsUpdate = true;
    },
  };

  return { component, guide };
}
