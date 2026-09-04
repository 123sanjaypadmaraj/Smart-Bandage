import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { api, ApiError } from "../api";
import { useDeviceSocket } from "../hooks/useDeviceSocket";
import type { AIChatTurn, DeviceStatus } from "../types";

interface Props {
  token: string;
  deviceId: string;
  onBack: () => void;
}

/** Mirrors the dashboard's live monitoring panel (frontend/src/App.tsx +
 * useDeviceSocket), GET /device-status + WS /ws/devices/{id}. No chart
 * library here -- the live readings feed and alerts list carry the same
 * information a phone screen has room to show. */
export function MonitorScreen({ token, deviceId, onBack }: Props) {
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const { connected, lastMeasurement, liveAlerts } = useDeviceSocket(deviceId);

  useEffect(() => {
    let cancelled = false;
    api
      .deviceStatus(deviceId)
      .then((s) => !cancelled && setStatus(s))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [deviceId]);

  // Phase 10: AI analysis -- mirrors frontend/src/components/AIInsightPanel.tsx.
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiNotConfigured, setAiNotConfigured] = useState(false);
  const [chatMessages, setChatMessages] = useState<AIChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);

  function loadInsight() {
    setAiLoading(true);
    setAiError(null);
    setAiNotConfigured(false);
    api
      .aiInsight(token, deviceId)
      .then((result) => setAiSummary(result.summary))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 503) setAiNotConfigured(true);
        else if (err instanceof ApiError && err.status === 404) setAiError("No readings yet for this device.");
        else setAiError("Could not load an AI insight.");
      })
      .finally(() => setAiLoading(false));
  }

  useEffect(() => {
    setAiSummary(null);
    setChatMessages([]);
    setChatInput("");
    loadInsight();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  async function sendChat() {
    const message = chatInput.trim();
    if (!message || chatSending) return;
    setChatInput("");
    const history = chatMessages;
    setChatMessages((prev) => [...prev, { role: "user", text: message }]);
    setChatSending(true);
    try {
      const result = await api.aiChat(token, deviceId, message, history);
      setChatMessages((prev) => [...prev, { role: "model", text: result.reply }]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setAiNotConfigured(true);
      setChatMessages((prev) => prev.slice(0, -1));
      setChatInput(message);
    } finally {
      setChatSending(false);
    }
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Pressable onPress={onBack}>
          <Text style={styles.back}>{"< Devices"}</Text>
        </Pressable>
        <View style={styles.liveBadge}>
          <View style={[styles.dot, { backgroundColor: connected ? "#22d3ee" : "#fb7185" }]} />
          <Text style={styles.liveText}>{connected ? "Live" : "Disconnected"}</Text>
        </View>
      </View>

      <Text style={styles.title}>{deviceId}</Text>

      <View style={styles.statGrid}>
        <Stat label="Battery" value={status?.battery != null ? `${Math.round(status.battery)}%` : "--"} />
        <Stat
          label="Signal quality"
          value={status?.signal_quality != null ? `${Math.round(status.signal_quality * 100)}%` : "--"}
        />
        <Stat label="Connected" value={status?.connected ? "Yes" : "No"} />
      </View>

      <Text style={styles.sectionTitle}>Latest reading</Text>
      {lastMeasurement ? (
        <View style={styles.card}>
          <Text style={styles.readingValue}>
            {lastMeasurement.estimated_value?.toFixed(2) ?? "--"} {lastMeasurement.unit ?? ""}
          </Text>
          <Text style={styles.readingMeta}>
            channel {lastMeasurement.channel_id} · {lastMeasurement.status} ·{" "}
            {new Date(lastMeasurement.timestamp).toLocaleTimeString()}
          </Text>
        </View>
      ) : (
        <Text style={styles.empty}>No live readings yet -- start a simulation from the dashboard.</Text>
      )}

      <ScrollView style={{ flex: 1 }}>
        <Text style={styles.sectionTitle}>Alerts</Text>
        {liveAlerts.length === 0 && <Text style={styles.empty}>No active alerts.</Text>}
        {liveAlerts.map((alert, i) => (
          <View key={i} style={styles.alertRow}>
            <Text style={styles.alertSeverity}>{alert.severity.toUpperCase()}</Text>
            <Text style={styles.alertMessage}>{alert.message}</Text>
          </View>
        ))}

        <View style={styles.aiHeaderRow}>
          <Text style={styles.sectionTitle}>AI insight</Text>
          <Pressable onPress={loadInsight} disabled={aiLoading}>
            <Text style={styles.aiRefresh}>{aiLoading ? "Refreshing…" : "Refresh"}</Text>
          </Pressable>
        </View>
        {aiNotConfigured ? (
          <Text style={styles.empty}>AI analysis isn't configured on this backend yet.</Text>
        ) : aiError ? (
          <Text style={styles.aiError}>{aiError}</Text>
        ) : aiLoading && !aiSummary ? (
          <ActivityIndicator color="#22d3ee" style={{ marginVertical: 8 }} />
        ) : aiSummary ? (
          <View style={styles.card}>
            <Text style={styles.aiSummary}>{aiSummary}</Text>
          </View>
        ) : null}

        {!aiNotConfigured && (
          <>
            {chatMessages.map((m, i) => (
              <View key={i} style={[styles.chatBubble, m.role === "user" ? styles.chatBubbleUser : styles.chatBubbleModel]}>
                <Text style={styles.chatText}>{m.text}</Text>
              </View>
            ))}
            {chatSending && <ActivityIndicator color="#22d3ee" style={{ marginVertical: 6 }} />}
            <View style={styles.chatInputRow}>
              <TextInput
                value={chatInput}
                onChangeText={setChatInput}
                placeholder="Ask about this device's data…"
                placeholderTextColor="#475569"
                style={styles.chatInput}
                onSubmitEditing={sendChat}
              />
              <Pressable onPress={sendChat} disabled={chatSending || !chatInput.trim()} style={styles.chatSend}>
                <Text style={styles.chatSendText}>Send</Text>
              </Pressable>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0b1120", paddingTop: 56, paddingHorizontal: 20 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  back: { color: "#3b82f6", fontSize: 14 },
  liveBadge: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  liveText: { color: "#94a3b8", fontSize: 12 },
  title: { color: "#f1f5f9", fontSize: 22, fontWeight: "700", marginTop: 12, marginBottom: 16 },
  statGrid: { flexDirection: "row", gap: 10, marginBottom: 20 },
  stat: { flex: 1, backgroundColor: "#0f172a", borderRadius: 10, borderWidth: 1, borderColor: "#1e293b", padding: 12 },
  statLabel: { color: "#64748b", fontSize: 11, textTransform: "uppercase" },
  statValue: { color: "#e2e8f0", fontSize: 18, fontWeight: "700", marginTop: 4 },
  sectionTitle: { color: "#94a3b8", fontSize: 12, textTransform: "uppercase", marginBottom: 8, marginTop: 4 },
  card: { backgroundColor: "#0f172a", borderRadius: 10, borderWidth: 1, borderColor: "#1e293b", padding: 14, marginBottom: 12 },
  readingValue: { color: "#22d3ee", fontSize: 26, fontWeight: "700" },
  readingMeta: { color: "#64748b", fontSize: 12, marginTop: 4 },
  empty: { color: "#64748b", marginBottom: 12 },
  alertRow: { borderBottomWidth: 1, borderColor: "#1e293b", paddingVertical: 10 },
  alertSeverity: { color: "#fb7185", fontSize: 11, fontWeight: "700" },
  alertMessage: { color: "#cbd5e1", fontSize: 13, marginTop: 2 },
  aiHeaderRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  aiRefresh: { color: "#3b82f6", fontSize: 12 },
  aiError: { color: "#fb7185", fontSize: 12, marginBottom: 8 },
  aiSummary: { color: "#e2e8f0", fontSize: 13, lineHeight: 19 },
  chatBubble: { borderRadius: 10, padding: 10, marginTop: 8, maxWidth: "85%" },
  chatBubbleUser: { backgroundColor: "#164e63", alignSelf: "flex-end" },
  chatBubbleModel: { backgroundColor: "#0f172a", borderWidth: 1, borderColor: "#1e293b", alignSelf: "flex-start" },
  chatText: { color: "#e2e8f0", fontSize: 13 },
  chatInputRow: { flexDirection: "row", gap: 8, marginTop: 10, marginBottom: 24, alignItems: "center" },
  chatInput: {
    flex: 1,
    backgroundColor: "#0f172a",
    borderWidth: 1,
    borderColor: "#1e293b",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    color: "#e2e8f0",
    fontSize: 13,
  },
  chatSend: { backgroundColor: "#22d3ee", borderRadius: 8, paddingHorizontal: 14, paddingVertical: 9 },
  chatSendText: { color: "#0b1120", fontSize: 12, fontWeight: "700" },
});
