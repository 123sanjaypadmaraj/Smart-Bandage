import { useEffect, useRef, useState } from "react";
import type { FC } from "react";
import { createBandageScene } from "./bandageScene";
import type { AssayState, BandageScene, PartKey } from "./types";

type ViewName = "iso" | "front" | "side" | "top" | "macro";
type ToggleKey = "xray" | "explode" | "flow" | "labels" | "autoRotate";

const VIEW_BUTTONS: { id: ViewName; label: string; title: string }[] = [
  { id: "iso", label: "Iso", title: "Isometric view" },
  { id: "front", label: "Front", title: "Side profile: shows the layer stack" },
  { id: "side", label: "Side", title: "End-on view along the strip" },
  { id: "top", label: "Top", title: "Top-down view" },
  { id: "macro", label: "Macro", title: "Zoom into the functionalised electrode: graphene, PBASE, aptamers, E. coli" },
];

const TOGGLE_BUTTONS: { id: ToggleKey; label: string; title: string }[] = [
  { id: "xray", label: "X-ray", title: "Make the backing, adhesive and pad translucent" },
  { id: "explode", label: "Explode", title: "Separate the layers vertically" },
  { id: "flow", label: "Flow", title: "Particle flows: current response, diffusion, free bacteria" },
  { id: "labels", label: "Labels", title: "Show telemetry pins and inset labels" },
  { id: "autoRotate", label: "Orbit", title: "Slowly auto-orbit the camera" },
];

// Exported so BandageTwinView's Manual/Live toggle shares this toolbar's look.
export const btnBase =
  "rounded-lg border px-2.5 py-1 text-[11px] font-medium leading-none transition focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40";
export const btnOff =
  "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300 dark:hover:bg-white/[0.08]";
export const btnOn =
  "border-amber-500/60 bg-amber-500/15 text-amber-700 dark:border-amber-400/50 dark:bg-amber-400/15 dark:text-amber-300";

interface Props {
  state: AssayState;
  selected: PartKey | null;
  onSelect: (p: PartKey | null) => void;
  className?: string;
}

/**
 * React wrapper around the procedural smart-bandage three.js scene (bandageScene.ts).
 * The scene owns its own render loop; React only forwards state, selection and toolbar toggles.
 */
export const BandageTwin3D: FC<Props> = ({ state, selected, onSelect, className }) => {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<BandageScene | null>(null);
  const stateRef = useRef(state);
  const selectedRef = useRef(selected);
  const onSelectRef = useRef(onSelect);
  stateRef.current = state;
  selectedRef.current = selected;
  onSelectRef.current = onSelect;

  const [view, setView] = useState<ViewName>("iso");
  const [toggles, setToggles] = useState<Record<ToggleKey, boolean>>({
    xray: false,
    explode: false,
    flow: true,
    labels: true,
    autoRotate: false,
  });
  const togglesRef = useRef(toggles);
  togglesRef.current = toggles;
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let scene: BandageScene | null = null;
    try {
      scene = createBandageScene(host, {
        onSelect: (p) => onSelectRef.current(p),
      });
      sceneRef.current = scene;
      if (import.meta.env.DEV) (window as unknown as { bandageScene?: BandageScene }).bandageScene = scene;
      scene.update(stateRef.current);
      scene.setToggles(togglesRef.current);
      scene.select(selectedRef.current);
      setFailure(null);
    } catch (err) {
      sceneRef.current = null;
      setFailure(err instanceof Error ? err.message : String(err));
    }
    return () => {
      sceneRef.current = null;
      try {
        scene?.dispose();
      } catch {
        /* teardown must never throw into React */
      }
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.update(state);
  }, [state]);

  useEffect(() => {
    sceneRef.current?.select(selected);
  }, [selected]);

  useEffect(() => {
    sceneRef.current?.setToggles(toggles);
  }, [toggles]);

  const pickView = (v: ViewName) => {
    setView(v);
    sceneRef.current?.setView(v);
  };

  return (
    <div className={`flex flex-col bg-slate-50 dark:bg-black/30 ${className ?? ""}`}>
      <div
        role="toolbar"
        aria-label="3D view controls"
        className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-slate-200 bg-white/70 px-2.5 py-2 dark:border-white/10 dark:bg-white/[0.03]"
      >
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Camera view">
          {VIEW_BUTTONS.map((b) => (
            <button
              key={b.id}
              type="button"
              title={b.title}
              aria-pressed={view === b.id}
              onClick={() => pickView(b.id)}
              className={`${btnBase} ${view === b.id ? btnOn : btnOff}`}
            >
              {b.label}
            </button>
          ))}
        </div>
        <span aria-hidden className="hidden h-4 w-px bg-slate-200 dark:bg-white/10 sm:block" />
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Display options">
          {TOGGLE_BUTTONS.map((b) => (
            <button
              key={b.id}
              type="button"
              title={b.title}
              aria-pressed={toggles[b.id]}
              onClick={() => setToggles((t) => ({ ...t, [b.id]: !t[b.id] }))}
              className={`${btnBase} ${toggles[b.id] ? btnOn : btnOff}`}
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>
      {failure ? (
        <div
          role="alert"
          className="flex min-h-[260px] flex-1 items-center justify-center p-6 text-center text-sm text-slate-500 dark:text-slate-400"
        >
          <div>
            <p className="font-medium text-slate-700 dark:text-slate-200">3D view unavailable</p>
            <p className="mt-1 text-xs">
              WebGL could not be started in this browser, so the bandage model cannot be drawn. The EIS panel still works.
            </p>
            <p className="mt-2 font-mono text-[10px] opacity-70">{failure}</p>
          </div>
        </div>
      ) : (
        <div ref={hostRef} className="relative min-h-[260px] flex-1 overflow-hidden" />
      )}
    </div>
  );
};
