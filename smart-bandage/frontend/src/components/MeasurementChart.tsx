import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { LegendPayload } from "recharts";
import { useTheme } from "../hooks/useTheme";
import type { MeasurementRecord } from "../types";
import { IconActivity } from "./icons";
import { InfoHint } from "./InfoHint";

interface MeasurementChartProps {
  records: MeasurementRecord[];
}

// What each traced line actually represents -- see common/schemas/measurement.py
// and the Phase 3 processing pipeline (processing/filtering, processing/calibration).
const LINE_INFO: Record<string, string> = {
  raw_signal: "The unprocessed reading straight off the electrode -- includes noise and baseline drift, before any correction.",
  processed_signal: "raw_signal after filtering plus baseline/drift correction (the Phase 3 processing pipeline).",
  estimated_value: "The final biomarker concentration, calibrated from processed_signal into real units.",
};

function renderLegend({ payload }: { payload?: ReadonlyArray<LegendPayload> }) {
  return (
    <ul className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-2 text-[11px]">
      {payload?.map((entry) => {
        const name = typeof entry.value === "string" ? entry.value : String(entry.value);
        return (
          <li key={name} className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: entry.color }} />
            {name}
            {LINE_INFO[name] && <InfoHint text={LINE_INFO[name]} />}
          </li>
        );
      })}
    </ul>
  );
}

/** Only the last point on the estimated_value line gets a marker -- a small
 * glowing "live edge" instead of a dot cluttering every sample. */
function makeLiveEdgeDot(lastIndex: number, color: string) {
  return function LiveEdgeDot(props: { cx?: number; cy?: number; index?: number }) {
    const { cx, cy, index } = props;
    if (index !== lastIndex || cx == null || cy == null) return null;
    return (
      <g>
        <circle cx={cx} cy={cy} r={7} fill={color} opacity={0.18} className="animate-pulse-soft" />
        <circle cx={cx} cy={cy} r={3} fill={color} stroke="white" strokeWidth={1.25} className="dark:stroke-slate-950" />
      </g>
    );
  };
}

export function MeasurementChart({ records }: MeasurementChartProps) {
  const { theme } = useTheme();
  const isDark = theme === "dark";
  const data = records.map((r) => ({
    time: new Date(r.timestamp).toLocaleTimeString(),
    raw: r.raw_signal,
    processed: r.processed_signal,
    estimated: r.estimated_value,
    invalid: r.status !== "valid",
  }));

  if (data.length === 0) {
    return (
      <div className="flex h-72 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-white text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.02]">
        <IconActivity className="h-5 w-5 text-slate-400 dark:text-slate-600" />
        No measurements yet -- start a simulation or wait for live data.
      </div>
    );
  }

  const estimatedColor = "#f59e0b";
  const liveEdgeDot = makeLiveEdgeDot(data.length - 1, estimatedColor);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        <IconActivity className="h-3.5 w-3.5" /> Signal trace
      </h2>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="estimatedFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={estimatedColor} stopOpacity={isDark ? 0.35 : 0.22} />
                <stop offset="100%" stopColor={estimatedColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.15)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "#64748b" }} minTickGap={40} />
            <YAxis tick={{ fontSize: 10, fill: "#64748b" }} width={40} />
            <Tooltip
              contentStyle={{
                background: isDark ? "rgba(8, 12, 24, 0.92)" : "rgba(255, 255, 255, 0.96)",
                border: isDark ? "1px solid rgba(148,163,184,0.15)" : "1px solid rgba(100,116,139,0.2)",
                borderRadius: 8,
                fontSize: 12,
                backdropFilter: "blur(8px)",
              }}
              labelStyle={{ color: "#64748b" }}
            />
            <Legend content={renderLegend} />
            <Area type="monotone" dataKey="estimated" stroke="none" fill="url(#estimatedFill)" isAnimationActive={false} legendType="none" />
            <Line type="monotone" dataKey="raw" stroke="#94a3b8" dot={false} strokeWidth={1.5} name="raw_signal" isAnimationActive={false} />
            <Line
              type="monotone"
              dataKey="processed"
              stroke={isDark ? "#8fa3c7" : "#16233f"}
              dot={false}
              strokeWidth={2}
              name="processed_signal"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="estimated"
              stroke={estimatedColor}
              dot={liveEdgeDot}
              strokeWidth={2.25}
              name="estimated_value"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
