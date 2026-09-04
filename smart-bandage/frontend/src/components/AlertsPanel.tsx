import type { AlertItem } from "../types";
import { IconAlertTriangle } from "./icons";
import { InfoHint } from "./InfoHint";

interface AlertsPanelProps {
  alerts: AlertItem[];
}

// See backend/app/schemas.py:AlertType and backend/app/intelligence.py (the
// Phase 6 trend/quality types below are history-based, unlike the single-
// reading threshold checks above them).
const ALERT_TYPE_INFO: Record<AlertItem["type"], string> = {
  INFO: "Informational only -- no action needed.",
  WARNING: "A single reading crossed a warning threshold.",
  CRITICAL: "A single reading crossed a critical threshold and needs attention.",
  DEVICE_ERROR: "The device itself reported a hardware fault.",
  SENSOR_ERROR: "A sensor reading failed validation (e.g. out of physical range).",
  RAPID_TREND: "The value is changing unusually fast across recent readings.",
  SUSTAINED_TREND: "The value has been steadily rising or falling over a sustained period.",
  QUALITY_DEGRADING: "Signal quality has been declining across recent readings.",
};

const SEVERITY_STYLE: Record<AlertItem["severity"], string> = {
  info: "border-[#16233f]/20 border-l-[3px] border-l-[#16233f]/50 bg-[#16233f]/[0.05] text-[#16233f] dark:border-blue-800/50 dark:border-l-blue-400/60 dark:bg-blue-950/30 dark:text-blue-300",
  warning:
    "border-amber-400/40 border-l-[3px] border-l-amber-500 bg-amber-50 text-amber-700 dark:border-amber-800/50 dark:border-l-amber-400 dark:bg-amber-950/30 dark:text-amber-300",
  critical:
    "border-rose-300 border-l-[3px] border-l-rose-500 bg-rose-50 text-rose-700 dark:border-rose-800/50 dark:border-l-rose-400 dark:bg-rose-950/30 dark:text-rose-300",
};

const SEVERITY_DOT: Record<AlertItem["severity"], string> = {
  info: "bg-[#16233f] dark:bg-blue-400",
  warning: "bg-amber-500 dark:bg-amber-400",
  critical: "bg-rose-500 shadow-[0_0_6px_2px_rgba(244,63,94,0.5)] dark:bg-rose-400",
};

export function AlertsPanel({ alerts }: AlertsPanelProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          <IconAlertTriangle className="h-3.5 w-3.5" /> Alerts
        </h2>
        {alerts.length > 0 && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-white/[0.06] dark:text-slate-400">{alerts.length}</span>
        )}
      </div>
      {alerts.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500">No active alerts.</p>
      ) : (
        <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
          {alerts.map((a, i) => (
            <li
              key={a.id ?? `${a.timestamp}-${i}`}
              className={`animate-alert-in rounded-lg border px-3 py-2 text-xs ${SEVERITY_STYLE[a.severity]}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide">
                  <span className="flex items-center gap-1.5 opacity-80">
                    <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_DOT[a.severity]}`} />
                    {a.type}
                  </span>
                  <InfoHint text={ALERT_TYPE_INFO[a.type]} align="left" />
                </span>
                <span className="text-[10px] opacity-60">{new Date(a.timestamp).toLocaleTimeString()}</span>
              </div>
              <p className="mt-1 pl-3">{a.message}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
