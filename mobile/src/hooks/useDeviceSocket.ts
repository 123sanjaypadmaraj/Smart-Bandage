import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../config";
import type { AlertItem, DeviceMessage, MeasurementRecord, TwinGroundTruth } from "../types";

interface UseDeviceSocketResult {
  connected: boolean;
  lastMeasurement: MeasurementRecord | null;
  liveMeasurements: MeasurementRecord[];
  liveAlerts: AlertItem[];
  // DT-6: only ever populated for a twin-backed simulation, and only in dev
  // (the backend never broadcasts twin_ground_truth in production) -- see
  // components/TwinControlPanel.tsx.
  lastGroundTruth: TwinGroundTruth | null;
  groundTruthHistory: TwinGroundTruth[];
}

const LIVE_BUFFER_SIZE = 100;
const GROUND_TRUTH_BUFFER_SIZE = 60;

/** WS /ws/devices/{device_id} -- live push to the app (Blueprint API §6).
 * Ported from frontend/src/hooks/useDeviceSocket.ts -- React Native's
 * global WebSocket has the same browser-style API, so this is unchanged
 * apart from importing WS_BASE from ./config instead of ../api. */
export function useDeviceSocket(deviceId: string | null): UseDeviceSocketResult {
  const [connected, setConnected] = useState(false);
  const [lastMeasurement, setLastMeasurement] = useState<MeasurementRecord | null>(null);
  const [liveMeasurements, setLiveMeasurements] = useState<MeasurementRecord[]>([]);
  const [liveAlerts, setLiveAlerts] = useState<AlertItem[]>([]);
  const [lastGroundTruth, setLastGroundTruth] = useState<TwinGroundTruth | null>(null);
  const [groundTruthHistory, setGroundTruthHistory] = useState<TwinGroundTruth[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setLiveMeasurements([]);
    setLiveAlerts([]);
    setLastMeasurement(null);
    setLastGroundTruth(null);
    setGroundTruthHistory([]);

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
      }
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [deviceId]);

  return { connected, lastMeasurement, liveMeasurements, liveAlerts, lastGroundTruth, groundTruthHistory };
}
