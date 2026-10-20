/**
 * Shared build/runtime context for the procedural smart-bandage twin.
 * Pure three.js -- no React. Units: 1 unit = 1 cm, strip laid flat along X, up = +Y.
 */
import * as THREE from 'three';
import type { AssayState, PartKey } from '../types';
import { PART_KEYS } from '../types';

/** Fixed layout of the strip (cm). */
export const L = {
  len: 9,
  wid: 3,
  adhH: 0.07,
  padX0: -3.7,
  padX1: 0.85,
  padHalfZ: 1.15,
  padTop: 0.3,
  we: { x: -2.3, z: 0, r: 0.62 },
  re: { x: -0.7, z: -0.72, r: 0.2 },
  ce: { r0: 0.86, r1: 1.0, a: 35 },
  goldX: 0.5,
  goldZ: [-0.5, 0, 0.5],
  pcb: { x0: 0.9, x1: 4.4, halfZ: 1.1, y0: 0.09, t: 0.04 },
  chip: { x: 1.75, z: 0 },
  cell: { x: 3.55, z: 0, r: 0.72 },
  backY: 0.72,
  insetR: 1.5,
  insetY: 1.85,
} as const;

/** How far each layer travels in Explode mode (cm at explode = 1). */
export const EXPLODE = { adh: 0, pad: 0.7, pcb: 1.4, elec: 2.3, back: 4.2, insetLift: 1.3 };

export const MAX_BOUND = 40;

/** Smoothed readings (k = 1 - exp(-dt/0.7)) that actually drive the visuals. */
export interface Disp {
  rs: number;
  rct: number;
  cdl: number;
  sigma: number;
  zMag: number;
  zPhase: number;
  deltaZ: number;
  deltaRct: number;
  deltaCdl: number;
  coverage: number;
  /** 0..1 droplet size, 0..1 free-bacteria fraction (faster smoothing) */
  drop: number;
  free: number;
}

export interface Frame {
  /** animation clock (frozen while stale) */
  t: number;
  dt: number;
  disp: Disp;
  state: AssayState;
  live: boolean;
  flow: boolean;
  explode: number;
  reduced: boolean;
  /** 0..1 during baseline / measure2 (log-frequency sweep progress), else 0 */
  sweep: number;
  measuring: boolean;
  /** sigma relative to baseline (1 = unchanged) */
  sigmaRatio: number;
  /** light UI theme: additive glow effects switch to normal blending + darker saturated colours */
  light: boolean;
}

export interface Component {
  update(f: Frame): void;
}

export interface Reg {
  mat: THREE.Material;
  gain: number;
  mode: 'emissive' | 'opacity';
  base: THREE.Color;
  baseOpacity: number;
}

export interface AnchorInfo {
  obj: THREE.Object3D;
  normal: THREE.Vector3;
}

export interface Ghost {
  mat: THREE.Material;
  ghost: number;
  orig: number;
  origTransparent: boolean;
  origDepthWrite: boolean;
}

export interface BuildCtx {
  root: THREE.Group;
  regs: Record<PartKey, Reg[]>;
  anchors: Partial<Record<PartKey, AnchorInfo>>;
  pickables: THREE.Object3D[];
  ghosts: Ghost[];
  /** register a material so telemetry can drive its emissive (or opacity) */
  reg(key: PartKey, mat: THREE.Material, gain?: number, opts?: { mode?: 'emissive' | 'opacity'; base?: THREE.Color | number }): void;
  /** make an object clickable/hoverable as `key`. Lower priority hits lose to higher ones. */
  pickable(obj: THREE.Object3D, key: PartKey, priority?: number): void;
  anchor(key: PartKey, parent: THREE.Object3D, pos: THREE.Vector3, normal: THREE.Vector3): void;
  /** register [material, ghostOpacity] for X-ray */
  ghost(mat: THREE.Material, ghostOpacity: number): void;
}

export function createCtx(root: THREE.Group): BuildCtx {
  const regs = {} as Record<PartKey, Reg[]>;
  for (const k of PART_KEYS) regs[k] = [];
  const ctx: BuildCtx = {
    root,
    regs,
    anchors: {},
    pickables: [],
    ghosts: [],
    reg(key, mat, gain = 1, opts) {
      const mode = opts?.mode ?? 'emissive';
      const base = new THREE.Color(opts?.base ?? 0x000000);
      const m = mat as THREE.Material & { opacity: number };
      regs[key].push({ mat, gain, mode, base, baseOpacity: m.opacity ?? 1 });
    },
    pickable(obj, key, priority = 1) {
      obj.userData.part = key;
      obj.userData.pickPriority = priority;
      ctx.pickables.push(obj);
    },
    anchor(key, parent, pos, normal) {
      const o = new THREE.Object3D();
      o.name = `anchor-${key}`;
      o.position.copy(pos);
      parent.add(o);
      ctx.anchors[key] = { obj: o, normal: normal.clone().normalize() };
    },
    ghost(mat, ghostOpacity) {
      const m = mat as THREE.Material & { opacity: number };
      ctx.ghosts.push({ mat, ghost: ghostOpacity, orig: m.opacity, origTransparent: mat.transparent, origDepthWrite: mat.depthWrite });
    },
  };
  return ctx;
}

/** Targets derived from the workflow phase (droplet size, free-bacteria fraction). */
export function phaseTargets(s: AssayState): { drop: number; free: number; measuring: boolean; sweep: number } {
  const pp = Math.min(1, Math.max(0, s.phaseProgress));
  const sm = (x: number) => {
    const c = Math.min(1, Math.max(0, x));
    return c * c * (3 - 2 * c);
  };
  let drop = 0;
  let free = 0;
  switch (s.phase) {
    case 'exposure':
      drop = sm(pp * 1.4);
      free = sm(pp * 1.2);
      break;
    case 'binding':
      drop = 1;
      free = 1 - 0.7 * pp;
      break;
    case 'measure2':
      drop = 1 - sm(pp * 1.6);
      free = 0.3 * (1 - pp);
      break;
    default:
      break;
  }
  const measuring = s.phase === 'baseline' || s.phase === 'measure2';
  return { drop, free, measuring, sweep: measuring ? pp : 0 };
}

const STATUS_HEX = { nominal: 0x34d399, warning: 0xffb700, critical: 0xff3b30 } as const;
export const STATUS_RGB = {
  nominal: new THREE.Color(STATUS_HEX.nominal),
  warning: new THREE.Color(STATUS_HEX.warning),
  critical: new THREE.Color(STATUS_HEX.critical),
};

/** Local, visual-only status tint (there is no backend status in this twin). */
export function statusOf(key: PartKey, s: AssayState): 'nominal' | 'warning' | 'critical' {
  if (key !== 'deltaZ' && key !== 'zmag' && key !== 'phase') return 'nominal';
  if (s.detected === true) return 'critical';
  if (s.deltaZMagPct != null && Math.abs(s.deltaZMagPct) > 10) return 'warning';
  return 'nominal';
}
