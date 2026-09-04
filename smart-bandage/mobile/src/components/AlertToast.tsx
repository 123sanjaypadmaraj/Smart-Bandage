import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { AlertItem } from "../types";

interface Props {
  alert: AlertItem;
  onPress: () => void;
  onDismiss: () => void;
}

const SEVERITY_COLOR: Record<AlertItem["severity"], string> = {
  info: "#3b82f6",
  warning: "#f59e0b",
  critical: "#fb7185",
};

const AUTO_DISMISS_MS = 5000;

/** A single transient banner for an alert that arrived on a device the user
 * isn't currently looking at (see useAlertsSocket) -- tap to jump to that
 * device's monitor screen, or let it clear itself. */
export function AlertToast({ alert, onPress, onDismiss }: Props) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [alert]);

  useEffect(() => {
    if (!visible) {
      const timer = setTimeout(onDismiss, 200);
      return () => clearTimeout(timer);
    }
  }, [visible, onDismiss]);

  if (!visible) return null;

  return (
    <Pressable
      style={[styles.toast, { borderLeftColor: SEVERITY_COLOR[alert.severity] }]}
      onPress={() => {
        setVisible(false);
        onPress();
      }}
    >
      <View style={styles.row}>
        <Text style={[styles.severity, { color: SEVERITY_COLOR[alert.severity] }]}>{alert.severity.toUpperCase()}</Text>
        <Text style={styles.device}>{alert.device_id}</Text>
      </View>
      <Text style={styles.message} numberOfLines={2}>
        {alert.message}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  toast: {
    position: "absolute",
    top: 52,
    left: 16,
    right: 16,
    backgroundColor: "#0f172a",
    borderWidth: 1,
    borderColor: "#1e293b",
    borderLeftWidth: 4,
    borderRadius: 10,
    padding: 12,
    shadowColor: "#000",
    shadowOpacity: 0.3,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
    zIndex: 50,
  },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  severity: { fontSize: 11, fontWeight: "700" },
  device: { color: "#64748b", fontSize: 11, fontFamily: "monospace" },
  message: { color: "#e2e8f0", fontSize: 13, marginTop: 4 },
});
