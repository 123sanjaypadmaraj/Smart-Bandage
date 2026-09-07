/**
 * Mirrors frontend/src/types.ts, which mirrors backend/app/schemas.py and
 * common/schemas/*.py -- the mobile app's half of the Phase 1 contract.
 * Keep in lockstep with the backend by hand for now; docs/api/openapi.yaml
 * is the source of truth if they ever drift.
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

// DT-6: twin-backed simulation (backend/app/simulation.py's DigitalTwinDevice
// integration) -- a patient profile plus a time-scale multiplier, instead of
// a scenario script, driving one channel. Mirrors frontend/src/types.ts;
// components/TwinControlPanel.tsx is the only thing that reads these.

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

export type DeviceMessage =
  | { type: "measurement"; measurement: MeasurementRecord }
  | { type: "alert"; alert: AlertItem }
  | { type: "twin_ground_truth"; ground_truth: TwinGroundTruth };

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
