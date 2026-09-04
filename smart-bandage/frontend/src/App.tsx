import { useEffect, useMemo, useState } from "react";
import { api, setTokenExpiredHandler, setUnauthorizedHandler } from "./api";
import { AIInsightPanel } from "./components/AIInsightPanel";
import { AlertsPanel } from "./components/AlertsPanel";
import { IconActivity } from "./components/icons";
import { LoginScreen } from "./components/LoginScreen";
import { MeasurementChart } from "./components/MeasurementChart";
import { RadialGauge } from "./components/RadialGauge";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { useDeviceSocket } from "./hooks/useDeviceSocket";
import type { AlertItem, Device, DeviceStatus, MeasurementRecord } from "./types";

const TOKEN_KEY = "smart-bandage.token";
const REFRESH_TOKEN_KEY = "smart-bandage.refresh_token";

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [history, setHistory] = useState<MeasurementRecord[]>([]);
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [sessionExpired, setSessionExpired] = useState(false);

  const { connected: wsConnected, lastMeasurement, liveMeasurements, liveAlerts } = useDeviceSocket(selectedDeviceId);

  function handleLogin(newToken: string, newRefreshToken: string) {
    localStorage.setItem(TOKEN_KEY, newToken);
    localStorage.setItem(REFRESH_TOKEN_KEY, newRefreshToken);
    setSessionExpired(false);
    setToken(newToken);
  }

  function handleLogout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    setToken(null);
    setDevices([]);
    setSelectedDeviceId(null);
  }

  // A stale/expired token is only discovered when some authenticated
  // request 401s, so react to that globally rather than relying on every
  // call site to notice and log out.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setSessionExpired(true);
      handleLogout();
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // This is what keeps an already-signed-in device signed in: the first
  // 401 on any authenticated call transparently swaps the stored refresh
  // token for a fresh access+refresh pair instead of bouncing to the login
  // screen. Only a genuinely dead/expired refresh token (or none stored)
  // falls through to onUnauthorized above.
  useEffect(() => {
    setTokenExpiredHandler(async () => {
      const storedRefreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
      if (!storedRefreshToken) return null;
      try {
        const { access_token, refresh_token } = await api.refresh(storedRefreshToken);
        localStorage.setItem(TOKEN_KEY, access_token);
        localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token);
        setToken(access_token);
        return access_token;
      } catch {
        return null;
      }
    });
    return () => setTokenExpiredHandler(null);
  }, []);

  // load devices once authenticated
  useEffect(() => {
    if (!token) return;
    api
      .listDevices()
      .then((list) => {
        setDevices(list);
        setSelectedDeviceId((current) => current ?? list[0]?.device_id ?? null);
      })
      .catch(() => {
        /* device list is non-critical to show on first load */
      });
  }, [token]);

  const selectedDevice = useMemo(
    () => devices.find((d) => d.device_id === selectedDeviceId) ?? null,
    [devices, selectedDeviceId],
  );

  useEffect(() => {
    setSelectedChannel(selectedDevice?.channels[0] ?? null);
  }, [selectedDevice]);

  // history + status + alerts, refreshed on device switch and then polled
  useEffect(() => {
    if (!selectedDeviceId) {
      setHistory([]);
      setStatus(null);
      setAlerts([]);
      return;
    }

    let cancelled = false;
    const refresh = () => {
      api
        .measurements(selectedDeviceId, selectedChannel ?? undefined, 200)
        .then((records) => !cancelled && setHistory(records.slice().reverse()))
        .catch(() => {});
      api
        .deviceStatus(selectedDeviceId, selectedChannel ?? undefined)
        .then((s) => !cancelled && setStatus(s))
        .catch(() => !cancelled && setStatus(null));
      api
        .alerts(selectedDeviceId)
        .then((a) => !cancelled && setAlerts(a))
        .catch(() => {});
    };

    refresh();
    const interval = setInterval(refresh, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedDeviceId, selectedChannel]);

  const chartRecords = useMemo(() => {
    const merged = [...history, ...liveMeasurements.filter((m) => m.channel_id === (selectedChannel ?? m.channel_id))];
    return merged.slice(-300);
  }, [history, liveMeasurements, selectedChannel]);

  // A single 0-1 score blending connectivity/battery/signal quality, shown
  // as the ring around the device's monogram in the header -- a
  // sense-at-a-glance "is this device basically fine?" that a raw list of
  // stat tiles doesn't give you.
  const healthScore = useMemo(() => {
    if (!status) return null;
    const parts: number[] = [status.connected ? 1 : 0];
    if (status.battery !== null) parts.push(status.battery / 100);
    if (status.signal_quality !== null) parts.push(status.signal_quality);
    return parts.reduce((a, b) => a + b, 0) / parts.length;
  }, [status]);

  const healthColor = healthScore === null ? "#94a3b8" : healthScore < 0.5 ? "#f43f5e" : healthScore < 0.8 ? "#f59e0b" : "#10b981";

  const combinedAlerts = useMemo(() => {
    const seen = new Set<string>();
    const combined: AlertItem[] = [];
    for (const a of [...liveAlerts, ...alerts]) {
      const key = a.id !== null ? String(a.id) : `${a.timestamp}-${a.message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      combined.push(a);
    }
    return combined.slice(0, 30);
  }, [liveAlerts, alerts]);

  if (!token) return <LoginScreen onLogin={handleLogin} notice={sessionExpired ? "Your session expired. Please sign in again." : null} />;

  return (
    <div className="flex min-h-screen">
      <Sidebar
        devices={devices}
        selectedDeviceId={selectedDeviceId}
        selectedDevice={selectedDevice}
        onSelect={setSelectedDeviceId}
        onDeviceRegistered={(d) => setDevices((prev) => [...prev.filter((x) => x.device_id !== d.device_id), d])}
        token={token}
        onLogout={handleLogout}
      />

      <main className="flex-1 space-y-6 overflow-y-auto p-6 lg:p-8">
        {!selectedDevice ? (
          <div className="flex h-[70vh] flex-col items-center justify-center gap-3 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-slate-200 bg-white text-amber-600 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:text-amber-300 dark:shadow-none">
              <IconActivity className="h-6 w-6" />
            </div>
            <p className="font-display text-base font-medium text-slate-700 dark:text-slate-200">No device selected</p>
            <p className="max-w-xs text-sm text-slate-500 dark:text-slate-500">Select a device from the sidebar, or register a new one to get started.</p>
          </div>
        ) : (
          <>
            <header className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_30px_rgba(15,23,42,0.06)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-[0_8px_30px_rgba(0,0,0,0.35)] dark:backdrop-blur-xl">
              <div className="flex items-center gap-3.5">
                <RadialGauge
                  value={healthScore ?? 0}
                  size={46}
                  strokeWidth={3.5}
                  color={healthColor}
                  trackClassName="text-slate-100 dark:text-white/[0.06]"
                  label={selectedDevice.name.charAt(0).toUpperCase()}
                />
                <div>
                  <div className="flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                      {wsConnected && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
                      <span className={`relative inline-flex h-2 w-2 rounded-full ${wsConnected ? "bg-emerald-500" : "bg-slate-400 dark:bg-slate-600"}`} />
                    </span>
                    <p
                      className={`font-mono text-[11px] uppercase tracking-widest ${wsConnected ? "text-emerald-600 dark:text-emerald-300/80" : "text-slate-500"}`}
                    >
                      {wsConnected ? "Live monitoring" : "Disconnected"}
                    </p>
                  </div>
                  <h1 className="font-display mt-1 text-xl font-semibold tracking-tight text-slate-900 dark:text-white">{selectedDevice.name}</h1>
                  <p className="mt-0.5 font-mono text-xs text-slate-500">
                    {selectedDevice.device_id} · fw {selectedDevice.firmware_version}
                  </p>
                </div>
              </div>
              {selectedDevice.channels.length > 1 && (
                <select
                  value={selectedChannel ?? ""}
                  onChange={(e) => setSelectedChannel(e.target.value)}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
                >
                  {selectedDevice.channels.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
            </header>

            <StatusBar status={status} wsConnected={wsConnected} lastMeasurement={lastMeasurement} records={chartRecords} />

            <MeasurementChart records={chartRecords} />

            <div className="grid gap-6 lg:grid-cols-2">
              <AlertsPanel alerts={combinedAlerts} />
              <AIInsightPanel token={token} deviceId={selectedDevice.device_id} />
            </div>
          </>
        )}
      </main>
    </div>
  );
}
