import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { WS_BASE } from "../config";
import type { AlertItem, DeviceMessage } from "../types";

interface UseAlertsSocketResult {
  /** Unresolved alerts seen so far, per device, most-recent first. */
  alertsByDevice: Record<string, AlertItem[]>;
  /** Total unresolved-alert count across every subscribed device, for a badge. */
  totalCount: number;
  /** The single most recently received alert, from any device -- a fresh
   * object each time one arrives, meant to drive a toast/banner. */
  latestAlert: AlertItem | null;
}

const LIVE_BUFFER_SIZE = 20;

function addAlert(prev: Record<string, AlertItem[]>, alert: AlertItem): Record<string, AlertItem[]> {
  const existing = prev[alert.device_id] ?? [];
  if (alert.id != null && existing.some((a) => a.id === alert.id)) return prev; // already have it (REST seed + WS overlap)
  return { ...prev, [alert.device_id]: [alert, ...existing].slice(0, LIVE_BUFFER_SIZE) };
}

/**
 * App-wide counterpart to useDeviceSocket: rather than watching whichever
 * single device happens to be on screen, this opens one WS
 * /ws/devices/{device_id} connection per id in `deviceIds` and tracks only
 * "alert" messages (measurements are dropped -- nothing here renders a
 * chart). Meant to run for the lifetime of a signed-in session, owned above
 * whichever screen is currently showing, so an alert on device B still
 * surfaces while device A's monitor screen is open.
 *
 * Seeded once from GET /alerts (every currently-active alert, across every
 * device) so badge counts are right immediately, not just for alerts that
 * arrive after this mounts.
 */
export function useAlertsSocket(deviceIds: string[]): UseAlertsSocketResult {
  const [alertsByDevice, setAlertsByDevice] = useState<Record<string, AlertItem[]>>({});
  const [latestAlert, setLatestAlert] = useState<AlertItem | null>(null);
  const socketsRef = useRef<Map<string, WebSocket>>(new Map());

  useEffect(() => {
    let cancelled = false;
    api
      .alerts()
      .then((seed) => {
        if (cancelled) return;
        setAlertsByDevice((prev) => seed.reduce((acc, alert) => addAlert(acc, alert), prev));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Sorting + joining keeps this a stable primitive, so the effect below
  // only reconnects sockets when the actual set of ids changes -- not on
  // every render of whichever screen passes a fresh array in.
  const idsKey = [...deviceIds].sort().join(",");

  useEffect(() => {
    const ids = idsKey ? idsKey.split(",") : [];
    const sockets = socketsRef.current;

    for (const id of ids) {
      if (sockets.has(id)) continue;
      const socket = new WebSocket(`${WS_BASE}/ws/devices/${encodeURIComponent(id)}`);
      socket.onmessage = (event) => {
        let msg: DeviceMessage;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type !== "alert") return;
        setAlertsByDevice((prev) => addAlert(prev, msg.alert));
        setLatestAlert(msg.alert);
      };
      sockets.set(id, socket);
    }

    for (const [id, socket] of [...sockets]) {
      if (!ids.includes(id)) {
        socket.close();
        sockets.delete(id);
      }
    }
  }, [idsKey]);

  // Full teardown only on unmount (sign-out) -- per-id teardown for a
  // shrinking device list is handled in the effect above.
  useEffect(() => {
    return () => {
      for (const socket of socketsRef.current.values()) socket.close();
      socketsRef.current.clear();
    };
  }, []);

  const totalCount = Object.values(alertsByDevice).reduce((sum, list) => sum + list.length, 0);

  return { alertsByDevice, totalCount, latestAlert };
}
