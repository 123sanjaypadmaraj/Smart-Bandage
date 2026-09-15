import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { api, ApiError } from "../api";
import type { PatientProfileInfo, TwinGroundTruth } from "../types";

interface Props {
  token: string;
  deviceId: string;
  channels: string[];
  /** From useDeviceSocket -- the WS "twin_ground_truth" stream (dev-only,
   * the backend never broadcasts it in production; see
   * backend/app/routers/simulation.py). */
  lastGroundTruth: TwinGroundTruth | null;
}

// WoundState's plausible range for each variable (digital_twin/state.py) --
// what the bars below normalize against. Mirrors
// frontend/src/components/TwinControlPanel.tsx.
const STATE_RANGE: Record<"inflammation" | "bacterial_load" | "moisture" | "perfusion", number> = {
  inflammation: 1.5,
  bacterial_load: 1.5,
  moisture: 1.0,
  perfusion: 1.0,
};

const STATE_LABELS: Record<keyof typeof STATE_RANGE, string> = {
  inflammation: "Inflammation",
  bacterial_load: "Bacterial load",
  moisture: "Moisture",
  perfusion: "Perfusion",
};

/**
 * DT-6: patient profile + time-scale controls for the twin-backed
 * simulation mode (backend/app/simulation.py's DigitalTwinDevice), plus a
 * dev-only "ground truth overlay" comparing the hidden physiological state
 * it's actually integrating against what the (noisy, fouled) channel
 * reports for it. Mirrors the dashboard's TwinControlPanel.tsx: no chart
 * library and no third-party UI kit on mobile (see MonitorScreen's
 * comment / this app's dependency list), so profile selection is a row of
 * Pressable chips instead of a <select>, and the overlay is numbers + bars
 * instead of a sparkline pair. __DEV__ is Expo/React Native's build-mode
 * flag, the RN equivalent of Vite's import.meta.env.DEV.
 */
