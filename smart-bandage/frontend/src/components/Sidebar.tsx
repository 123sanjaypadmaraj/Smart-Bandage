import { FormEvent, useState } from "react";
import { api, ApiError } from "../api";
import type { Device } from "../types";
import { SimulationControls } from "./SimulationControls";
import { ThemeToggle } from "./ThemeToggle";
import { IconHeartPulse, IconLogOut, IconPlus } from "./icons";

interface SidebarProps {
  devices: Device[];
  selectedDeviceId: string | null;
  selectedDevice: Device | null;
  onSelect: (deviceId: string) => void;
  onDeviceRegistered: (device: Device) => void;
  token: string;
  onLogout: () => void;
}

export function Sidebar({ devices, selectedDeviceId, selectedDevice, onSelect, onDeviceRegistered, token, onLogout }: SidebarProps) {
  const [showForm, setShowForm] = useState(false);
  const [deviceId, setDeviceId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleRegister(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const device = await api.registerDevice(token, {
        device_id: deviceId,
        name: name || deviceId,
        firmware_version: "0.1.0",
        channels: ["CH-01"],
      });
      onDeviceRegistered(device);
      onSelect(device.device_id);
      setShowForm(false);
      setDeviceId("");
      setName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not register device");
    }
  }

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-slate-200 bg-white/70 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.02]">
      <div className="flex items-center gap-3 border-b border-slate-200 p-4 dark:border-white/10">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-amber-400 to-[#16233f] text-white shadow-[0_0_20px_rgba(245,158,11,0.35)]">
          <IconHeartPulse className="h-4.5 w-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[10px] uppercase tracking-widest text-amber-600 dark:text-amber-300/80">Smart Bandage</p>
          <h1 className="font-display truncate text-sm font-semibold text-slate-900 dark:text-slate-100">Devices</h1>
        </div>
        <ThemeToggle />
      </div>

      <nav className="flex-1 space-y-1.5 overflow-y-auto p-3">
        {devices.length === 0 && (
          <p className="px-2 py-4 text-xs text-slate-500">No devices yet -- register one below.</p>
        )}
        {devices.map((d) => {
          const active = d.device_id === selectedDeviceId;
          const dotColor = d.status === "online" ? "bg-emerald-500" : d.status === "offline" ? "bg-rose-500" : "bg-slate-400 dark:bg-slate-600";
          const dotGlow =
            d.status === "online"
              ? "shadow-[0_0_8px_2px_rgba(16,185,129,0.5)]"
              : d.status === "offline"
                ? "shadow-[0_0_8px_2px_rgba(244,63,94,0.5)]"
                : "";
          return (
            <button
              key={d.device_id}
              onClick={() => onSelect(d.device_id)}
              className={`group relative flex w-full items-center gap-2.5 overflow-hidden rounded-lg border px-2.5 py-2 text-left text-sm transition ${
                active
                  ? "border-amber-400/40 bg-gradient-to-r from-amber-400/10 to-[#16233f]/10 text-amber-700 dark:text-amber-200"
                  : "border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-100 dark:text-slate-300 dark:hover:border-white/10 dark:hover:bg-white/[0.04]"
              }`}
            >
              {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-gradient-to-b from-amber-400 to-[#16233f]" />}
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md font-display text-[11px] font-semibold transition ${
                  active
                    ? "bg-gradient-to-br from-amber-400 to-amber-500 text-[#16233f] shadow-[0_0_12px_rgba(245,158,11,0.35)]"
                    : "bg-slate-100 text-slate-500 group-hover:bg-slate-200 dark:bg-white/[0.06] dark:text-slate-400 dark:group-hover:bg-white/[0.1]"
                }`}
              >
                {d.name.charAt(0).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{d.name}</span>
                <span className="block truncate font-mono text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">{d.status}</span>
              </span>
              <span className={`ml-1 h-2 w-2 shrink-0 rounded-full ${dotColor} ${dotGlow} ${d.status === "online" ? "animate-pulse-soft" : ""}`} />
            </button>
          );
        })}
      </nav>

      {selectedDevice && (
        <div className="border-t border-slate-200 p-3 dark:border-white/10">
          <SimulationControls token={token} deviceId={selectedDevice.device_id} channels={selectedDevice.channels} compact />
        </div>
      )}

      <div className="border-t border-slate-200 p-3 dark:border-white/10">
        {showForm ? (
          <form onSubmit={handleRegister} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-white/10 dark:bg-white/[0.03]">
            <input
              placeholder="device_id (e.g. SB-001)"
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              required
              className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
            />
            <input
              placeholder="name (optional)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
            />
            {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                className="flex-1 rounded-md bg-gradient-to-r from-amber-400 to-amber-500 py-1.5 text-xs font-medium text-[#16233f] transition hover:brightness-105"
              >
                Register
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-md border border-slate-200 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/[0.06]"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <button
            onClick={() => setShowForm(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-300 py-2 text-xs text-slate-500 transition hover:border-amber-500/50 hover:text-amber-600 dark:border-white/15 dark:text-slate-400 dark:hover:border-amber-400/50 dark:hover:text-amber-300"
          >
            <IconPlus className="h-3.5 w-3.5" /> Register device
          </button>
        )}
        <button
          onClick={onLogout}
          className="mt-3 flex w-full items-center justify-center gap-1.5 text-center text-xs text-slate-500 transition hover:text-slate-700 dark:hover:text-slate-300"
        >
          <IconLogOut className="h-3.5 w-3.5" /> Sign out
        </button>
      </div>
    </aside>
  );
}
