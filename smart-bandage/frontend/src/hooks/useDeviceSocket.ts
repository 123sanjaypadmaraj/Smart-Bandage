import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../api";
import type { AlertItem, DeviceMessage, MeasurementRecord } from "../types";

interface UseDeviceSocketResult {
  connected: boolean;
  lastMeasurement: MeasurementRecord | null;
  liveMeasurements: MeasurementRecord[];
  liveAlerts: AlertItem[];
}

const LIVE_BUFFER_SIZE = 300;

/** WS /ws/devices/{device_id} -- live push to the dashboard (Blueprint API §6). */
export function useDeviceSocket(deviceId: string | null): UseDeviceSocketResult {
  const [connected, setConnected] = useState(false);
  const [lastMeasurement, setLastMeasurement] = useState<MeasurementRecord | null>(null);
  const [liveMeasurements, setLiveMeasurements] = useState<MeasurementRecord[]>([]);
  const [liveAlerts, setLiveAlerts] = useState<AlertItem[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setLiveMeasurements([]);
    setLiveAlerts([]);
    setLastMeasurement(null);

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
      }
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [deviceId]);

  return { connected, lastMeasurement, liveMeasurements, liveAlerts };
}
