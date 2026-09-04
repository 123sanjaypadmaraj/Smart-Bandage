import type { ComponentType, SVGProps } from "react";
import type { DeviceStatus, MeasurementRecord } from "../types";
import { IconActivity, IconBattery, IconCheckCircle, IconZap } from "./icons";
import { InfoHint } from "./InfoHint";
import { RadialGauge } from "./RadialGauge";
import { Sparkline } from "./Sparkline";

interface StatusBarProps {
  status: DeviceStatus | null;
  wsConnected: boolean;
  lastMeasurement: MeasurementRecord | null;
  /** Recent history (oldest -> newest), used to draw the battery sparkline. */
  records: MeasurementRecord[];
}

type Tone = "ok" | "warn" | "bad" | "neutral";

const TONE_STYLES: Record<Tone, { text: string; ring: string; glow: string; iconBg: string; line: string }> = {
  ok: {
    text: "text-emerald-600 dark:text-emerald-300",
    ring: "border-emerald-400/30 dark:border-emerald-400/20",
    glow: "shadow-[0_0_24px_-8px_rgba(16,185,129,0.35)]",
    iconBg: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300",
    line: "#10b981",
  },
  warn: {
    text: "text-amber-600 dark:text-amber-300",
    ring: "border-amber-400/30 dark:border-amber-400/20",
    glow: "shadow-[0_0_24px_-8px_rgba(251,191,36,0.4)]",
    iconBg: "bg-amber-400/10 text-amber-600 dark:text-amber-300",
    line: "#f59e0b",
  },
  bad: {
    text: "text-rose-600 dark:text-rose-300",
    ring: "border-rose-400/30 dark:border-rose-400/20",
    glow: "shadow-[0_0_24px_-8px_rgba(244,63,94,0.4)]",
    iconBg: "bg-rose-500/10 text-rose-600 dark:text-rose-300",
    line: "#f43f5e",
  },
  neutral: {
    text: "text-slate-700 dark:text-slate-200",
    ring: "border-slate-200 dark:border-white/10",
    glow: "",
    iconBg: "bg-slate-100 text-slate-500 dark:bg-white/[0.06] dark:text-slate-400",
    line: "#94a3b8",
  },
};

function TileShell({ tone, flashKey, children }: { tone: Tone; flashKey?: string | number; children: React.ReactNode }) {
  const s = TONE_STYLES[tone];
  return (
    <div
      key={flashKey}
      className={`group flex items-center gap-3 rounded-xl border ${s.ring} bg-white px-4 py-3.5 shadow-sm transition hover:-translate-y-0.5 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl ${s.glow} ${flashKey !== undefined ? "animate-tile-flash" : ""}`}
    >
      {children}
    </div>
  );
}

function Stat({
  label,
  hint,
  value,
  tone = "neutral",
  icon: Icon,
  flashKey,
}: {
  label: string;
  hint: string;
  value: string;
  tone?: Tone;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  flashKey?: string | number;
}) {
  const s = TONE_STYLES[tone];
  return (
    <TileShell tone={tone} flashKey={flashKey}>
      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${s.iconBg}`}>
        <Icon className="h-4.5 w-4.5" />
      </div>
      <div className="min-w-0">
        <p className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-slate-500">
          {label}
          <InfoHint text={hint} align="left" />
        </p>
        <p className={`mt-0.5 font-mono text-base font-medium leading-tight ${s.text}`}>{value}</p>
      </div>
    </TileShell>
  );
}

function SparklineStat({
  label,
  hint,
  value,
  tone,
  icon: Icon,
  series,
}: {
  label: string;
  hint: string;
  value: string;
  tone: Tone;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  series: number[];
}) {
  const s = TONE_STYLES[tone];
  return (
    <TileShell tone={tone}>
      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${s.iconBg}`}>
        <Icon className="h-4.5 w-4.5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-slate-500">
          {label}
          <InfoHint text={hint} align="left" />
        </p>
        <p className={`mt-0.5 font-mono text-base font-medium leading-tight ${s.text}`}>{value}</p>
      </div>
      {series.length > 1 && <Sparkline values={series} stroke={s.line} className="shrink-0 opacity-80" />}
    </TileShell>
  );
}

function GaugeStat({
  label,
  hint,
  value,
  tone,
}: {
  label: string;
  hint: string;
  value: number | null;
  tone: Tone;
}) {
  const s = TONE_STYLES[tone];
  return (
    <TileShell tone={tone}>
      <RadialGauge
        value={value ?? 0}
        size={44}
        strokeWidth={4}
        color={s.line}
        label={value !== null ? `${Math.round(value * 100)}` : "—"}
      />
      <div className="min-w-0">
        <p className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-slate-500">
          {label}
          <InfoHint text={hint} align="left" />
        </p>
        <p className={`mt-0.5 font-mono text-base font-medium leading-tight ${s.text}`}>
          {value !== null ? `${Math.round(value * 100)}%` : "—"}
        </p>
      </div>
    </TileShell>
  );
}

export function StatusBar({ status, wsConnected, lastMeasurement, records }: StatusBarProps) {
  const battery = status?.battery ?? lastMeasurement?.battery ?? null;
  const quality = status?.signal_quality ?? lastMeasurement?.signal_quality ?? null;
  const connected = status?.connected ?? false;

  const batterySeries = records.map((r) => r.battery).filter((b): b is number => b !== null);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <Stat
        label="Sensor"
        hint="Whether the backend currently has a live connection to this device's sensor hardware (or its simulator)."
        value={connected ? "Connected" : "Disconnected"}
        tone={connected ? "ok" : "bad"}
        icon={IconCheckCircle}
      />
      <Stat
        label="Live feed"
        hint="Whether this dashboard's WebSocket connection to the backend is open and streaming readings in real time."
        value={wsConnected ? "Streaming" : "Idle"}
        tone={wsConnected ? "ok" : "warn"}
        icon={IconZap}
      />
      <SparklineStat
        label="Battery"
        hint="Remaining battery charge reported by the device itself, 0-100%."
        value={battery !== null ? `${battery}%` : "—"}
        tone={battery !== null ? (battery < 15 ? "bad" : battery < 30 ? "warn" : "ok") : "neutral"}
        icon={IconBattery}
        series={batterySeries}
      />
      <GaugeStat
        label="Signal quality"
        hint="A 0-100% score for how clean the current sensor signal is. Low values usually mean noise, drift, or poor electrode contact."
        value={quality}
        tone={quality !== null ? (quality < 0.5 ? "bad" : quality < 0.8 ? "warn" : "ok") : "neutral"}
      />
      <Stat
        label="Latest status"
        hint="Validity of the single most recent reading: valid, invalid (failed a sanity check), or error (device fault)."
        value={lastMeasurement?.status ?? "—"}
        tone={lastMeasurement?.status === "valid" ? "ok" : lastMeasurement ? "bad" : "neutral"}
        icon={IconActivity}
        flashKey={lastMeasurement?.timestamp}
      />
    </div>
  );
}
