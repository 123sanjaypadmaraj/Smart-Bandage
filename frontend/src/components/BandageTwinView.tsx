import { useEffect, useMemo, useState } from "react";
import type { TwinBandageTruth, TwinEisReading } from "../types";
import { BandageTwin3D, btnBase, btnOff, btnOn } from "../twin3d/BandageTwin3D";
import { BackendCheck } from "../twin3d/BackendCheckPanel";
import { EisPanel } from "../twin3d/EisPanel";
import { PHASE_LABEL } from "../twin3d/types";
import type { AssayState, PartKey } from "../twin3d/types";
import { useAssay } from "../twin3d/useAssay";
import { useLiveAssay } from "../twin3d/useLiveAssay";
import { InfoHint } from "./InfoHint";

type TwinMode = "manual" | "live";

/** The selected device's bandage-twin stream, from App's useDeviceSocket. */
export interface BandageTwinLiveFeed {
  deviceId: string | null;
  connected: boolean;
  eisByChannel: Record<string, TwinEisReading>;
  /** dev-only (the backend never sends twin_bandage_truth in production) */
  lastBandageTruth: TwinBandageTruth | null;
}

interface Props {
  /** Omitted by the standalone, auth-free entry (bandage-main.tsx), which
   * has no device to listen to -- then only the manual assay is offered. */
  live?: BandageTwinLiveFeed;
}

const CARD =
  "rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl";

