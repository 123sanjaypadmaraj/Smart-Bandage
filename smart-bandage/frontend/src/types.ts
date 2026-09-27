/**
 * Mirrors backend/app/schemas.py and common/schemas/*.py -- the dashboard's
 * half of the Phase 1 contract. Keep these in lockstep with the backend
 * models by hand for now; docs/api/openapi.yaml is the source of truth if
 * they ever drift.
 */

export type DeviceConnectionStatus = "online" | "offline" | "unknown";

export interface Device {
  device_id: string;
  name: string;
  firmware_version: string;
  channels: string[];
  status: DeviceConnectionStatus;
  last_seen: string | null;
}

export interface DeviceStatus {
  connected: boolean;
  battery: number | null;
  signal_quality: number | null;
  last_error: string | null;
}

export type MeasurementStatus = "valid" | "invalid" | "error";

export interface MeasurementRecord {
  device_id: string;
  channel_id: string;
  timestamp: string;
  raw_signal: number;
  processed_signal: number | null;
  estimated_value: number | null;
  unit: string | null;
  signal_quality: number | null;
  temperature: number | null;
  battery: number | null;
  status: MeasurementStatus;
}

export type AlertType =
  | "INFO"
  | "WARNING"
  | "CRITICAL"
  | "DEVICE_ERROR"
  | "SENSOR_ERROR"
  // Phase 6 intelligence engine (backend/app/intelligence.py) -- history-based,
  // vs. the single-reading types above
  | "RAPID_TREND"
  | "SUSTAINED_TREND"
  | "QUALITY_DEGRADING";
export type AlertSeverity = "info" | "warning" | "critical";

export interface AlertItem {
  id: number | null;
  type: AlertType;
  severity: AlertSeverity;
  device_id: string;
  channel: string | null;
  message: string;
  timestamp: string;
  resolved: boolean;
}

export const SCENARIOS = [
  "normal",
  "rising_concentration",
  "sudden_abnormal",
  "electrode_degradation",
  "high_noise",
  "sensor_disconnect",
  "comms_failure",
] as const;

export type Scenario = (typeof SCENARIOS)[number];

// DT-6: twin-backed simulation (backend/app/simulation.py's DigitalTwinDevice
// integration) -- a patient profile plus a time-scale multiplier, instead
// of a scenario script, driving one channel. The dashboard's twin control
// panel (components/TwinControlPanel.tsx) is the only thing that reads
// these; a device running the ordinary scenario-backed simulation never
// produces them.

export interface PatientProfileInfo {
  name: string;
  description: string;
}

export interface TwinGroundTruth {
  device_id: string;
  channel_id: string;
  patient_profile: string;
  time_scale: number;
  inflammation: number;
  bacterial_load: number;
  moisture: number;
  perfusion: number;
  true_signal: number;
  estimated_signal: number | null;
  timestamp: string;
}

// DT-3 bandage twin (digital_twin/bandage's BandageTwin, opted into via
// POST /simulation/start's bandage_design) -- a physically-modelled
// bandage rather than DT-6's single hidden-state channel. Field names
// below are the Python side's, verbatim: the conversion into the 3D twin's
// camelCase AssayState lives in exactly one place (twin3d/useLiveAssay.ts)
// so these stay a byte-for-byte mirror of the wire.

export interface BandageDesignInfo {
  name: string;
  description: string;
}

/** Randles-circuit parameters as the backend sends them. Same names and
 * units as twin3d/types.ts's EisParams (cdl in farads, not uF), so these
 * are structurally interchangeable. */
export interface TwinEisParams {
  rs: number;
  rct: number;
  cdl: number;
  sigma: number;
  n: number;
}

/** One sweep point, Python naming: `phase_deg` where twin3d's
 * ImpedancePoint says `phase` (both degrees). */
export interface TwinEisSpectrumPoint {
  freq: number;
  re: number;
  im: number;
  mag: number;
  phase_deg: number;
}

/** The `eis` body of a twin_eis message -- one channel's latest sweep. */
export interface TwinEisSnapshot {
  channel_id: string;
  /** simulated (physiological) seconds since the twin started */
  t_s: number;
  local_concentration_nM: number;
  theta: number;
  cfu_equivalent_per_ml: number;
  baseline_params: TwinEisParams;
  params: TwinEisParams;
  spectrum: TwinEisSpectrumPoint[];
  z_ref: { freq: number; mag: number; phase_deg: number };
  delta: { z_ref_pct: number; rct_pct: number; cdl_pct: number };
  detection: { detected: boolean; estimated_cfu: number | null };
}

/** A twin_eis push minus its `type` tag. Unlike twin_ground_truth this is
 * a real device measurement, so the backend sends it in production too. */
export interface TwinEisReading {
  device_id: string;
  channel_id: string;
  timestamp: string;
  eis: TwinEisSnapshot;
}

export interface TwinBandageChannelTruth {
  local_concentration: number;
  wound_concentration: number;
  ideal_au: number;
  sensor_health: number;
  fouling: number;
}

/** The hidden physical state behind a bandage twin -- the DT-3 analogue
 * of TwinGroundTruth. */
export interface TwinBandageTruthState {
  bacterial_load: number;
  inflammation: number;
  moisture: number;
  perfusion: number;
  analytes: Record<string, number>;
  contact: number;
  link_quality: number;
  battery_pct: number;
  channels: Record<string, TwinBandageChannelTruth>;
}

/** A twin_bandage_truth push minus its `type` tag. Dev-only, like
 * twin_ground_truth: the backend never broadcasts it in production. */
export interface TwinBandageTruth {
  device_id: string;
  bandage_design: string;
  timestamp: string;
  truth: TwinBandageTruthState;
}

export type DeviceMessage =
  | { type: "measurement"; measurement: MeasurementRecord }
  | { type: "alert"; alert: AlertItem }
  | { type: "twin_ground_truth"; ground_truth: TwinGroundTruth }
  | ({ type: "twin_eis" } & TwinEisReading)
  | ({ type: "twin_bandage_truth" } & TwinBandageTruth);

// Phase 10: AI analysis (backend/app/schemas.py, backend/app/routers/ai.py)

export interface AIInsight {
  device_id: string;
  summary: string;
  model: string;
  generated_at: string;
}

export interface AIChatTurn {
  role: "user" | "model";
  text: string;
}

export interface AIChatReply {
  device_id: string;
  reply: string;
  model: string;
}
