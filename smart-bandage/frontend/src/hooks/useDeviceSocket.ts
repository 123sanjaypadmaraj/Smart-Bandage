import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../api";
import type {
  AlertItem,
  DeviceMessage,
  MeasurementRecord,
  TwinBandageTruth,
  TwinEisReading,
  TwinGroundTruth,
} from "../types";

interface UseDeviceSocketResult {
  connected: boolean;
  lastMeasurement: MeasurementRecord | null;
  liveMeasurements: MeasurementRecord[];
  liveAlerts: AlertItem[];
  // DT-6: only ever populated for a twin-backed simulation, and only in
  // dev (the backend never broadcasts twin_ground_truth in production) --
  // see components/TwinControlPanel.tsx.
  lastGroundTruth: TwinGroundTruth | null;
  groundTruthHistory: TwinGroundTruth[];
  // DT-3 bandage twin: populated only while a bandage_design simulation
  // runs. twin_eis is a real measurement (sent in production too) and
  // feeds the 3D twin's Live mode -- see twin3d/useLiveAssay.ts.
  lastEis: TwinEisReading | null;
  eisHistory: TwinEisReading[];
  /** Latest reading per EIS channel. A bandage can carry several pathogen
   * electrodes that each broadcast every tick, so `lastEis` alone would
   * flip between channels; the 3D view pins one channel from here. */
  eisByChannel: Record<string, TwinEisReading>;
  // Dev-only, like lastGroundTruth.
  lastBandageTruth: TwinBandageTruth | null;
  bandageTruthHistory: TwinBandageTruth[];
}

const LIVE_BUFFER_SIZE = 300;
const GROUND_TRUTH_BUFFER_SIZE = 120;
// Same depth as the ground-truth ring: enough for a sparkline, and each
// twin_eis carries a full ~61-point spectrum so this is the bigger one.
const EIS_BUFFER_SIZE = GROUND_TRUTH_BUFFER_SIZE;
const BANDAGE_TRUTH_BUFFER_SIZE = GROUND_TRUTH_BUFFER_SIZE;

/** WS /ws/devices/{device_id} -- live push to the dashboard (Blueprint API §6). */
export function useDeviceSocket(deviceId: string | null): UseDeviceSocketResult {
  const [connected, setConnected] = useState(false);
  const [lastMeasurement, setLastMeasurement] = useState<MeasurementRecord | null>(null);
  const [liveMeasurements, setLiveMeasurements] = useState<MeasurementRecord[]>([]);
  const [liveAlerts, setLiveAlerts] = useState<AlertItem[]>([]);
  const [lastGroundTruth, setLastGroundTruth] = useState<TwinGroundTruth | null>(null);
  const [groundTruthHistory, setGroundTruthHistory] = useState<TwinGroundTruth[]>([]);
  const [lastEis, setLastEis] = useState<TwinEisReading | null>(null);
  const [eisHistory, setEisHistory] = useState<TwinEisReading[]>([]);
  const [eisByChannel, setEisByChannel] = useState<Record<string, TwinEisReading>>({});
  const [lastBandageTruth, setLastBandageTruth] = useState<TwinBandageTruth | null>(null);
  const [bandageTruthHistory, setBandageTruthHistory] = useState<TwinBandageTruth[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setLiveMeasurements([]);
    setLiveAlerts([]);
    setLastMeasurement(null);
    setLastGroundTruth(null);
    setGroundTruthHistory([]);
    setLastEis(null);
    setEisHistory([]);
    setEisByChannel({});
    setLastBandageTruth(null);
    setBandageTruthHistory([]);

    if (!deviceId) return;

    const socket = new WebSocket(`${WS_BASE}/ws/devices/${encodeURIComponent(deviceId)}`);
    socketRef.current = socket;

    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onerror = () => setConnected(false);

    socket.onmessage = (event) => {
      let msg: DeviceMessage;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "measurement") {
        setLastMeasurement(msg.measurement);
        setLiveMeasurements((prev) => [...prev.slice(-(LIVE_BUFFER_SIZE - 1)), msg.measurement]);
      } else if (msg.type === "alert") {
        setLiveAlerts((prev) => [msg.alert, ...prev].slice(0, 50));
      } else if (msg.type === "twin_ground_truth") {
        setLastGroundTruth(msg.ground_truth);
        setGroundTruthHistory((prev) => [...prev.slice(-(GROUND_TRUTH_BUFFER_SIZE - 1)), msg.ground_truth]);
      } else if (msg.type === "twin_eis") {
        // A malformed push must not reach useLiveAssay, which would render
        // NaNs into the 3D scene rather than fail loudly.
        if (!msg.eis || !Array.isArray(msg.eis.spectrum)) return;
        const reading: TwinEisReading = {
          device_id: msg.device_id,
          channel_id: msg.channel_id ?? msg.eis.channel_id,
          timestamp: msg.timestamp,
          eis: msg.eis,
        };
        setLastEis(reading);
        setEisHistory((prev) => [...prev.slice(-(EIS_BUFFER_SIZE - 1)), reading]);
        setEisByChannel((prev) => ({ ...prev, [reading.channel_id]: reading }));
      } else if (msg.type === "twin_bandage_truth") {
        if (!msg.truth) return;
        const truth: TwinBandageTruth = {
          device_id: msg.device_id,
          bandage_design: msg.bandage_design,
          timestamp: msg.timestamp,
          truth: msg.truth,
        };
        setLastBandageTruth(truth);
        setBandageTruthHistory((prev) => [...prev.slice(-(BANDAGE_TRUTH_BUFFER_SIZE - 1)), truth]);
      }
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [deviceId]);

  return {
    connected,
    lastMeasurement,
    liveMeasurements,
    liveAlerts,
    lastGroundTruth,
    groundTruthHistory,
    lastEis,
    eisHistory,
    eisByChannel,
    lastBandageTruth,
    bandageTruthHistory,
  };
}