export function TwinControlPanel({ token, deviceId, channels, lastGroundTruth }: Props) {
  const [profiles, setProfiles] = useState<PatientProfileInfo[]>([]);
  const [patientProfile, setPatientProfile] = useState<string>("");
  const [timeScale, setTimeScale] = useState("60");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .patientProfiles()
      .then((list) => {
        setProfiles(list);
        setPatientProfile((current) => current || list[0]?.name || "");
      })
      .catch(() => {
        /* twin control panel is non-critical to show on first load */
      });
  }, []);

  // Reset per-device UI state on device switch -- otherwise "running" would
  // carry over to a device that was never started as a twin.
  useEffect(() => setRunning(false), [deviceId]);

  async function withBusy(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    withBusy(async () => {
      await api.startSimulation(token, {
        device_id: deviceId,
        channels: channels.length ? channels : ["CH-01"],
        patient_profile: patientProfile,
        time_scale: Math.max(1, Number(timeScale) || 1),
      });
      setRunning(true);
    });

  const stop = () =>
    withBusy(async () => {
      await api.stopSimulation(token, deviceId);
      setRunning(false);
    });

  const activeProfile = profiles.find((p) => p.name === patientProfile);

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>Digital twin</Text>
      {activeProfile && <Text style={styles.hint}>{activeProfile.description}</Text>}

      <View style={styles.chipRow}>
        {profiles.map((p) => {
          const selected = p.name === patientProfile;
          return (
            <Pressable
              key={p.name}
              onPress={() => !running && setPatientProfile(p.name)}
              disabled={running}
              style={[styles.chip, selected && styles.chipSelected]}
            >
              <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{p.name.replaceAll("_", " ")}</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.row}>
        <Text style={styles.timeScaleLabel}>time scale</Text>
        <TextInput
          value={timeScale}
          onChangeText={setTimeScale}
          editable={!running}
          keyboardType="number-pad"
          style={styles.timeScaleInput}
        />

        {!running ? (
          <Pressable
            onPress={start}
            disabled={busy || !patientProfile}
            style={[styles.button, styles.buttonStart, (busy || !patientProfile) && styles.buttonDisabled]}
          >
            <Text style={styles.buttonText}>Start twin</Text>
          </Pressable>
        ) : (
          <Pressable onPress={stop} disabled={busy} style={[styles.button, styles.buttonStop, busy && styles.buttonDisabled]}>
            <Text style={styles.buttonText}>Stop twin</Text>
          </Pressable>
        )}
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      {__DEV__ && (
        <View style={styles.overlay}>
          <View style={styles.overlayHeader}>
            <Text style={styles.overlayTitle}>Ground truth overlay</Text>
            <Text style={styles.devBadge}>dev only</Text>
          </View>

          {!lastGroundTruth ? (
            <Text style={styles.hint}>Start a twin-backed simulation on this device to see estimated vs. true here.</Text>
          ) : (
            <View>
              <View style={styles.compareRow}>
                <Text style={styles.compareLabel}>Estimated vs true</Text>
                <Text style={styles.compareValue}>
                  <Text style={styles.estimated}>{lastGroundTruth.estimated_signal?.toFixed(2) ?? "--"}</Text>
                  <Text style={styles.compareSlash}> / </Text>
                  <Text style={styles.trueValue}>{lastGroundTruth.true_signal.toFixed(2)}</Text>
                </Text>
              </View>

              {(Object.keys(STATE_RANGE) as (keyof typeof STATE_RANGE)[]).map((key) => {
                const value = lastGroundTruth[key];
                const pct = Math.max(0, Math.min(100, (value / STATE_RANGE[key]) * 100));
                return (
                  <View key={key} style={styles.barRow}>
                    <View style={styles.barLabelRow}>
                      <Text style={styles.barLabel}>{STATE_LABELS[key]}</Text>
                      <Text style={styles.barValue}>{value.toFixed(2)}</Text>
                    </View>
                    <View style={styles.barTrack}>
                      <View style={[styles.barFill, { width: `${pct}%` }]} />
                    </View>
                  </View>
                );
              })}
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: "#0f172a", borderRadius: 10, borderWidth: 1, borderColor: "#1e293b", padding: 14, marginBottom: 12 },
  heading: { color: "#94a3b8", fontSize: 12, fontWeight: "700", textTransform: "uppercase" },
  hint: { color: "#64748b", fontSize: 12, marginTop: 4 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 },
  chip: { borderWidth: 1, borderColor: "#1e293b", borderRadius: 20, paddingHorizontal: 12, paddingVertical: 6, backgroundColor: "#0b1120" },
  chipSelected: { borderColor: "#fbbf24", backgroundColor: "rgba(251,191,36,0.12)" },
  chipText: { color: "#94a3b8", fontSize: 12 },
  chipTextSelected: { color: "#fbbf24", fontWeight: "600" },
  row: { flexDirection: "row", gap: 10, marginTop: 12, alignItems: "center" },
  timeScaleLabel: { color: "#64748b", fontSize: 11 },
  timeScaleInput: {
    width: 52,
    backgroundColor: "#0b1120",
    borderWidth: 1,
    borderColor: "#1e293b",
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 6,
    color: "#e2e8f0",
    fontSize: 13,
    textAlign: "center",
  },
  button: { flex: 1, borderRadius: 8, paddingVertical: 9, alignItems: "center" },
  buttonStart: { backgroundColor: "#fbbf24" },
  buttonStop: { backgroundColor: "#fb7185" },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: "#0b1120", fontSize: 12, fontWeight: "700" },
  error: { color: "#fb7185", fontSize: 12, marginTop: 8 },
  overlay: { borderTopWidth: 1, borderColor: "#1e293b", borderStyle: "dashed", marginTop: 12, paddingTop: 10 },
  overlayHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  overlayTitle: { color: "#64748b", fontSize: 11, fontWeight: "700", textTransform: "uppercase" },
  devBadge: {
    color: "#fbbf24",
    backgroundColor: "rgba(251,191,36,0.12)",
    fontSize: 9,
    fontWeight: "700",
    textTransform: "uppercase",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  compareRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#0b1120",
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
  },
  compareLabel: { color: "#64748b", fontSize: 10, textTransform: "uppercase" },
  compareValue: { fontSize: 15, fontVariant: ["tabular-nums"] },
  estimated: { color: "#fbbf24" },
  compareSlash: { color: "#475569" },
  trueValue: { color: "#34d399" },
  barRow: { marginBottom: 8 },
  barLabelRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 3 },
  barLabel: { color: "#94a3b8", fontSize: 10 },
  barValue: { color: "#94a3b8", fontSize: 10, fontVariant: ["tabular-nums"] },
  barTrack: { height: 6, borderRadius: 3, backgroundColor: "#1e293b", overflow: "hidden" },
  barFill: { height: "100%", borderRadius: 3, backgroundColor: "#fbbf24" },
});
