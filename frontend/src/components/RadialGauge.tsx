interface RadialGaugeProps {
  /** 0-1 */
  value: number;
  size?: number;
  strokeWidth?: number;
  color: string;
  trackClassName?: string;
  label?: string;
  sublabel?: string;
}

/**
 * A ring gauge for a 0-1 score (signal quality, device health). Renders its
 * own centered label so it doubles as the stat's value display, not just
 * decoration.
 */
export function RadialGauge({
  value,
  size = 56,
  strokeWidth = 5,
  color,
  trackClassName = "text-slate-200 dark:text-white/10",
  label,
  sublabel,
}: RadialGaugeProps) {
  const clamped = Math.max(0, Math.min(1, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped);
  const center = size / 2;

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={center} cy={center} r={radius} fill="none" strokeWidth={strokeWidth} className={trackClassName} stroke="currentColor" />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          stroke={color}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.6s ease" }}
        />
      </svg>
      {(label || sublabel) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
          {label && <span className="font-mono text-[13px] font-semibold text-slate-800 dark:text-slate-100">{label}</span>}
          {sublabel && <span className="mt-0.5 text-[8px] uppercase tracking-wide text-slate-400">{sublabel}</span>}
        </div>
      )}
    </div>
  );
}
