/** Small procedural helpers: seeded RNG, canvas textures, geometry builders, disposal. */
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const TAU = Math.PI * 2;

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const clamp = (v: number, a: number, b: number): number => Math.min(b, Math.max(a, v));
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
export const smooth = (t: number): number => {
  const x = clamp(t, 0, 1);
  return x * x * (3 - 2 * x);
};

export function roundedRectShape(w: number, d: number, r: number): THREE.Shape {
  const x = -w / 2;
  const y = -d / 2;
  const rr = Math.max(0.005, Math.min(r, w / 2, d / 2));
  const s = new THREE.Shape();
  s.moveTo(x + rr, y);
  s.lineTo(x + w - rr, y);
  s.absarc(x + w - rr, y + rr, rr, -Math.PI / 2, 0, false);
  s.lineTo(x + w, y + d - rr);
  s.absarc(x + w - rr, y + d - rr, rr, 0, Math.PI / 2, false);
  s.lineTo(x + rr, y + d);
  s.absarc(x + rr, y + d - rr, rr, Math.PI / 2, Math.PI, false);
  s.lineTo(x, y + rr);
  s.absarc(x + rr, y + rr, rr, Math.PI, Math.PI * 1.5, false);
  return s;
}

/** Flat rounded-rectangle slab: centred on x/z = 0, y in [0, h], bevelled edges. */
export function slabGeometry(w: number, d: number, h: number, r: number, bevel = 0): THREE.BufferGeometry {
  const b = Math.min(bevel, h * 0.45);
  const shape = roundedRectShape(w - 2 * b, d - 2 * b, Math.max(0.01, r - b));
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(0.001, h - 2 * b),
    bevelEnabled: b > 0,
    bevelThickness: b,
    bevelSize: b,
    bevelSegments: 2,
    curveSegments: 14,
    steps: 1,
  });
  g.rotateX(-Math.PI / 2);
  g.translate(0, b, 0);
  return g;
}

/** Hexagonal ring lying flat in the XZ plane. */
export function hexRingGeometry(rOuter: number, rInner: number): THREE.BufferGeometry {
  const g = new THREE.RingGeometry(rInner, rOuter, 6, 1);
  g.rotateX(-Math.PI / 2);
  return g;
}

export function merge(geos: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const m = mergeGeometries(geos, false);
  geos.forEach((g) => g.dispose());
  return m ?? new THREE.BufferGeometry();
}

/** Box segment from a to b (XZ-plane routing at constant y), used for meanders. */
export function segBox(a: THREE.Vector3, b: THREE.Vector3, w: number, h: number): THREE.BufferGeometry {
  const len = a.distanceTo(b) + w;
  const g = new THREE.BoxGeometry(len, h, w);
  const ang = Math.atan2(b.z - a.z, b.x - a.x);
  g.rotateY(-ang);
  g.translate((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
  return g;
}

/* ------------------------------------------------------------------ textures */

export function canvasTex(
  size: number,
  draw: (g: CanvasRenderingContext2D, s: number) => void,
  opts: { color?: boolean; repeat?: boolean; w?: number; h?: number } = {},
): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = opts.w ?? size;
  c.height = opts.h ?? size;
  const g = c.getContext('2d');
  if (g) draw(g, size);
  const t = new THREE.CanvasTexture(c);
  if (opts.repeat !== false) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  if (opts.color) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

/** Woven film bump: alternating over/under threads. */
export function weaveTexture(): THREE.CanvasTexture {
  return canvasTex(128, (g, s) => {
    g.fillStyle = '#808080';
    g.fillRect(0, 0, s, s);
    const n = 8;
    const c = s / n;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const over = (i + j) % 2 === 0;
        const gr = over ? g.createLinearGradient(0, j * c, 0, (j + 1) * c) : g.createLinearGradient(i * c, 0, (i + 1) * c, 0);
        gr.addColorStop(0, '#3a3a3a');
        gr.addColorStop(0.5, '#f2f2f2');
        gr.addColorStop(1, '#3a3a3a');
        g.fillStyle = gr;
        g.fillRect(i * c + 1, j * c + 1, c - 2, c - 2);
      }
    }
  });
}

/** Soft value-noise bump (fibrous pad / skin). */
export function noiseTexture(seed: number, size = 128, cells = 40): THREE.CanvasTexture {
  const rng = mulberry32(seed);
  return canvasTex(size, (g, s) => {
    const small = document.createElement('canvas');
    small.width = small.height = cells;
    const sg = small.getContext('2d');
    if (!sg) return;
    for (let y = 0; y < cells; y++) {
      for (let x = 0; x < cells; x++) {
        const v = Math.floor(90 + rng() * 120);
        sg.fillStyle = `rgb(${v},${v},${v})`;
        sg.fillRect(x, y, 1, 1);
      }
    }
    g.imageSmoothingEnabled = true;
    g.drawImage(small, 0, 0, s, s);
  });
}

