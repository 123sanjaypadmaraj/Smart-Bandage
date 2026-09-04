import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { api, ApiError } from "../api";
import type { Device } from "../types";

interface Props {
  token: string;
  onSelectDevice: (deviceId: string) => void;
  onLogout: () => void;
}

const statusColor: Record<Device["status"], string> = {
  online: "#22d3ee",
  offline: "#fb7185",
  unknown: "#475569",
};

/** Mirrors the dashboard's device sidebar (frontend/src/components/Sidebar.tsx),
 * GET /devices. */
export function DevicesScreen({ token, onSelectDevice, onLogout }: Props) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDevices(await api.listDevices(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load devices");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Devices</Text>
        <Pressable onPress={onLogout}>
          <Text style={styles.logout}>Sign out</Text>
        </Pressable>
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      <FlatList
        data={devices}
        keyExtractor={(d) => d.device_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#22d3ee" />}
        ListEmptyComponent={<Text style={styles.empty}>No devices yet -- register one from the dashboard.</Text>}
        renderItem={({ item }) => (
          <Pressable style={styles.row} onPress={() => onSelectDevice(item.device_id)}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{item.name}</Text>
              <Text style={styles.rowSubtitle}>{item.device_id}</Text>
            </View>
            <View style={[styles.dot, { backgroundColor: statusColor[item.status] }]} />
          </Pressable>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0b1120", paddingTop: 56, paddingHorizontal: 20 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 },
  title: { color: "#f1f5f9", fontSize: 22, fontWeight: "700" },
  logout: { color: "#94a3b8", fontSize: 13 },
  error: { color: "#fb7185", marginBottom: 12 },
  empty: { color: "#64748b", textAlign: "center", marginTop: 40 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#1e293b",
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    backgroundColor: "#0f172a",
  },
  rowTitle: { color: "#e2e8f0", fontSize: 16, fontWeight: "600" },
  rowSubtitle: { color: "#64748b", fontSize: 12, marginTop: 2 },
  dot: { width: 10, height: 10, borderRadius: 5, marginLeft: 12 },
});
