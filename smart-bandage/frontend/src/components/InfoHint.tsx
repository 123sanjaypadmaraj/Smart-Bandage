import { useEffect, useRef, useState } from "react";
import { IconInfo } from "./icons";

interface InfoHintProps {
  /** Explanation shown in the pop-up when clicked (also shown on hover/focus for mouse users). */
  text: string;
  /** Optional "what a normal/expected value looks like" line, shown under the explanation. */
  nominal?: string;
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
 * A small "i" glyph that reveals an explanatory pop-up when clicked (and
 * still previews on hover/focus for mouse users). Click-triggered so it
 * works on touch devices and stays open long enough to read. Used
 * throughout the dashboard to explain what a stat, chart line, or badge
 * actually means -- see MeasurementChart, StatusBar, AlertsPanel,
 * SimulationControls, EisPanel.
 */
export function InfoHint({ text, nominal, className = "", align = "center" }: InfoHintProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={ref} className={`group/info relative inline-flex ${className}`}>
      <button
        type="button"
        tabIndex={0}
        aria-label={text}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className={`flex h-3.5 w-3.5 cursor-pointer items-center justify-center rounded-full text-slate-400 outline-none transition hover:text-slate-600 focus-visible:text-slate-600 dark:text-slate-600 dark:hover:text-slate-300 dark:focus-visible:text-slate-300 ${open ? "text-amber-600 dark:text-amber-300" : ""}`}
      >
        <IconInfo className="h-3 w-3" />
      </button>
      <span
        role="tooltip"
        className={`pointer-events-none absolute bottom-full z-30 mb-1.5 w-56 -translate-y-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-600 opacity-0 shadow-lg shadow-slate-900/5 transition duration-150 dark:border-white/10 dark:bg-slate-900 dark:text-slate-300 dark:shadow-black/40 ${
          open
            ? "pointer-events-auto translate-y-0 opacity-100"
            : "group-hover/info:translate-y-0 group-hover/info:opacity-100 group-focus-within/info:translate-y-0 group-focus-within/info:opacity-100"
        } ${ALIGN_CLASS[align]}`}
      >
        {text}
        {nominal && (
          <span className="mt-1 block border-t border-slate-100 pt-1 text-[10px] text-slate-500 dark:border-white/10 dark:text-slate-400">
            <span className="font-semibold text-slate-600 dark:text-slate-300">Nominal: </span>
            {nominal}
          </span>
        )}
      </span>
    </span>
  );
}
