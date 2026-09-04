import type { AIChatReply, AIChatTurn, AIInsight, AlertItem, Device, DeviceStatus, MeasurementRecord } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
export const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? "ws://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Fired when an authenticated request comes back 401 *and* a silent
// refresh (below) either isn't wired up or didn't work -- i.e. the device
// isn't signed in after all. App.tsx wires this up to drop the dead tokens
// and return to the login screen instead of leaving the user stuck
// re-clicking a dead button.
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

// Fired the first time an authenticated request 401s, i.e. the access
// token expired. App.tsx wires this to POST /auth/refresh with the stored
// refresh token and, on success, returns the new access token so `request`
// can retry the original call transparently -- this is what keeps an
// already-signed-in device from ever seeing the login screen again.
let onTokenExpired: (() => Promise<string | null>) | null = null;
export function setTokenExpiredHandler(handler: (() => Promise<string | null>) | null) {
  onTokenExpired = handler;
}

async function request<T>(path: string, options: RequestInit = {}, token?: string | null): Promise<T> {
  const doFetch = (authToken?: string | null) => {
    const headers = new Headers(options.headers);
    if (options.body) headers.set("Content-Type", "application/json");
    if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
    return fetch(`${API_BASE}${path}`, { ...options, headers });
  };

  let res = await doFetch(token);
  if (res.status === 401 && token && onTokenExpired) {
    const refreshedToken = await onTokenExpired();
    if (refreshedToken) res = await doFetch(refreshedToken);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // body wasn't JSON -- keep statusText
    }
    // Only treat this as "session expired" when we actually sent a token --
    // a 401 on an unauthenticated call (e.g. a bad login attempt) is not that.
    if (res.status === 401 && token) onUnauthorized?.();
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; refresh_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  // Swaps a refresh token for a fresh access+refresh pair. Deliberately
  // called without a `token` argument -- it authenticates via the refresh
  // token in the body, not a Bearer header, so it can't itself trigger the
  // 401 -> onTokenExpired retry above.
  refresh: (refreshToken: string) =>
    request<{ access_token: string; refresh_token: string; token_type: string }>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  listDevices: () => request<Device[]>("/devices"),

  registerDevice: (
    token: string,
    body: { device_id: string; name: string; firmware_version: string; channels: string[] },
  ) => request<Device>("/devices/register", { method: "POST", body: JSON.stringify(body) }, token),

  deviceStatus: (deviceId: string, channelId?: string) =>
    request<DeviceStatus>(
      `/device-status?${new URLSearchParams({ device_id: deviceId, ...(channelId ? { channel_id: channelId } : {}) })}`,
    ),

  measurements: (deviceId: string, channelId?: string, limit = 200) =>
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
    body: { device_id: string; channels: string[]; scenario: string; duration?: number },
  ) => request<{ status: string }>("/simulation/start", { method: "POST", body: JSON.stringify(body) }, token),

  stopSimulation: (token: string, deviceId: string) =>
    request<{ status: string }>(
      `/simulation/stop?${new URLSearchParams({ device_id: deviceId })}`,
      { method: "POST" },
      token,
    ),

  setScenario: (token: string, body: { device_id: string; scenario: string; channel_id?: string }) =>
    request<{ status: string }>("/simulation/scenario", { method: "POST", body: JSON.stringify(body) }, token),

  // Phase 10: AI analysis -- both require auth; a 503 means GEMINI_API_KEY
  // isn't set on the backend, a 404 means the device has no readings yet.
  aiInsight: (token: string, deviceId: string) =>
    request<AIInsight>(`/devices/${deviceId}/ai/insight`, {}, token),

  aiChat: (token: string, deviceId: string, message: string, history: AIChatTurn[]) =>
    request<AIChatReply>(
      `/devices/${deviceId}/ai/chat`,
      { method: "POST", body: JSON.stringify({ message, history }) },
      token,
    ),
};
