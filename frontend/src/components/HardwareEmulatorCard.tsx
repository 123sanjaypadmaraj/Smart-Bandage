import { useEffect, useState } from "react";
import { API_BASE } from "../api";
import { btnBase, btnOff, btnOn } from "../twin3d/BandageTwin3D";
import type { HardwareRequest, useHardwareAssay } from "../twin3d/useHardwareAssay";

const CARD =
  "rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl";
const FIELD =
  "rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-200";

interface StoredRun {
  run_id: string;
  sample_class: HardwareRequest["sampleClass"];
  cfu_per_ml: number;
  label: number;
}

const CLASSES: HardwareRequest["sampleClass"][] = ["target", "nonspecific", "blank"];
const SPEEDS: [string, number][] = [["1×", 0.05], ["4×", 0.0125], ["Instant", 0]];

const fmtCfu = (c: number) => (c === 0 ? "0" : c.toExponential(0).replace("e+", "e"));

/** Source picker + Run/Stop for the emulated hardware analyzer (WS /ws/hardware/eis). */
export function HardwareEmulatorCard({ hardware }: { hardware: ReturnType<typeof useHardwareAssay> }) {
  const { status, start, stop } = hardware;
  const [runs, setRuns] = useState<StoredRun[]>([]);
  const [source, setSource] = useState<"stored" | "custom">("stored");
  const [runId, setRunId] = useState("");
  const [sampleClass, setSampleClass] = useState<HardwareRequest["sampleClass"]>("target");
  const [logCfu, setLogCfu] = useState(4);
  const [seed, setSeed] = useState(1);
  const [speed, setSpeed] = useState(SPEEDS[0][1]);

  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/api/hardware/eis/samples`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((rows: StoredRun[]) => {
        if (!live) return;
        setRuns(rows);
        setRunId((cur) => cur || rows[0]?.run_id || "");
      })
      .catch(() => live && setSource("custom"));
    return () => {
      live = false;
    };
  }, []);

  const streaming = status.link === "connecting" || status.link === "streaming";
  const stored = runs.find((r) => r.run_id === runId);

  const run = () => {
    if (source === "stored" && stored) {
      start({ runId: stored.run_id, sampleClass: stored.sample_class, cfu: stored.cfu_per_ml, seed: 0, pointIntervalS: speed });
    } else {
      start({ sampleClass, cfu: Math.pow(10, logCfu), seed, pointIntervalS: speed });
    }
  };

  return (
    <section className={CARD} aria-label="Emulated hardware analyzer">
      <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Emulated EIS analyzer</h2>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Replays the E. coli impedance dataset as raw ADC counts, one frequency point at a time, like the bandage reader would. The fit, ΔRct and the detect call are
        computed here from the counts received, not read from the labels.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {(["stored", "custom"] as const).map((s) => (
          <button key={s} type="button" aria-pressed={source === s} disabled={s === "stored" && runs.length === 0}
            onClick={() => setSource(s)} className={`${btnBase} ${source === s ? btnOn : btnOff}`}>
            {s === "stored" ? "Stored run" : "Synthesise"}
          </button>
        ))}
        {source === "stored" ? (
          <select aria-label="Stored run" value={runId} onChange={(e) => setRunId(e.target.value)} className={FIELD}>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} · {r.sample_class} · {fmtCfu(r.cfu_per_ml)} CFU/mL
              </option>
            ))}
          </select>
        ) : (
          <>
            <select aria-label="Sample class" value={sampleClass} onChange={(e) => setSampleClass(e.target.value as HardwareRequest["sampleClass"])} className={FIELD}>
              {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
              10^{logCfu.toFixed(1)} CFU/mL
              <input type="range" min={1} max={7} step={0.5} value={logCfu} disabled={sampleClass === "blank"}
                onChange={(e) => setLogCfu(Number(e.target.value))} className="accent-amber-500" />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
              seed
              <input type="number" min={0} value={seed} onChange={(e) => setSeed(Math.max(0, Math.floor(Number(e.target.value) || 0)))} className={`${FIELD} w-16`} />
            </label>
          </>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={streaming ? stop : run} className={`${btnBase} ${streaming ? btnOff : btnOn}`}>
          {streaming ? "Stop" : status.link === "done" ? "Run again" : "Run"}
        </button>
        <div className="flex items-center gap-1" role="group" aria-label="Streaming speed">
          {SPEEDS.map(([label, v]) => (
            <button key={label} type="button" aria-pressed={speed === v} onClick={() => setSpeed(v)} className={`${btnBase} ${speed === v ? btnOn : btnOff}`}>
              {label}
            </button>
          ))}
        </div>
        {status.error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{status.error}</p>}
        {status.truth && (
          <p className="text-xs text-slate-600 dark:text-slate-300">
            Truth: {status.truth.sample_class}, {fmtCfu(status.truth.cfu_per_ml)} CFU/mL ·{" "}
            <span className={status.truth.correct ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
              call {status.truth.correct ? "correct" : "wrong"}
            </span>
          </p>
        )}
      </div>
    </section>
  );
}
