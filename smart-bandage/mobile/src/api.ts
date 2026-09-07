/**
 * Mirrors frontend/src/api.ts against the same Phase 4 backend contract.
 */
import { API_BASE } from "./config";
import type {
  AIChatReply,
  AIChatTurn,
  AIInsight,
  AlertItem,
  Device,
  DeviceStatus,
  MeasurementRecord,
  PatientProfileInfo,
  TwinGroundTruth,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // body wasn't JSON -- keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  listDevices: (token?: string | null) => request<Device[]>("/devices", {}, token),

  registerDevice: (
    token: string,
    body: { device_id: string; name: string; firmware_version: string; channels: string[] },
  ) => request<Device>("/devices/register", { method: "POST", body: JSON.stringify(body) }, token),

  deviceStatus: (deviceId: string, channelId?: string) =>
    request<DeviceStatus>(
      `/device-status?${new URLSearchParams({ device_id: deviceId, ...(channelId ? { channel_id: channelId } : {}) })}`,
    ),

  measurements: (deviceId: string, channelId?: string, limit = 50) =>
    request<MeasurementRecord[]>(
      `/measurements?${new URLSearchParams({
        device_id: deviceId,
        ...(channelId ? { channel_id: channelId } : {}),
        limit: String(limit),
      })}`,
    ),

  alerts: (deviceId?: string) =>
    request<AlertItem[]>(`/alerts${deviceId ? `?${new URLSearchParams({ device_id: deviceId })}` : ""}`),

  startSimulation: (
    token: string,
    body: {
      device_id: string;
      channels: string[];
      scenario?: string;
      duration?: number;
      // DT-6: omit patient_profile for the ordinary scenario-backed run --
      // see components/TwinControlPanel.tsx.
      patient_profile?: string;
      time_scale?: number;
      twin_channel_id?: string;
    },
  ) => request<{ status: string; twin: boolean }>("/simulation/start", { method: "POST", body: JSON.stringify(body) }, token),

  stopSimulation: (token: string, deviceId: string) =>
    request<{ status: string }>(
      `/simulation/stop?${new URLSearchParams({ device_id: deviceId })}`,
      { method: "POST" },
      token,
    ),

  // DT-6: twin-backed mode -- components/TwinControlPanel.tsx.
  patientProfiles: () => request<PatientProfileInfo[]>("/simulation/patient-profiles"),

  // Dev-only "ground truth overlay" -- 404s in production (see
  // backend/app/routers/simulation.py) and whenever the device isn't
  // twin-backed / hasn't ticked yet. Used as the panel's first paint,
  // before the WS "twin_ground_truth" pushes take over.
  twinGroundTruth: (deviceId: string) => request<TwinGroundTruth>(`/simulation/twin/${encodeURIComponent(deviceId)}`),

  // Phase 10: AI analysis -- mirrors frontend/src/api.ts.
  aiInsight: (token: string, deviceId: string) =>
    request<AIInsight>(`/devices/${deviceId}/ai/insight`, {}, token),

  aiChat: (token: string, deviceId: string, message: string, history: AIChatTurn[]) =>
    request<AIChatReply>(
      `/devices/${deviceId}/ai/chat`,
      { method: "POST", body: JSON.stringify({ message, history }) },
      token,
    ),
};