function fmtSimTime(s: number): string {
  if (!Number.isFinite(s)) return "—";
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(0)} s`;
}

/**
 * Smart Bandage E. coli EIS digital twin: the 3D bandage scene (left/top) next to
 * the EIS analysis panel (workflow, spectra, parameters, circuit, detection).
 * Owns the assay simulation and the shared "selected part" state, so clicking a
 * part in 3D highlights the matching table row / circuit element and vice versa.
 *
 * Two sources can drive it: "Manual" is the self-contained browser assay
 * (useAssay, unchanged), "Live" renders the backend BandageTwin's twin_eis
 * pushes (useLiveAssay). Live is picked automatically once the selected
 * device starts sending twin_eis -- i.e. a bandage_design simulation is
 * running -- because that is almost always what someone opening this tab on
 * such a device wants to see; the toggle still lets them override either way.
 */
export function BandageTwinView({ live }: Props) {
  const manual = useAssay();
  const [selected, setSelected] = useState<PartKey | null>(null);

  const eisByChannel = live?.eisByChannel;
  const channels = useMemo(() => Object.keys(eisByChannel ?? {}).sort(), [eisByChannel]);
  const [channelPick, setChannelPick] = useState<string | null>(null);
  const channel = channelPick && channels.includes(channelPick) ? channelPick : channels[0] ?? null;
  const reading = channel && eisByChannel ? eisByChannel[channel] ?? null : null;
  const liveState = useLiveAssay(reading, live?.connected ?? false);

  // A manual choice belongs to the device it was made on: switching device
  // should re-evaluate the auto default rather than carry "Live" over to a
  // device that has no bandage twin (or "Manual" to one that does).
  const [override, setOverride] = useState<TwinMode | null>(null);
  const deviceId = live?.deviceId ?? null;
  useEffect(() => {
    setOverride(null);
    setChannelPick(null);
  }, [deviceId]);

  const hasLive = channels.length > 0;
  const mode: TwinMode = live ? override ?? (hasLive ? "live" : "manual") : "manual";
  const state: AssayState = mode === "live" ? liveState : manual.state;

  const statusText =
    mode === "live"
      ? !reading
        ? "Live · waiting for bandage twin"
        : `Live · ${channel}${liveState.stale ? " · stale" : ""}`
      : PHASE_LABEL[state.phase];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_30px_rgba(15,23,42,0.06)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-[0_8px_30px_rgba(0,0,0,0.35)] dark:backdrop-blur-xl">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-widest text-amber-600 dark:text-amber-300/80">Digital twin · graphene-PBASE-aptamer electrode</p>
          <h1 className="font-display mt-1 text-xl font-semibold tracking-tight text-slate-900 dark:text-white">Smart Bandage — E. coli EIS Digital Twin</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {live && (
            <div className="flex items-center gap-1" role="group" aria-label="Twin data source">
              {(
                [
                  ["manual", "Manual assay", "Run the self-contained browser assay step by step"],
                  ["live", "Live", "Show the backend bandage twin's live EIS readings for the selected device"],
                ] as const
              ).map(([id, label, title]) => (
                <button
                  key={id}
                  type="button"
                  title={title}
                  aria-pressed={mode === id}
                  onClick={() => setOverride(id)}
                  className={`${btnBase} ${mode === id ? btnOn : btnOff}`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <p className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 dark:border-white/10 dark:text-slate-300" role="status" aria-live="polite">
            {mode === "live" ? "Source" : "Step"}: <span className="font-semibold text-slate-900 dark:text-white">{statusText}</span>
          </p>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="lg:sticky lg:top-4 lg:self-start">
          <BandageTwin3D
            state={state}
            selected={selected}
            onSelect={setSelected}
            className="h-[52vh] min-h-[320px] w-full overflow-hidden rounded-2xl border border-slate-200 dark:border-white/10 lg:h-[calc(100vh-9rem)]"
          />
        </div>
        <div className="space-y-4">
          {mode === "live" && live && (
            <LiveFeedCard
              live={live}
              reading={reading}
              stale={liveState.stale}
              channels={channels}
              channel={channel}
              onChannel={setChannelPick}
            />
          )}
          <EisPanel state={state} controls={mode === "live" ? null : manual.controls} selected={selected} onSelect={setSelected} />
          {/* The cross-check compares the browser model with the backend at one
              fixed concentration; on the live path the concentration moves every
              tick (which resets the check), and the live data already IS the backend. */}
          {mode === "manual" && <BackendCheck cfu={state.cfuPerMl} />}
        </div>
      </div>
    </div>
  );
}

interface LiveFeedCardProps {
  live: BandageTwinLiveFeed;
  reading: TwinEisReading | null;
  stale: boolean;
  channels: string[];
  channel: string | null;
  onChannel: (c: string) => void;
}

/** Stands in for EisPanel's workflow card on the live path: where the data comes from and how fresh it is. */
function LiveFeedCard({ live, reading, stale, channels, channel, onChannel }: LiveFeedCardProps) {
  const truth = live.lastBandageTruth;
  const chTruth = truth && channel ? truth.truth.channels?.[channel] : undefined;

  return (
    <section className={CARD} aria-label="Live bandage twin feed">
      <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        Live bandage twin
        <InfoHint
          align="left"
          text="Readings pushed over the device WebSocket by the backend's physically-modelled bandage twin (wound bed, diffusion, aptamer binding, electrode fouling, EIS). Start one from the Device monitor's Digital twin panel with a bandage design."
        />
      </h2>

      {!reading ? (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-500">
          {live.deviceId
            ? live.connected
              ? "No EIS readings from this device yet. Start a bandage-design simulation from the Device monitor tab."
              : "Not connected to this device's live stream."
            : "Select a device to see its live bandage twin."}
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            <span className="relative flex h-2 w-2">
              {!stale && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
              <span className={`relative inline-flex h-2 w-2 rounded-full ${stale ? "bg-slate-400 dark:bg-slate-600" : "bg-emerald-500"}`} />
            </span>
            <span className={`font-mono text-[11px] uppercase tracking-widest ${stale ? "text-slate-500" : "text-emerald-600 dark:text-emerald-300/80"}`}>
              {stale ? "Stale" : "Live"}
            </span>
            <span className="font-mono text-[11px] text-slate-500">{reading.device_id}</span>
            {channels.length > 1 ? (
              <select
                value={channel ?? ""}
                onChange={(e) => onChannel(e.target.value)}
                aria-label="EIS channel"
                className="ml-auto rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
              >
                {channels.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            ) : (
              <span className="ml-auto font-mono text-[11px] text-slate-500">{channel}</span>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div>
              <dt className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">Simulated time</dt>
              <dd className="font-mono text-slate-800 dark:text-slate-100">{fmtSimTime(reading.eis.t_s)}</dd>
            </div>
            <div>
              <dt className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Local marker
                <InfoHint text="Pathogen-marker concentration at the electrode surface after diffusion from the wound bed -- what the aptamers actually see." />
              </dt>
              <dd className="font-mono text-slate-800 dark:text-slate-100">{Number.isFinite(reading.eis.local_concentration_nM) ? reading.eis.local_concentration_nM.toFixed(2) : "—"} nM</dd>
            </div>
          </dl>

          {import.meta.env.DEV && truth && (
            <div className="border-t border-dashed border-slate-200 pt-3 dark:border-white/10">
              <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Ground truth
                <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-amber-700 dark:bg-amber-400/10 dark:text-amber-300">
                  dev only
                </span>
                <InfoHint text="The bandage twin's hidden physical state (twin_bandage_truth) -- never sent by the backend in production." />
              </h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-[11px] text-slate-700 dark:text-slate-200">
                <dt className="text-slate-500">design</dt>
                <dd>{truth.bandage_design.replaceAll("_", " ")}</dd>
                <dt className="text-slate-500">bacterial load</dt>
                <dd>{truth.truth.bacterial_load.toFixed(2)}</dd>
                <dt className="text-slate-500">contact</dt>
                <dd>{truth.truth.contact.toFixed(2)}</dd>
                {chTruth && (
                  <>
                    <dt className="text-slate-500">{channel} fouling</dt>
                    <dd>{chTruth.fouling.toFixed(3)}</dd>
                    <dt className="text-slate-500">{channel} sensor health</dt>
                    <dd>{chTruth.sensor_health.toFixed(2)}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
