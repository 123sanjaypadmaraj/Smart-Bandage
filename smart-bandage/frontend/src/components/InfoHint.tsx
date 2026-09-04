import { IconInfo } from "./icons";

interface InfoHintProps {
  /** Explanation shown in the pop-up on hover/focus. */
  text: string;
  className?: string;
  /** Which side of the icon the pop-up opens toward; defaults to centered above. */
  align?: "center" | "left" | "right";
}

const ALIGN_CLASS: Record<NonNullable<InfoHintProps["align"]>, string> = {
  center: "left-1/2 -translate-x-1/2",
  left: "left-0",
  right: "right-0",
};

/**
 * A small "i" glyph that reveals an explanatory pop-up on hover (and on
 * keyboard focus, so it isn't mouse-only). Used throughout the dashboard to
 * explain what a stat, chart line, or badge actually means -- see
 * MeasurementChart, StatusBar, AlertsPanel, SimulationControls.
 */
export function InfoHint({ text, className = "", align = "center" }: InfoHintProps) {
  return (
    <span className={`group/info relative inline-flex ${className}`}>
      <button
        type="button"
        tabIndex={0}
        aria-label={text}
        className="flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full text-slate-400 outline-none transition hover:text-slate-600 focus-visible:text-slate-600 dark:text-slate-600 dark:hover:text-slate-300 dark:focus-visible:text-slate-300"
      >
        <IconInfo className="h-3 w-3" />
      </button>
      <span
        role="tooltip"
        className={`pointer-events-none absolute bottom-full z-30 mb-1.5 w-52 -translate-y-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-600 opacity-0 shadow-lg shadow-slate-900/5 transition duration-150 group-hover/info:translate-y-0 group-hover/info:opacity-100 group-focus-within/info:translate-y-0 group-focus-within/info:opacity-100 dark:border-white/10 dark:bg-slate-900 dark:text-slate-300 dark:shadow-black/40 ${ALIGN_CLASS[align]}`}
      >
        {text}
      </span>
    </span>
  );
}
