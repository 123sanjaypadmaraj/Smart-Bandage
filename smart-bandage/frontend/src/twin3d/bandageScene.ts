/**
 * Smart-bandage digital twin -- plain three.js scene (no React).
 * Same realism recipe as the aero-engine twin: procedural PBR geometry, RoomEnvironment + PMREM IBL,
 * ACES filmic tone mapping, key + rim + hemisphere lights with soft shadows, narrow-FOV camera,
 * telemetry-driven emissive glow on smoothed readings, X-ray / explode, raycast picking, projected pins.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { PART_KEYS, PHASE_LABEL, REF_FREQ_HZ, type AssayState, type BandageScene, type PartKey } from './types';
import { createCtx, EXPLODE, L, phaseTargets, statusOf, STATUS_RGB, type Disp, type Frame } from './scene/ctx';
import { buildBandage, type BandageModel } from './scene/model';
import { clamp, disposeTree, lerp, smooth } from './scene/util';

type ViewName = 'iso' | 'front' | 'side' | 'top' | 'macro';

export interface BandageSceneOptions {
  onSelect?: (p: PartKey | null) => void;
  onHover?: (p: PartKey | null) => void;
}

const LABEL: Record<PartKey, string> = {
  rs: 'Rs',
  rct: 'Rct',
  cdl: 'Cdl',
  warburg: 'σ Warburg',
  zmag: '|Z|',
  phase: 'Phase',
  deltaZ: 'ΔZ',
  coverage: 'Coverage',
  battery: 'Battery',
};

const ACCENT = new THREE.Color(0.36, 0.55, 1);
const GLOW = {
  rct: new THREE.Color(1.0, 0.55, 0.08),
  cdl: new THREE.Color(0.2, 0.5, 1.0),
  rs: new THREE.Color(0.1, 0.9, 1.0),
  warburg: new THREE.Color(0.55, 0.36, 1.0),
  coverage: new THREE.Color(0.7, 0.92, 0.2),
  battery: new THREE.Color(0.2, 0.9, 0.5),
};

/** Camera directions (target -> camera). Distance and target are fitted to the content + canvas aspect. */
const VIEW_DIR: Record<ViewName, [number, number, number]> = {
  iso: [0.5, 0.6, 0.9],
  front: [0, 0.14, 1],
  side: [1, 0.16, 0.0001],
  top: [0.0001, 1, 0.006],
  macro: [0.28, 0.32, 1],
};

const fin = (v: unknown, fb: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : fb);

const DEFAULT_STATE: AssayState = {
  phase: 'idle',
  phaseProgress: 0,
  cfuPerMl: 0,
  coverage: 0,
  baseline: null,
  current: { rs: 120, rct: 2000, cdl: 2e-6, sigma: 150, n: 0.92 },
  baselineSpectrum: null,
  currentSpectrum: [],
  zMag: 800,
  zPhase: -30,
  deltaZMagPct: null,
  deltaRctPct: null,
  deltaCdlPct: null,
  detected: null,
  estimatedCfuPerMl: null,
  stale: false,
};

function fmtValue(key: PartKey, d: Disp, s: AssayState): string {
  switch (key) {
    case 'rs':
      return `${d.rs.toFixed(0)} Ω`;
    case 'rct':
      return d.rct >= 1000 ? `${(d.rct / 1000).toFixed(2)} kΩ` : `${d.rct.toFixed(0)} Ω`;
    case 'cdl':
      return `${(d.cdl * 1e6).toFixed(2)} µF`;
    case 'warburg':
      return `${d.sigma.toFixed(0)} Ω·s⁻½`;
    case 'zmag':
      return `${d.zMag.toFixed(0)} Ω`;
    case 'phase':
      return `${d.zPhase.toFixed(1)}°`;
    case 'deltaZ':
      return s.deltaZMagPct == null ? '—' : `${d.deltaZ >= 0 ? '+' : ''}${d.deltaZ.toFixed(1)} %`;
    case 'coverage':
      return `${(d.coverage * 100).toFixed(0)} %`;
    case 'battery':
      return '3.0 V';
  }
}

const PIN_CSS =
  'position:absolute;left:0;top:0;display:flex;align-items:center;gap:6px;padding:3px 9px 3px 7px;border-radius:999px;' +
  'font:600 11px/1.25 ui-sans-serif,system-ui,sans-serif;color:#f2f5ff;background:rgba(12,14,20,0.82);' +
  'border:1px solid rgba(140,170,255,0.4);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);white-space:nowrap;' +
  'cursor:pointer;pointer-events:auto;will-change:transform;opacity:0;visibility:hidden;' +
  'transition:opacity .15s ease,box-shadow .15s ease,border-color .15s ease;';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** UI theme: the app toggles a `dark` class on <html> (Tailwind class strategy). */
function isLightTheme(): boolean {
  if (typeof document === 'undefined') return false;
  return !document.documentElement.classList.contains('dark');
}

