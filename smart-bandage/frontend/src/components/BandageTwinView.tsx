import { useState } from "react";
import { BandageTwin3D } from "../twin3d/BandageTwin3D";
import { BackendCheck } from "../twin3d/BackendCheckPanel";
import { EisPanel } from "../twin3d/EisPanel";
import { PHASE_LABEL } from "../twin3d/types";
import type { PartKey } from "../twin3d/types";
import { useAssay } from "../twin3d/useAssay";

/**
 * Smart Bandage E. coli EIS digital twin: the 3D bandage scene (left/top) next to
 * the EIS analysis panel (workflow, spectra, parameters, circuit, detection).
 * Owns the assay simulation and the shared "selected part" state, so clicking a
 * part in 3D highlights the matching table row / circuit element and vice versa.
 */
export function BandageTwinView() {
  const { state, controls } = useAssay();
  const [selected, setSelected] = useState<PartKey | null>(null);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_30px_rgba(15,23,42,0.06)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-[0_8px_30px_rgba(0,0,0,0.35)] dark:backdrop-blur-xl">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-widest text-amber-600 dark:text-amber-300/80">Digital twin · graphene-PBASE-aptamer electrode</p>
          <h1 className="font-display mt-1 text-xl font-semibold tracking-tight text-slate-900 dark:text-white">Smart Bandage — E. coli EIS Digital Twin</h1>
        </div>
        <p className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 dark:border-white/10 dark:text-slate-300" role="status" aria-live="polite">
          Step: <span className="font-semibold text-slate-900 dark:text-white">{PHASE_LABEL[state.phase]}</span>
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="lg:sticky lg:top-4 lg:self-start">
          <BandageTwin3D
            state={state}
            selected={selected}
            onSelect={setSelected}
            className="h-[52vh] min-h-[320px] w-full overflow-hidden rounded-2xl border border-slate-200 dark:border-white/10 lg:h-[calc(100vh-9rem)]"
          />
        </div>
        <div className="space-y-4">
          <EisPanel state={state} controls={controls} selected={selected} onSelect={setSelected} />
          <BackendCheck cfu={state.cfuPerMl} />
        </div>
      </div>
    </div>
  );
}
