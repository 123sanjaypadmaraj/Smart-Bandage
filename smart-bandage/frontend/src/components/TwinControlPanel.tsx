import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { PatientProfileInfo, TwinGroundTruth } from "../types";
import { IconShield } from "./icons";
import { InfoHint } from "./InfoHint";
import { Sparkline } from "./Sparkline";

interface TwinControlPanelProps {
  token: string;
  deviceId: string;
  channels: string[];
  /** From useDeviceSocket -- the WS "twin_ground_truth" stream (dev-only,
   * the backend never broadcasts it in production; see
   * backend/app/routers/simulation.py). */
  lastGroundTruth: TwinGroundTruth | null;
  groundTruthHistory: TwinGroundTruth[];
}

// WoundState's plausible range for each variable (digital_twin/state.py) --
// what the bars below normalize against.
const STATE_RANGE: Record<"inflammation" | "bacterial_load" | "moisture" | "perfusion", number> = {
  inflammation: 1.5,
  bacterial_load: 1.5,
  moisture: 1.0,
  perfusion: 1.0,
};

const STATE_LABELS: Record<keyof typeof STATE_RANGE, string> = {
  inflammation: "Inflammation",
  bacterial_load: "Bacterial load",
  moisture: "Moisture",
  perfusion: "Perfusion",
};

/**
 * DT-6: patient profile + time-scale controls for the twin-backed
 * simulation mode (backend/app/simulation.py's DigitalTwinDevice), plus a
 * dev-only "ground truth overlay" comparing the hidden physiological state
 * it's actually integrating against what the (noisy, fouled) channel
 * reports for it. import.meta.env.DEV gates the overlay client-side; the
 * backend also never broadcasts twin_ground_truth in production, so a prod
 * build sees nothing here to fetch even if this rendered anyway.
 */
export function TwinControlPanel({ token, deviceId, channels, lastGroundTruth, groundTruthHistory }: TwinControlPanelProps) {
  const [profiles, setProfiles] = useState<PatientProfileInfo[]>([]);
  const [patientProfile, setPatientProfile] = useState<string>("");
  const [timeScale, setTimeScale] = useState(60);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .patientProfiles()
      .then((list) => {
        setProfiles(list);
        setPatientProfile((current) => current || list[0]?.name || "");
      })
      .catch(() => {
        /* twin control panel is non-critical to show on first load */
      });
  }, []);

  // Reset per-device UI state on device switch -- otherwise "running"
  // would carry over to a device that was never started as a twin.
  useEffect(() => setRunning(false), [deviceId]);

  async function withBusy(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    withBusy(async () => {
      await api.startSimulation(token, {
        device_id: deviceId,
        channels: channels.length ? channels : ["CH-01"],
        patient_profile: patientProfile,
        time_scale: timeScale,
      });
      setRunning(true);
    });

  const stop = () =>
    withBusy(async () => {
      await api.stopSimulation(token, deviceId);
      setRunning(false);
    });

  const activeProfile = profiles.find((p) => p.name === patientProfile);
  const trueHistory = groundTruthHistory.map((g) => g.true_signal);
  const estimatedHistory = groundTruthHistory.map((g) => g.estimated_signal ?? g.true_signal);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl">
      <h2 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        <IconShield className="h-3.5 w-3.5" /> Digital twin
        <InfoHint
          text="Drives the virtual bandage from a physiological hidden state (a patient profile) instead of a scripted scenario -- backend/app/simulation.py's DigitalTwinDevice."
          align="left"
        />
      </h2>

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={patientProfile}
          onChange={(e) => setPatientProfile(e.target.value)}
          disabled={running}
          title={activeProfile?.description}
          className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
        >
          {profiles.map((p) => (
            <option key={p.name} value={p.name} title={p.description}>
              {p.name.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        {activeProfile && <InfoHint text={activeProfile.description} />}

        <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
          time scale
          <input
            type="number"
            min={1}
            step={1}
            value={timeScale}
            disabled={running}
            onChange={(e) => setTimeScale(Math.max(1, Number(e.target.value) || 1))}
            className="w-16 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
          />
          <InfoHint text="Multiplies wall-clock seconds into physiological seconds fed to the twin each tick -- 60 means one real second covers one simulated minute." />
        </label>

        {!running ? (
          <button
            onClick={start}
            disabled={busy || !patientProfile}
            className="rounded-lg bg-gradient-to-r from-amber-400 to-amber-500 px-3.5 py-1.5 text-xs font-medium text-[#16233f] shadow-[0_4px_16px_rgba(245,158,11,0.25)] transition hover:brightness-105 disabled:opacity-50"
          >
            Start twin
          </button>
        ) : (
          <button
            onClick={stop}
            disabled={busy}
            className="rounded-lg bg-rose-500 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-rose-400 disabled:opacity-50"
          >
            Stop twin
          </button>
        )}
      </div>

      {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {import.meta.env.DEV && (
        <div className="mt-4 border-t border-dashed border-slate-200 pt-3 dark:border-white/10">
          <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            Ground truth overlay
            <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-amber-700 dark:bg-amber-400/10 dark:text-amber-300">
              dev only
            </span>
            <InfoHint text="Compares the channel's actual (noisy, fouled) estimated_value against the clean value the twin's hidden state would produce with no noise -- never sent by the backend in production." />
          </h3>

          {!lastGroundTruth ? (
            <p className="text-xs text-slate-500 dark:text-slate-500">
              Start a twin-backed simulation on this device to see estimated vs. true here.
            </p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 p-2.5 dark:bg-white/[0.04]">
                <div>
                  <p className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">Estimated vs true</p>
                  <p className="mt-0.5 font-mono text-sm text-slate-800 dark:text-slate-100">
                    <span className="text-amber-600 dark:text-amber-300">{lastGroundTruth.estimated_signal?.toFixed(2) ?? "--"}</span>
                    <span className="mx-1 text-slate-400">/</span>
                    <span className="text-emerald-600 dark:text-emerald-300">{lastGroundTruth.true_signal.toFixed(2)}</span>
                  </p>
                </div>
                <Sparkline values={estimatedHistory} stroke="#f59e0b" width={72} height={28} />
                <Sparkline values={trueHistory} stroke="#10b981" width={72} height={28} />
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {(Object.keys(STATE_RANGE) as (keyof typeof STATE_RANGE)[]).map((key) => {
                  const value = lastGroundTruth[key];
                  const pct = Math.max(0, Math.min(100, (value / STATE_RANGE[key]) * 100));
                  return (
                    <div key={key}>
                      <div className="flex items-center justify-between text-[10px] text-slate-500 dark:text-slate-400">
                        <span>{STATE_LABELS[key]}</span>
                        <span className="font-mono">{value.toFixed(2)}</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
                        <div className="h-full rounded-full bg-gradient-to-r from-amber-400 to-[#16233f]" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