export function createBandageScene(host: HTMLElement, opts: BandageSceneOptions = {}): BandageScene {
  const reduced = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';

  /* ------------------------------------------------------------ renderer */
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    if (!renderer.getContext()) throw new Error('no context');
  } catch (err) {
    throw new Error(`WebGL unavailable: ${err instanceof Error ? err.message : String(err)}`);
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const canvas = renderer.domElement;
  // dev-only introspection hook (renderer.info for leak / draw-call checks)
  if (import.meta.env.DEV) (window as unknown as { __bandageRenderer?: THREE.WebGLRenderer }).__bandageRenderer = renderer;
  canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;outline:none;';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', '3D digital twin of the smart bandage: layers, three-electrode sensor and reader electronics');
  host.appendChild(canvas);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  let envTarget = null as THREE.WebGLRenderTarget | null;
  const buildEnvironment = () => {
    const room = new RoomEnvironment();
    const target = pmrem.fromScene(room, 0.04);
    room.dispose();
    envTarget?.dispose();
    envTarget = target;
    scene.environment = target.texture;
  };
  buildEnvironment();
  scene.environmentIntensity = 0.75;

  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 200);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 2.5;
  controls.maxDistance = 70;
  controls.maxPolarAngle = Math.PI * 0.94;
  controls.autoRotateSpeed = 1.2;

  /* -------------------------------------------------------------- lights */
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(6, 9, 6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, near: 1, far: 34 });
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.03;
  const rim = new THREE.DirectionalLight(0x5b8cff, 1.1);
  rim.position.set(-7, 3, -6);
  scene.add(key, rim, new THREE.HemisphereLight(0xbfd0ff, 0x1a1208, 0.35));

  /* ------------------------------------------------------------ pedestal */
  // soft floor: a radial alpha fade instead of a hard-edged slab, so it never reads as a clipped plate
  const fadeCanvas = document.createElement('canvas');
  fadeCanvas.width = fadeCanvas.height = 256;
  {
    const g = fadeCanvas.getContext('2d')!;
    const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    grad.addColorStop(0, '#fff');
    grad.addColorStop(0.55, '#c8c8c8');
    grad.addColorStop(1, '#000');
    g.fillStyle = grad;
    g.fillRect(0, 0, 256, 256);
  }
  const fadeTex = new THREE.CanvasTexture(fadeCanvas);
  const discMat = new THREE.MeshStandardMaterial({
    color: 0x1a1d24, metalness: 0.4, roughness: 0.6, transparent: true, opacity: 0.8, alphaMap: fadeTex, depthWrite: false,
  });
  const disc = new THREE.Mesh(new THREE.CircleGeometry(7.4, 96), discMat);
  disc.rotation.x = -Math.PI / 2;
  disc.position.y = -0.06;
  disc.receiveShadow = true;
  scene.add(disc);
  const ringOp = [0.3, 0.18, 0.08];
  const ringMats: THREE.MeshBasicMaterial[] = [];
  [5.0, 5.7, 6.4].forEach((r, i) => {
    const mat = new THREE.MeshBasicMaterial({ color: 0x5b8cff, transparent: true, opacity: ringOp[i], side: THREE.DoubleSide, depthWrite: false });
    ringMats.push(mat);
    const ring = new THREE.Mesh(new THREE.RingGeometry(r - 0.012, r + 0.012, 128), mat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = -0.038;
    scene.add(ring);
  });
  const groundMat = new THREE.ShadowMaterial({ opacity: 0.38 });
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), groundMat);
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.035;
  ground.receiveShadow = true;
  scene.add(ground);

  /* --------------------------------------------------------------- model */
  const modelRoot = new THREE.Group();
  modelRoot.name = 'bandage';
  scene.add(modelRoot);
  const ctx = createCtx(modelRoot);
  let model: BandageModel;
  try {
    model = buildBandage(ctx);
    for (const k of PART_KEYS) if (!ctx.anchors[k]) throw new Error(`missing anchor ${k}`);
  } catch (err) {
    // partial build: release everything created so far, then let the caller show its fallback
    try {
      controls.dispose();
      disposeTree(scene);
      scene.environment = null;
      envTarget?.dispose();
      pmrem.dispose();
      renderer.dispose();
      canvas.remove();
    } catch {
      /* ignore secondary failures */
    }
    throw err;
  }

  /* ---------------------------------------------------------------- state */
  let state: AssayState = DEFAULT_STATE;
  let firstUpdate = true;
  const disp: Disp = {
    rs: 120, rct: 2000, cdl: 2e-6, sigma: 150, zMag: 800, zPhase: -30, deltaZ: 0, deltaRct: 0, deltaCdl: 0, coverage: 0, drop: 0, free: 0,
  };
  const tgt: Disp = { ...disp };
  const toggles = { xray: false, explode: false, flow: true, labels: true, autoRotate: false };
  let selected: PartKey | null = null;
  let hover: PartKey | null = null;
  let xrayF = 0;
  let ghostOn = false;
  let explodeF = 0;
  let animT = 0;
  let disposed = false;
  let lost = false;
  let onScreen = true;
  let tabVisible = typeof document === 'undefined' ? true : !document.hidden;
  let failures = 0;
  let raf = 0;
  let lastT = 0;
  let currentView: ViewName = 'iso';
  /** true until the user orbits/zooms: then a canvas resize re-fits the current view */
  let autoFrame = true;

  /* ---------------------------------------------------------------- theme */
  let light = isLightTheme();
  function applyTheme(): void {
    discMat.color.setHex(light ? 0xdfe6f5 : 0x1a1d24);
    discMat.metalness = light ? 0.05 : 0.4;
    discMat.roughness = light ? 0.8 : 0.6;
    discMat.opacity = light ? 0.95 : 0.8;
    ringMats.forEach((m, i) => {
      m.color.setHex(light ? 0x2f5fd0 : 0x5b8cff);
      m.opacity = ringOp[i] * (light ? 1.3 : 1);
    });
    groundMat.opacity = light ? 0.3 : 0.38;
    renderer.toneMappingExposure = light ? 1.0 : 1.05;
  }
  applyTheme();
  const onTheme = () => {
    const next = isLightTheme();
    if (next !== light) {
      light = next;
      applyTheme();
    }
  };
  const themeObserver = new MutationObserver(onTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });

  function computeTargets(): void {
    const s = state;
    const c = s.current;
    tgt.rs = fin(c?.rs, disp.rs);
    tgt.rct = fin(c?.rct, disp.rct);
    tgt.cdl = fin(c?.cdl, disp.cdl);
    tgt.sigma = fin(c?.sigma, disp.sigma);
    tgt.zMag = fin(s.zMag, disp.zMag);
    tgt.zPhase = fin(s.zPhase, disp.zPhase);
    tgt.deltaZ = fin(s.deltaZMagPct, 0);
    tgt.deltaRct = fin(s.deltaRctPct, 0);
    tgt.deltaCdl = fin(s.deltaCdlPct, 0);
    tgt.coverage = clamp(fin(s.coverage, 0), 0, 1);
    const pt = phaseTargets(s);
    tgt.drop = pt.drop;
    tgt.free = pt.free;
  }
  computeTargets();

  /* ------------------------------------------------------------------ UI */
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden;';
  host.appendChild(overlay);
  // leader lines (chip -> projected anchor) live under the chips
  const leaderSvg = document.createElementNS(SVG_NS, 'svg');
  leaderSvg.setAttribute('aria-hidden', 'true');
  leaderSvg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible;';
  overlay.appendChild(leaderSvg);
  const pinLayer = document.createElement('div');
  pinLayer.style.cssText = 'position:absolute;inset:0;pointer-events:none;';
  overlay.appendChild(pinLayer);
  const hud = document.createElement('div');
  hud.style.cssText =
    'position:absolute;left:10px;top:8px;padding:4px 9px;border-radius:8px;background:rgba(12,14,20,0.7);color:#e8ecf8;' +
    'font:600 10.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.04em;pointer-events:none;';
  overlay.appendChild(hud);
  const notice = document.createElement('div');
  notice.setAttribute('role', 'status');
  notice.style.cssText =
    'position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;padding:16px;' +
    'background:rgba(12,14,20,0.55);color:#f2f5ff;font:500 13px system-ui,sans-serif;pointer-events:none;';
  overlay.appendChild(notice);

  interface Pin {
    el: HTMLButtonElement;
    dot: HTMLSpanElement;
    lab: HTMLSpanElement;
    val: HTMLSpanElement;
    last: string;
    lastStatus: string;
    line: SVGLineElement;
    node: SVGCircleElement;
    /** measured chip size (px) */
    w: number;
    h: number;
    /** smoothed chip centre (px) */
    px: number;
    py: number;
    shown: boolean;
    /** per-frame scratch: anchor + target centre */
    ax: number;
    ay: number;
    cx: number;
    cy: number;
  }
  const pins = new Map<PartKey, Pin>();
  const setHover = (k: PartKey | null) => {
    if (hover === k) return;
    hover = k;
    canvas.style.cursor = k ? 'pointer' : 'grab';
    opts.onHover?.(k);
  };
  const setSelected = (k: PartKey | null, notify: boolean) => {
    if (selected === k) return;
    selected = k;
    if (notify) opts.onSelect?.(k);
  };
  const toggleSelect = (k: PartKey | null) => setSelected(k === selected ? null : k, true);

  for (const k of PART_KEYS) {
    const el = document.createElement('button');
    el.type = 'button';
    el.style.cssText = PIN_CSS;
    el.dataset.part = k;
    el.setAttribute('aria-pressed', 'false');
    const dot = document.createElement('span');
    dot.style.cssText = 'width:7px;height:7px;border-radius:50%;background:#34d399;flex:none;';
    const lab = document.createElement('span');
    lab.textContent = LABEL[k];
    lab.style.cssText = 'opacity:.72;font-weight:500;';
    const val = document.createElement('span');
    val.style.cssText = 'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;';
    el.append(dot, lab, val);
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelect(k);
    });
    el.addEventListener('pointerenter', () => setHover(k));
    el.addEventListener('pointerleave', () => setHover(null));
    pinLayer.appendChild(el);
    const line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('stroke-linecap', 'round');
    const node = document.createElementNS(SVG_NS, 'circle');
    node.setAttribute('r', '3');
    leaderSvg.append(line, node);
    pins.set(k, { el, dot, lab, val, last: '', lastStatus: '', line, node, w: 110, h: 22, px: 0, py: 0, shown: false, ax: 0, ay: 0, cx: 0, cy: 0 });
  }

  /* ----------------------------------------------------- camera / views */
  const tw = { active: false, t0: 0, dur: 0.9, fromP: new THREE.Vector3(), fromT: new THREE.Vector3(), toP: new THREE.Vector3(), toT: new THREE.Vector3() };
  const tmpV2 = new THREE.Vector3();
  let width = 1;
  let height = 1;

  function tweenTo(p: THREE.Vector3, t: THREE.Vector3, immediate = false): void {
    if (immediate) {
      camera.position.copy(p);
      controls.target.copy(t);
      camera.lookAt(t);
      tw.active = false;
      return;
    }
    tw.fromP.copy(camera.position);
    tw.fromT.copy(controls.target);
    tw.toP.copy(p);
    tw.toT.copy(t);
    tw.t0 = 0; // elapsed seconds (advanced by tick dt, so manual stepping is deterministic)
    tw.dur = reduced ? 0.01 : 0.9;
    tw.active = true;
  }
  /**
   * Points that must stay in frame: the strip's bounding box (with explode offsets) plus the macro inset
   * (or only the inset for the macro view).
   */
  function fitPoints(macro: boolean, ex: number): THREE.Vector3[] {
    const pts: THREE.Vector3[] = [];
    const ix = L.we.x;
    const iy = L.insetY + EXPLODE.insetLift * ex;
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2;
      for (const y of [-0.15, 1.95]) pts.push(new THREE.Vector3(ix + Math.cos(a) * 1.6, iy + y, Math.sin(a) * 1.6));
    }
    // inset labels sit to the right of the disc
    for (const y of [-0.2, 1.05]) pts.push(new THREE.Vector3(ix + 3.5, iy + y, 0.2));
    if (macro) return pts;
    const top = L.backY + 0.06 + EXPLODE.back * ex;
    for (const x of [-L.len / 2, L.len / 2]) for (const y of [-0.1, top]) for (const z of [-L.wid / 2, L.wid / 2]) pts.push(new THREE.Vector3(x, y, z));
    return pts;
  }
  const fitCam = new THREE.PerspectiveCamera(32, 1, 0.1, 200);
  const fitX = new THREE.Vector3();
  const fitY = new THREE.Vector3();
  const fitZ = new THREE.Vector3();
  const fitQ = new THREE.Vector3();
  /**
   * Fit distance + target so `pts` fill ~88% of the canvas for the given viewing direction and current aspect
   * (a few relaxation passes on real projected NDC extents, so perspective is accounted for).
   */
  function fitPose(dir: THREE.Vector3, macro: boolean, ex: number, outP: THREE.Vector3, outT: THREE.Vector3): void {
    const aspect = width / Math.max(1, height);
    fitCam.fov = camera.fov;
    fitCam.aspect = aspect;
    fitCam.updateProjectionMatrix();
    fitZ.copy(dir).normalize();
    fitX.set(0, 1, 0).cross(fitZ);
    if (fitX.lengthSq() < 1e-8) fitX.set(1, 0, 0);
    fitX.normalize();
    fitY.crossVectors(fitZ, fitX);
    const pts = fitPoints(macro, ex);
    const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const tanH = tanV * aspect;
    // horizontal margin 0.9; vertical 0.84 leaves headroom for pin chips above the inset
    const mx = 0.94;
    const my = macro ? 0.86 : 0.9;
    let lo = [Infinity, Infinity, Infinity];
    let hi = [-Infinity, -Infinity, -Infinity];
    for (const p of pts) {
      const c = [p.dot(fitX), p.dot(fitY), p.dot(fitZ)];
      for (let i = 0; i < 3; i++) {
        lo[i] = Math.min(lo[i], c[i]);
        hi[i] = Math.max(hi[i], c[i]);
      }
    }
    const mid = lo.map((v, i) => (v + hi[i]) / 2);
    outT.set(0, 0, 0).addScaledVector(fitX, mid[0]).addScaledVector(fitY, mid[1]).addScaledVector(fitZ, mid[2]);
    let D = 0;
    for (const p of pts) {
      const dz = p.dot(fitZ) - mid[2];
      D = Math.max(D, Math.abs(p.dot(fitX) - mid[0]) / (tanH * mx) + dz, Math.abs(p.dot(fitY) - mid[1]) / (tanV * my) + dz);
    }
    for (let it = 0; it < 4; it++) {
      outP.copy(outT).addScaledVector(fitZ, D);
      fitCam.position.copy(outP);
      fitCam.lookAt(outT);
      fitCam.updateMatrixWorld(true);
      let x0 = Infinity;
      let x1 = -Infinity;
      let y0 = Infinity;
      let y1 = -Infinity;
      for (const p of pts) {
        fitQ.copy(p).project(fitCam);
        x0 = Math.min(x0, fitQ.x);
        x1 = Math.max(x1, fitQ.x);
        y0 = Math.min(y0, fitQ.y);
        y1 = Math.max(y1, fitQ.y);
      }
      // re-centre the target in the view, then rescale the distance to hit the margins
      outT.addScaledVector(fitX, ((x0 + x1) / 2) * tanH * D).addScaledVector(fitY, ((y0 + y1) / 2) * tanV * D);
      const need = Math.max((x1 - x0) / 2 / mx, (y1 - y0) / 2 / my);
      if (Number.isFinite(need) && need > 0) D *= clamp(need, 0.6, 1.6);
    }
    D = Math.max(D, controls.minDistance);
    outP.copy(outT).addScaledVector(fitZ, D);
  }
  const dirV = new THREE.Vector3();
  function viewPose(v: ViewName, outP: THREE.Vector3, outT: THREE.Vector3): void {
    dirV.set(...VIEW_DIR[v]);
    // tall/narrow canvas: look down more steeply so the long strip uses the vertical space
    if (v === 'iso' && width / Math.max(1, height) < 1) dirV.set(0.5, 0.72, 0.9);
    fitPose(dirV, v === 'macro', toggles.explode ? 1 : 0, outP, outT);
  }
  function setView(v: ViewName, immediate = false): void {
    const p = new THREE.Vector3();
    const t = new THREE.Vector3();
    currentView = v;
    autoFrame = true;
    viewPose(v, p, t);
    tweenTo(p, t, immediate);
  }

  function resize(): void {
    const w = Math.max(1, host.clientWidth);
    const h = Math.max(1, host.clientHeight);
    width = w;
    height = h;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // keep the whole strip + inset framed when the canvas changes shape, unless the user has taken the camera
    if (autoFrame && !firstResize) setView(currentView, true);
    firstResize = false;
  }
  let firstResize = true;
  resize();
  setView('iso', true);

  /* --------------------------------------------------------- interaction */
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let pointerIn = false;
  let downX = 0;
  let downY = 0;
  let isDown = false;
  let dragged = false;

  function visibleChain(o: THREE.Object3D): boolean {
    for (let n: THREE.Object3D | null = o; n; n = n.parent) if (!n.visible) return false;
    return true;
  }
  function pickAt(): PartKey | null {
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(ctx.pickables, false);
    let low: PartKey | null = null;
    for (const h of hits) {
      if (!visibleChain(h.object)) continue;
      const part = h.object.userData.part as PartKey | undefined;
      if (!part) continue;
      if ((h.object.userData.pickPriority as number) > 0) return part;
      if (!low) low = part;
    }
    return low;
  }
  function setNdc(e: PointerEvent): void {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / Math.max(1, r.width)) * 2 - 1, -((e.clientY - r.top) / Math.max(1, r.height)) * 2 + 1);
  }
  const onMove = (e: PointerEvent) => {
    pointerIn = true;
    setNdc(e);
    if (isDown && Math.hypot(e.clientX - downX, e.clientY - downY) > 5) dragged = true;
  };
  const onWheel = () => {
    autoFrame = false;
  };
  const onDown = (e: PointerEvent) => {
    autoFrame = false;
    isDown = true;
    dragged = false;
    downX = e.clientX;
    downY = e.clientY;
    setNdc(e);
    canvas.style.cursor = 'grabbing';
  };
  const onUp = (e: PointerEvent) => {
    if (!isDown) return;
    isDown = false;
    setNdc(e);
    const moved = dragged || Math.hypot(e.clientX - downX, e.clientY - downY) > 5;
    canvas.style.cursor = hover ? 'pointer' : 'grab';
    if (moved) return; // orbit drag, not a click
    toggleSelect(pickAt());
  };
  const onLeave = () => {
    pointerIn = false;
    if (!isDown) setHover(null);
  };
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('wheel', onWheel, { passive: true });

  /* -------------------------------------------------------------- glow */
  const emis = new THREE.Color();
  const stC = new THREE.Color();
  const tmpC = new THREE.Color();
  function glowFor(k: PartKey, f: Frame, out: THREE.Color): number {
    const d = f.disp;
    const s = f.state;
    const t = f.t;
    switch (k) {
      case 'rct':
        out.copy(GLOW.rct);
        return Math.pow(clamp(d.deltaRct / 60, 0, 1), 0.9);
      case 'cdl':
        out.copy(GLOW.cdl);
        return clamp(0.75 + d.deltaCdl / 60, 0.1, 0.85);
      case 'rs':
        out.copy(GLOW.rs);
        return clamp(0.25 + (d.rs / 120 - 1) * 4, 0.15, 0.7);
      case 'warburg':
        out.copy(GLOW.warburg);
        return clamp(0.35 + (f.sigmaRatio - 1) * 1.2, 0.2, 1) * (0.6 + 0.4 * Math.sin(t * 1.6));
      case 'coverage':
        out.copy(GLOW.coverage);
        return clamp(d.coverage, 0, 1) * 0.6;
      case 'battery':
        out.copy(GLOW.battery);
        return f.measuring ? 0.35 + 0.2 * Math.sin(t * 5) : 0.12;
      case 'zmag':
      case 'phase':
      case 'deltaZ': {
        const st = statusOf(k, s);
        out.copy(STATUS_RGB[st]);
        if (k === 'deltaZ') {
          const blink = f.measuring ? 0.55 + 0.45 * (Math.sin(t * 8) > 0 ? 1 : 0.3) : 1;
          const base = st === 'nominal' ? 0.85 : st === 'warning' ? 0.95 + 0.1 * Math.sin(t * 3.2) : 1.0 + 0.3 * Math.sin(t * 9);
          return base * blink;
        }
        return st === 'nominal' ? 0.1 : st === 'warning' ? 0.22 + 0.12 * Math.sin(t * 3.2) : 0.4 + 0.25 * Math.sin(t * 9);
      }
    }
  }
  function applyGlow(f: Frame): void {
    for (const k of PART_KEYS) {
      const level = glowFor(k, f, stC);
      const acc = k === selected ? 0.22 + 0.06 * Math.sin(f.t * 4) : k === hover ? 0.14 : 0;
      for (const r of ctx.regs[k]) {
        if (r.mode === 'emissive') {
          const m = r.mat as THREE.MeshStandardMaterial;
          emis.copy(r.base).add(tmpC.copy(stC).multiplyScalar(level * r.gain)).add(tmpC.copy(ACCENT).multiplyScalar(acc));
          m.emissive.copy(emis);
        } else {
          const m = r.mat as THREE.Material & { opacity: number };
          m.opacity = clamp(r.baseOpacity + level * r.gain + acc * 0.15, 0, 1);
        }
      }
    }
    const ledSt = statusOf('deltaZ', f.state);
    model.ledLight.color.copy(STATUS_RGB[ledSt]);
    model.ledLight.intensity = 0.2 * clamp(glowFor('deltaZ', f, stC), 0, 1.3);
  }
  function applyGhosts(k: number): void {
    const on = k > 0.001;
    for (const g of ctx.ghosts) {
      const m = g.mat as THREE.Material & { opacity: number };
      m.opacity = lerp(g.orig, g.ghost, k);
      if (on !== ghostOn) {
        m.transparent = on || g.origTransparent;
        m.depthWrite = on ? false : g.origDepthWrite;
        m.needsUpdate = true;
      }
    }
    ghostOn = on;
  }

  /* --------------------------------------------------------------- pins */
  const worldV = new THREE.Vector3();
  const toCam = new THREE.Vector3();
  let compact = false;
  let measureDirty = true;
  const hudRect = { x0: 0, y0: 0, x1: 0, y1: 0 };
  const vis: Pin[] = [];
  function setCompact(c: boolean): void {
    compact = c;
    for (const pin of pins.values()) {
      pin.el.style.padding = c ? '2px 7px 2px 5px' : '3px 9px 3px 7px';
      pin.el.style.gap = c ? '4px' : '6px';
      pin.el.style.fontSize = c ? '10px' : '11px';
    }
    measureDirty = true;
  }
  function measurePins(): void {
    for (const pin of pins.values()) {
      pin.w = Math.max(40, pin.el.offsetWidth);
      pin.h = Math.max(16, pin.el.offsetHeight);
    }
    hudRect.x0 = hud.offsetLeft - 4;
    hudRect.y0 = hud.offsetTop - 4;
    hudRect.x1 = hud.offsetLeft + hud.offsetWidth + 4;
    hudRect.y1 = hud.offsetTop + hud.offsetHeight + 4;
    measureDirty = false;
  }
  /**
   * Screen-space declutter: every chip starts radially outside the anchor cloud (fixed per-key hints break ties
   * so it stays stable), then a few relaxation passes push overlapping chips apart. Result is smoothed by the caller.
   */
  function declutter(items: Pin[]): void {
    const n = items.length;
    if (!n) return;
    const gap = compact ? 4 : 6;
    const pad = 8;
    const W = width;
    const H = height;
    // tall canvas: callout rows above/below the model; wide canvas: columns left/right of it
    const rows = W / Math.max(1, H) < 1.25;
    const key = (p: Pin) => (rows ? p.ay : p.ax);
    const along = (p: Pin) => (rows ? p.ax : p.ay);
    const sorted = [...items].sort((p, q) => key(p) - key(q));
    // callouts sit just outside the cloud of anchors (not at the canvas edge) so leader lines stay short
    const cloudLo = key(sorted[0]);
    const cloudHi = key(sorted[n - 1]);
    const lead = compact ? 26 : 34;
    const extent = rows ? H : W;
    const half = Math.ceil(n / 2);
    const groups = [sorted.slice(0, half), sorted.slice(half)];
    groups.forEach((grp, gi) => {
      if (!grp.length) return;
      grp.sort((p, q) => along(p) - along(q));
      // pack into lines along the edge, wrapping when a line is full
      const span = rows ? W : H;
      const lines: Pin[][] = [[]];
      let used = 0;
      for (const p of grp) {
        const size = (rows ? p.w : p.h) + gap;
        if (used + size > span - 2 * pad && lines[lines.length - 1].length) {
          lines.push([]);
          used = 0;
        }
        lines[lines.length - 1].push(p);
        used += size;
      }
      let offset = pad; // distance from the near edge of the canvas
      for (const line of lines) {
        const thick = Math.max(...line.map((p) => (rows ? p.h : p.w)));
        const total = line.reduce((t, p) => t + (rows ? p.w : p.h) + gap, -gap);
        const mean = line.reduce((t, p) => t + along(p), 0) / line.length;
        let start = clamp(mean - total / 2, pad, Math.max(pad, span - pad - total));
        for (const p of line) {
          const size = rows ? p.w : p.h;
          const mid = start + size / 2;
          const want = gi === 0 ? cloudLo - lead - offset - thick / 2 : cloudHi + lead + offset + thick / 2;
          const cross = clamp(want, pad + thick / 2, Math.max(pad + thick / 2, extent - pad - thick / 2));
          if (rows) {
            p.cx = mid;
            p.cy = cross;
          } else {
            p.cx = cross;
            p.cy = mid;
          }
          start += size + gap;
        }
        offset += thick + gap;
      }
    });
    // the HUD card is an immovable obstacle: nudge any chip that lands on it
    for (const a of items) {
      const hx = a.w / 2 + (hudRect.x1 - hudRect.x0) / 2 - Math.abs(a.cx - (hudRect.x0 + hudRect.x1) / 2);
      const hy = a.h / 2 + (hudRect.y1 - hudRect.y0) / 2 - Math.abs(a.cy - (hudRect.y0 + hudRect.y1) / 2);
      if (hx > 0 && hy > 0) {
        if (rows) a.cy = hudRect.y1 + a.h / 2 + gap;
        else a.cx = hudRect.x1 + a.w / 2 + gap;
      }
    }
  }
  function updatePins(f: Frame): void {
    if (Math.abs(width - lastPinW) > 0.5) {
      lastPinW = width;
      measureDirty = true;
    }
    vis.length = 0;
    for (const k of PART_KEYS) {
      const pin = pins.get(k);
      const a = ctx.anchors[k];
      if (!pin || !a) continue;
      a.obj.getWorldPosition(worldV);
      toCam.copy(camera.position).sub(worldV).normalize();
      const facing = a.normal.dot(toCam);
      worldV.project(camera);
      const on = toggles.labels && worldV.z < 1 && Math.abs(worldV.x) < 1.05 && Math.abs(worldV.y) < 1.05 && facing > -0.05;
      pin.ax = (worldV.x * 0.5 + 0.5) * width;
      pin.ay = (-worldV.y * 0.5 + 0.5) * height;
      if (on) vis.push(pin);
      else pin.shown = false;
      pin.el.style.opacity = on ? '1' : '0';
      pin.el.style.visibility = on ? 'visible' : 'hidden';
      pin.el.style.zIndex = k === selected ? '3' : k === hover ? '2' : '1';
      pin.line.style.display = on ? '' : 'none';
      pin.node.style.display = on ? '' : 'none';
      const st = statusOf(k, f.state);
      if (st !== pin.lastStatus) {
        pin.lastStatus = st;
        pin.dot.style.background = '#' + STATUS_RGB[st].getHexString();
      }
      const isSel = k === selected;
      pin.el.style.borderColor = isSel ? '#5b8cff' : k === hover ? 'rgba(140,170,255,0.85)' : 'rgba(140,170,255,0.4)';
      pin.el.style.boxShadow = isSel ? '0 0 0 2px rgba(91,140,255,0.45)' : 'none';
      if (pin.el.getAttribute('aria-pressed') !== String(isSel)) pin.el.setAttribute('aria-pressed', String(isSel));
      const stroke = isSel ? '#5b8cff' : k === hover ? '#8fb0ff' : light ? 'rgba(47,95,208,0.8)' : 'rgba(140,170,255,0.7)';
      pin.line.setAttribute('stroke', stroke);
      pin.line.setAttribute('stroke-width', isSel || k === hover ? '1.6' : '1');
      pin.node.setAttribute('fill', stroke);
    }
    const wantCompact = vis.length >= 6 || width < 560;
    if (wantCompact !== compact) setCompact(wantCompact);
    if (measureDirty) measurePins();
    declutter(vis);
    const ks = 1 - Math.exp(-Math.max(f.dt, 0.001) / 0.08);
    for (const pin of vis) {
      if (!pin.shown) {
        pin.shown = true;
        pin.px = pin.cx;
        pin.py = pin.cy;
      } else {
        pin.px += (pin.cx - pin.px) * ks;
        pin.py += (pin.cy - pin.py) * ks;
      }
      pin.el.style.transform = `translate3d(${(pin.px - pin.w / 2).toFixed(1)}px,${(pin.py - pin.h / 2).toFixed(1)}px,0)`;
      // leader: anchor -> nearest point on the chip rectangle
      const lx = clamp(pin.ax, pin.px - pin.w / 2, pin.px + pin.w / 2);
      const ly = clamp(pin.ay, pin.py - pin.h / 2, pin.py + pin.h / 2);
      const far = Math.hypot(lx - pin.ax, ly - pin.ay) > 3;
      pin.line.style.display = far ? '' : 'none';
      pin.line.setAttribute('x1', pin.ax.toFixed(1));
      pin.line.setAttribute('y1', pin.ay.toFixed(1));
      pin.line.setAttribute('x2', lx.toFixed(1));
      pin.line.setAttribute('y2', ly.toFixed(1));
      pin.node.setAttribute('cx', pin.ax.toFixed(1));
      pin.node.setAttribute('cy', pin.ay.toFixed(1));
    }
  }
  let lastPinW = 0;
  let uiClock = 0;
  function updatePinText(f: Frame): void {
    for (const k of PART_KEYS) {
      const pin = pins.get(k);
      if (!pin) continue;
      const txt = fmtValue(k, f.disp, f.state);
      if (txt !== pin.last) {
        pin.last = txt;
        pin.val.textContent = txt;
        pin.el.setAttribute('aria-label', `${LABEL[k]} ${txt}`);
        measureDirty = true;
      }
    }
    const line1 = `PHASE: ${PHASE_LABEL[f.state.phase]}`;
    const line2 = `f_ref ${REF_FREQ_HZ} Hz${f.state.stale ? ' · TELEMETRY STALE' : ''}`;
    const html = `${line1}<br>${line2}`;
    if (hud.dataset.txt !== html) {
      hud.dataset.txt = html;
      hud.innerHTML = '';
      hud.append(document.createTextNode(line1), document.createElement('br'), document.createTextNode(line2));
      measureDirty = true;
    }
  }

  /* --------------------------------------------------------------- frame */
  const frame: Frame = {
    t: 0, dt: 0, disp, state, live: true, flow: true, explode: 0, reduced, sweep: 0, measuring: false, sigmaRatio: 1, light,
  };

  function tick(dtIn: number): void {
    const dt = clamp(dtIn, 0, 0.1);
    const k = 1 - Math.exp(-dt / 0.7);
    const kf = 1 - Math.exp(-dt / 0.3);
    for (const key2 of Object.keys(tgt) as (keyof Disp)[]) {
      const kk = key2 === 'drop' || key2 === 'free' ? kf : k;
      disp[key2] += (tgt[key2] - disp[key2]) * kk;
    }
    xrayF += ((toggles.xray ? 1 : 0) - xrayF) * (1 - Math.exp(-dt / 0.2));
    if (Math.abs(xrayF - (toggles.xray ? 1 : 0)) < 0.002) xrayF = toggles.xray ? 1 : 0;
    applyGhosts(xrayF);
    const prevE = explodeF;
    explodeF += ((toggles.explode ? 1 : 0) - explodeF) * (1 - Math.exp(-dt / 0.28));
    if (Math.abs(explodeF - (toggles.explode ? 1 : 0)) < 0.001) explodeF = toggles.explode ? 1 : 0;
    if (explodeF !== prevE) model.setExplode(explodeF);

    if (tw.active) {
      tw.t0 += dt;
      const e = smooth(tw.t0 / tw.dur);
      camera.position.lerpVectors(tw.fromP, tw.toP, e);
      controls.target.lerpVectors(tw.fromT, tw.toT, e);
      if (e >= 1) tw.active = false;
    }
    controls.autoRotate = toggles.autoRotate && !reduced;
    controls.update();

    if (pointerIn && !isDown) setHover(pickAt());

    const live = !state.stale;
    if (live) animT += dt;
    const pt = phaseTargets(state);
    frame.t = animT;
    frame.dt = dt;
    frame.state = state;
    frame.live = live;
    frame.flow = toggles.flow;
    frame.explode = explodeF;
    frame.sweep = pt.sweep;
    frame.measuring = pt.measuring;
    frame.sigmaRatio = disp.sigma / Math.max(1, fin(state.baseline?.sigma, 150));
    frame.light = light;
    for (const c of model.components) c.update(frame);
    model.insetLabels.visible = toggles.labels;
    applyGlow(frame);
    uiClock += dt;
    if (uiClock > 0.25 || hud.dataset.txt === undefined) {
      uiClock = 0;
      updatePinText(frame);
    }
    updatePins(frame);
    renderer.render(scene, camera);
  }

  function loop(now: number): void {
    raf = 0;
    if (!shouldRun()) return;
    const t = now / 1000;
    const dt = lastT ? t - lastT : 0.016;
    lastT = t;
    try {
      tick(dt);
      failures = 0;
    } catch (err) {
      failures++;
      if (failures >= 5) {
        notice.textContent = '3D view stopped after repeated rendering errors. Reload the page to retry.';
        notice.style.display = 'flex';
        console.error('[bandage3d]', err);
        return;
      }
    }
    raf = requestAnimationFrame(loop);
  }
  function shouldRun(): boolean {
    return !disposed && onScreen && tabVisible && !lost && failures < 5;
  }
  function syncLoop(): void {
    if (shouldRun()) {
      if (!raf) {
        lastT = 0;
        raf = requestAnimationFrame(loop);
      }
    } else if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  /* -------------------------------------------------- observers / events */
  const ro = new ResizeObserver(() => {
    resize();
  });
  ro.observe(host);
  const io = new IntersectionObserver((entries) => {
    onScreen = entries.some((e) => e.isIntersecting);
    syncLoop();
  });
  io.observe(host);
  const onVis = () => {
    tabVisible = !document.hidden;
    syncLoop();
  };
  document.addEventListener('visibilitychange', onVis);
  const onLost = (e: Event) => {
    e.preventDefault();
    lost = true;
    notice.textContent = 'Graphics context lost -- waiting for the browser to restore it...';
    notice.style.display = 'flex';
    syncLoop();
  };
  const onRestored = () => {
    try {
      buildEnvironment();
    } catch (err) {
      console.error('[bandage3d] env rebuild failed', err);
    }
    lost = false;
    notice.style.display = 'none';
    resize();
    syncLoop();
  };
  canvas.addEventListener('webglcontextlost', onLost);
  canvas.addEventListener('webglcontextrestored', onRestored);

  /* ---------------------------------------------------------- public API */
  function disposeAll(): void {
    try {
      disposed = true;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      ro?.disconnect();
      io?.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('wheel', onWheel);
      themeObserver.disconnect();
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      controls.dispose();
      disposeTree(scene);
      scene.environment = null;
      envTarget?.dispose();
      envTarget = null;
      pmrem.dispose();
      renderer.dispose();
      overlay.remove();
      canvas.remove();
    } catch (err) {
      console.error('[bandage3d] dispose', err);
    }
  }

  // establish initial toggle-dependent state and paint one frame so the canvas is never blank
  model.insetLabels.visible = true;
  syncLoop();

  return {
    update(s: AssayState): void {
      if (disposed || !s) return;
      state = s;
      computeTargets();
      if (firstUpdate) {
        firstUpdate = false;
        (Object.keys(tgt) as (keyof Disp)[]).forEach((k) => {
          disp[k] = tgt[k];
        });
      }
    },
    setView(v): void {
      if (disposed) return;
      setView(v);
    },
    setToggles(t): void {
      if (disposed) return;
      const wasExplode = toggles.explode;
      if (t.xray !== undefined) toggles.xray = t.xray;
      if (t.flow !== undefined) toggles.flow = t.flow;
      if (t.labels !== undefined) toggles.labels = t.labels;
      if (t.autoRotate !== undefined) toggles.autoRotate = t.autoRotate;
      if (t.explode !== undefined && t.explode !== wasExplode) {
        toggles.explode = t.explode;
        // re-fit (keeping the current viewing direction) so the separated layers, or the re-assembled strip, stay framed
        if (tw.active) tmpV2.copy(tw.toP).sub(tw.toT);
        else tmpV2.copy(camera.position).sub(controls.target);
        const p = new THREE.Vector3();
        const tg = new THREE.Vector3();
        fitPose(tmpV2, currentView === 'macro', t.explode ? 1 : 0, p, tg);
        tweenTo(p, tg);
      }
    },
    select(part): void {
      if (disposed) return;
      setSelected(part, false);
    },
    tick(dt = 0.016): void {
      if (disposed || lost) return;
      tick(dt);
    },
    dispose(): void {
      if (disposed) return;
      disposeAll();
    },
  };
}
