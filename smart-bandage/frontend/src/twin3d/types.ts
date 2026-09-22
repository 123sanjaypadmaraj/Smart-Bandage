/**
 * Shared contract for the smart-bandage 3D digital twin.
 * Owned by the coordinator -- the 3D scene, the EIS model and the EIS panel all
 * import from here, so do NOT change existing names/shapes without telling the others.
 *
 * Sensor: graphene-PBASE-aptamer electrode read by electrochemical impedance
 * spectroscopy (EIS) against E. coli. Equivalent circuit = modified Randles:
 *
 *   Z(w) = Rs + 1 / ( 1/(Rct + Zw(w)) + Q*(j*w)^n )        (Q*(jw)^n is the CPE for Cdl)
 *   Zw(w) = sigma / sqrt(w) * (1 - j)                       (semi-infinite Warburg)
 *   Cdl (reported) = Q^(1/n) * (1/Rs + 1/Rct)^((n-1)/n)  -- or simply Q if n == 1; either is
 *                    fine as long as it is documented and monotone in Q.
 *
 * Binding (Langmuir):  theta = C / (Kd + C)   with C in CFU/mL
 *   Rct   = Rct0   * (1 + A_RCT   * theta)   bacteria insulate the surface -> harder charge transfer
 *   Cdl   = Cdl0   * (1 - A_CDL   * theta)   thicker dielectric layer -> lower double-layer capacitance
 *   Rs    = Rs0    * (1 + A_RS    * theta)   (small)
 *   sigma = sigma0 * (1 + A_SIGMA * theta)   diffusion of redox probe hindered
 * Defaults: Rs0=120 ohm, Rct0=2000 ohm, Cdl0=2.0 uF, sigma0=150 ohm*s^-0.5, n=0.92, Kd=1e4 CFU/mL,
 *   A_RCT=1.6, A_CDL=0.35, A_RS=0.04, A_SIGMA=0.5. Sweep 0.1 Hz .. 100 kHz, 10 pts/decade, amplitude 10 mV.
 * Reference frequency for the single-number readout (|Z| and phase): 100 Hz.
 * Detection: dRct% = (Rct_post - Rct_base)/Rct_base*100 ; positive if dRct% > LOD threshold
 *   (calibration: dRct% = slope * log10(CFU/mL) + intercept, slope ~ 54.8 %/decade, intercept ~ -136 % (fitted to the model over 1e2..1e5 CFU/mL),
 *    threshold = 3 * baseline-noise SD (default 3 * 1.5 % = 4.5 %)).
 */

/** Linearised sensor parameters -- these ARE the "sensor parameters" the user asked for. */
export interface EisParams {
  rs: number;      // solution resistance, ohm
  rct: number;     // charge-transfer (interfacial) resistance, ohm
  cdl: number;     // double-layer capacitance, farad
  sigma: number;   // Warburg coefficient (diffusion), ohm*s^-1/2
  n: number;       // CPE exponent (1 = ideal capacitor)
}

export interface ImpedancePoint {
  freq: number;    // Hz
  re: number;      // ohm  (Z')
  im: number;      // ohm  (Z'' -- store the physical value, i.e. NEGATIVE for capacitive; plots use -im)
  mag: number;     // |Z| ohm
  phase: number;   // degrees (negative for capacitive)
}

export type EisSpectrum = ImpedancePoint[];

/** Workflow steps -- mirror the conceptual sequence in the brief. */
export type AssayPhase =
  | 'idle'       // modified electrode, nothing measured yet
  | 'baseline'   // baseline impedance measurement (clean sensor)
  | 'exposure'   // sample containing E. coli contacts the sensing surface
  | 'binding'    // target binding (if present) -- coverage theta ramps up
  | 'measure2'   // second impedance measurement
  | 'compare'    // compare with baseline
  | 'result';    // impedance change (dZ) -> detection result

export const ASSAY_PHASES: AssayPhase[] = [
  'idle', 'baseline', 'exposure', 'binding', 'measure2', 'compare', 'result',
];

export const PHASE_LABEL: Record<AssayPhase, string> = {
  idle: 'Modified electrode',
  baseline: 'Baseline impedance',
  exposure: 'Sample exposure',
  binding: 'Target binding',
  measure2: 'Second impedance measurement',
  compare: 'Compare with baseline',
  result: 'Impedance change (ΔZ) → detection',
};

/** Everything the 3D scene and the panel need for one render frame. */
export interface AssayState {
  phase: AssayPhase;
  /** progress 0..1 inside the current phase (drives animations / stepper fill) */
  phaseProgress: number;
  /** E. coli concentration in the sample, CFU/mL (0 = clean control) */
  cfuPerMl: number;
  /** fractional aptamer occupancy 0..1 (drives how many bacteria sit on the surface) */
  coverage: number;
  baseline: EisParams | null;   // null until baseline step done
  current: EisParams;           // live sensor parameters (== baseline before exposure)
  baselineSpectrum: EisSpectrum | null;
  currentSpectrum: EisSpectrum;
  /** single-frequency readout at REF_FREQ_HZ */
  zMag: number;                 // ohm
  zPhase: number;               // deg
  /** ΔZ readouts (null until baseline exists). percentages relative to baseline. */
  deltaZMagPct: number | null;  // (|Z|post-|Z|base)/|Z|base*100 at REF_FREQ_HZ
  deltaRctPct: number | null;
  deltaCdlPct: number | null;
  detected: boolean | null;     // null until phase === 'result'
  /** estimated concentration from the calibration curve, CFU/mL (null if below LOD) */
  estimatedCfuPerMl: number | null;
  stale: boolean;               // feed stalled -> scene freezes motion
}

export const REF_FREQ_HZ = 100;

/** Selectable parts in the 3D scene; keys double as EIS parameter keys where they exist. */
export type PartKey =
  | 'rs' | 'rct' | 'cdl' | 'warburg'      // equivalent-circuit elements (shown on the electrode / interface)
  | 'zmag' | 'phase' | 'deltaZ'           // readouts (shown on the AFE / potentiostat)
  | 'coverage'                            // aptamer occupancy (bacteria on the surface)
  | 'battery';                            // coin cell / power

export const PART_KEYS: PartKey[] = [
  'rs', 'rct', 'cdl', 'warburg', 'zmag', 'phase', 'deltaZ', 'coverage', 'battery',
];

export type PartStatus = 'nominal' | 'warning' | 'critical';

/** Imperative handle exposed by the 3D component (mirrors the engine guide's window.twin3d). */
export interface BandageScene {
  update(state: AssayState): void;
  setView(v: 'iso' | 'front' | 'side' | 'top' | 'macro'): void;
  setToggles(t: Partial<{ xray: boolean; explode: boolean; flow: boolean; labels: boolean; autoRotate: boolean }>): void;
  select(part: PartKey | null): void;
  /** step one frame manually (tests / hidden tab), like the engine guide's twin3d.tick */
  tick(dt?: number): void;
  dispose(): void;
}
