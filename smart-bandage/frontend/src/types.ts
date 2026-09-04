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

export type DeviceMessage =
  | { type: "measurement"; measurement: MeasurementRecord }
  | { type: "alert"; alert: AlertItem };

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
