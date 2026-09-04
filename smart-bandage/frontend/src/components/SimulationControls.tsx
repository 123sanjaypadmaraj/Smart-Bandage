import { useState } from "react";
import { api, ApiError } from "../api";
import { SCENARIOS, type Scenario } from "../types";
import { IconBeaker } from "./icons";
import { InfoHint } from "./InfoHint";

// Mirrors simulator/scenarios/scenarios.py -- what each injected scenario
// actually does to the simulated sensor.
const SCENARIO_INFO: Record<Scenario, string> = {
  normal: "Stable signal, low noise, healthy battery.",
  rising_concentration: "Concentration ramps up in steps: 100 -> 110 -> 125 -> 140 -> 155 -> 170.",
  sudden_abnormal: "Stable, then a sudden spike: 105 -> 108 -> 110 -> 160 -> 185 -> 210.",
  electrode_degradation: "Signal quality steadily decays: 98% -> 97% -> 94% -> 88% -> 76% -> 62%.",
  high_noise: "Signal-to-noise ratio degrades sharply (8x noise amplification).",
  sensor_disconnect: "3 valid readings, then the sensor reports disconnected/invalid from then on.",
  comms_failure: "Packets alternate: 3 delivered, 3 dropped, repeating.",
};

interface SimulationControlsProps {
  token: string;
  deviceId: string;
  channels: string[];
  /**
   * Stacks the controls into a narrow vertical panel instead of the
   * full-width horizontal bar -- used when this renders inside the left
   * sidebar (see Sidebar.tsx) alongside the device list, IDE-panel style.
   */
  compact?: boolean;
}

export function SimulationControls({ token, deviceId, channels, compact = false }: SimulationControlsProps) {
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      await api.startSimulation(token, { device_id: deviceId, channels: channels.length ? channels : ["CH-01"], scenario });
      setRunning(true);
    });

  const stop = () =>
    withBusy(async () => {
      await api.stopSimulation(token, deviceId);
      setRunning(false);
    });

  const injectScenario = (next: Scenario) =>
    withBusy(async () => {
      setScenario(next);
      if (running) await api.setScenario(token, { device_id: deviceId, scenario: next });
    });

  const runningIndicator = (
    <span className={`flex items-center gap-2 text-xs ${running ? "text-emerald-600 dark:text-emerald-300" : "text-slate-500"}`}>
      {running ? (
        <span className="flex h-3 items-end gap-[2.5px]" aria-hidden="true">
          <span className="w-[2.5px] animate-waveform rounded-full bg-emerald-500 [animation-delay:0ms]" />
          <span className="w-[2.5px] animate-waveform rounded-full bg-emerald-500 [animation-delay:120ms]" />
          <span className="w-[2.5px] animate-waveform rounded-full bg-emerald-500 [animation-delay:240ms]" />
          <span className="w-[2.5px] animate-waveform rounded-full bg-emerald-500 [animation-delay:360ms]" />
        </span>
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-600" />
      )}
      {running ? "running" : "stopped"}
    </span>
  );

  const toggleButton = !running ? (
    <button
      onClick={start}
      disabled={busy}
      className={`rounded-lg bg-gradient-to-r from-amber-400 to-amber-500 py-1.5 text-xs font-medium text-[#16233f] shadow-[0_4px_16px_rgba(245,158,11,0.25)] transition hover:brightness-105 disabled:opacity-50 ${compact ? "w-full" : "px-3.5"}`}
    >
      Start
    </button>
  ) : (
    <button
      onClick={stop}
      disabled={busy}
      className={`rounded-lg bg-rose-500 py-1.5 text-xs font-medium text-white transition hover:bg-rose-400 disabled:opacity-50 ${compact ? "w-full" : "px-3.5"}`}
    >
      Stop
    </button>
  );

  const scenarioSelect = (
    <select
      value={scenario}
      onChange={(e) => injectScenario(e.target.value as Scenario)}
      title={SCENARIO_INFO[scenario]}
      className={`rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20 ${compact ? "w-full" : ""}`}
    >
      {SCENARIOS.map((s) => (
        <option key={s} value={s} title={SCENARIO_INFO[s]}>
          {s.replaceAll("_", " ")}
        </option>
      ))}
    </select>
  );

  if (compact) {
    return (
      <div className="space-y-2">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          <IconBeaker className="h-3.5 w-3.5" /> Simulation
          <InfoHint text="Drives the virtual bandage instead of real hardware, so you can inject fault/noise scenarios and watch the dashboard react." />
        </h2>

        <div className="flex items-center gap-1.5">
          {scenarioSelect}
          <InfoHint text={SCENARIO_INFO[scenario]} />
        </div>

        {toggleButton}

        <div className="flex items-center justify-between">{runningIndicator}</div>

        {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl">
      <h2 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        <IconBeaker className="h-3.5 w-3.5" /> Simulation
        <InfoHint text="Drives the virtual bandage instead of real hardware, so you can inject fault/noise scenarios and watch the dashboard react." align="left" />
      </h2>

      <div className="flex flex-wrap items-center gap-2">
        {scenarioSelect}
        <InfoHint text={SCENARIO_INFO[scenario]} />
        {toggleButton}
        {runningIndicator}
      </div>

      {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}