/** Skin pore bump: dark dimples on mid-grey. */
export function poreTexture(seed: number): THREE.CanvasTexture {
  const rng = mulberry32(seed);
  return canvasTex(128, (g, s) => {
    g.fillStyle = '#9a9a9a';
    g.fillRect(0, 0, s, s);
    for (let i = 0; i < 70; i++) {
      const x = rng() * s;
      const y = rng() * s;
      const r = 2 + rng() * 3;
      const gr = g.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, '#1a1a1a');
      gr.addColorStop(1, 'rgba(154,154,154,0)');
      g.fillStyle = gr;
      g.beginPath();
      g.arc(x, y, r, 0, TAU);
      g.fill();
    }
  });
}

/** White radial falloff, tinted through material.color (halo sprites). */
export function glowTexture(): THREE.CanvasTexture {
  return canvasTex(
    128,
    (g, s) => {
      const gr = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
      gr.addColorStop(0, 'rgba(255,255,255,1)');
      gr.addColorStop(0.35, 'rgba(255,255,255,0.45)');
      gr.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = gr;
      g.fillRect(0, 0, s, s);
    },
    { repeat: false },
  );
}

/** Text on transparent background (chip marking, battery label). */
export function textTexture(lines: string[], opts: { color: string; font?: string; dot?: boolean; size?: number } ): THREE.CanvasTexture {
  return canvasTex(
    opts.size ?? 256,
    (g, s) => {
      g.clearRect(0, 0, s, s);
      g.fillStyle = opts.color;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      const fs = s / (lines.length + 2.2);
      g.font = opts.font ?? `600 ${fs}px "Inter", Arial, sans-serif`;
      lines.forEach((ln, i) => g.fillText(ln, s / 2, s / 2 + (i - (lines.length - 1) / 2) * fs * 1.15));
      if (opts.dot) {
        g.beginPath();
        g.arc(s * 0.16, s * 0.16, s * 0.05, 0, TAU);
        g.fill();
      }
    },
    { color: true, repeat: false },
  );
}

/** Pill-shaped label sprite for the macro inset. */
export function labelSprite(text: string, worldHeight = 0.2): THREE.Sprite {
  const c = document.createElement('canvas');
  const g = c.getContext('2d');
  const fs = 44;
  const font = `600 ${fs}px "Inter", Arial, sans-serif`;
  let w = 256;
  if (g) {
    g.font = font;
    w = Math.ceil(g.measureText(text).width) + 56;
  }
  c.width = w;
  c.height = 80;
  const g2 = c.getContext('2d');
  if (g2) {
    g2.fillStyle = 'rgba(12,14,20,0.74)';
    g2.beginPath();
    g2.roundRect(2, 2, w - 4, 76, 38);
    g2.fill();
    g2.strokeStyle = 'rgba(140,170,255,0.55)';
    g2.lineWidth = 3;
    g2.stroke();
    g2.font = font;
    g2.fillStyle = '#f2f5ff';
    g2.textAlign = 'center';
    g2.textBaseline = 'middle';
    g2.fillText(text, w / 2, 42);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const m = new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false, depthTest: false, toneMapped: false });
  const sp = new THREE.Sprite(m);
  sp.scale.set((w / 80) * worldHeight, worldHeight, 1);
  sp.renderOrder = 20;
  return sp;
}

/* ------------------------------------------------------------------ disposal */

export function disposeTree(root: THREE.Object3D): void {
  root.traverse((o) => {
    const any = o as THREE.Object3D & {
      geometry?: THREE.BufferGeometry;
      material?: THREE.Material | THREE.Material[];
      isInstancedMesh?: boolean;
      dispose?: () => void;
    };
    if (any.isInstancedMesh && typeof any.dispose === 'function') any.dispose();
    if (any.geometry) any.geometry.dispose();
    const mats = any.material ? (Array.isArray(any.material) ? any.material : [any.material]) : [];
    for (const m of mats) {
      for (const v of Object.values(m)) {
        if (v instanceof THREE.Texture) v.dispose();
      }
      m.dispose();
    }
  });
}

/**
 * Theme a glow material: additive + bright colour on dark backgrounds, normal blending + darker saturated
 * colour on light ones (additive light on a light page is invisible).
 */
export function setGlowTheme(
  m: THREE.MeshBasicMaterial | THREE.PointsMaterial,
  light: boolean,
  darkHex: number,
  lightHex: number,
  sizeMul = 1.35,
): void {
  m.color.setHex(light ? lightHex : darkHex);
  m.blending = light ? THREE.NormalBlending : THREE.AdditiveBlending;
  if (m instanceof THREE.PointsMaterial) {
    const base = (m.userData.baseSize as number | undefined) ?? m.size;
    m.userData.baseSize = base;
    m.size = light ? base * sizeMul : base;
  }
  m.needsUpdate = true;
}

/** Soft round sprite points with a per-vertex `aFade` alpha (readable on light and dark themes). */
export function fadePointsMaterial(map: THREE.Texture, size: number, color: number): THREE.PointsMaterial {
  const m = new THREE.PointsMaterial({ size, map, color, transparent: true, depthWrite: false, sizeAttenuation: true, toneMapped: false });
  m.onBeforeCompile = (sh) => {
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aFade;\nvarying float vFade;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvFade = aFade;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vFade;')
      .replace('#include <opaque_fragment>', '#include <opaque_fragment>\ngl_FragColor.a *= vFade;');
  };
  return m;
}
