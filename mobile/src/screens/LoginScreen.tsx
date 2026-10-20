import { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { api, ApiError } from "../api";

interface Props {
  onLoggedIn: (token: string) => void;
}

/** Mirrors the dashboard's sign-in flow (frontend/src/App.tsx) against the
 * same POST /auth/login -- dev seed login is admin/admin, see
 * backend/app/security.py. */
export function LoginScreen({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.login(username, password);
      onLoggedIn(access_token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend");
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Smart Bandage</Text>
      <Text style={styles.subtitle}>Care Platform</Text>

      <View style={styles.form}>
        <Text style={styles.label}>Username</Text>
        <TextInput
          style={styles.input}
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Text style={styles.label}>Password</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoCapitalize="none"
        />
        {error && <Text style={styles.error}>{error}</Text>}
        <Pressable style={styles.button} onPress={handleSubmit} disabled={loading}>
          {loading ? <ActivityIndicator color="#0f172a" /> : <Text style={styles.buttonText}>Sign in</Text>}
        </Pressable>
        <Text style={styles.hint}>Dev default: admin / admin</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0b1120", padding: 24, justifyContent: "center" },
  title: { color: "#f1f5f9", fontSize: 28, fontWeight: "700" },
  subtitle: { color: "#3b82f6", fontSize: 16, fontWeight: "600", marginBottom: 32 },
  form: { gap: 8 },
  label: { color: "#94a3b8", fontSize: 13, marginTop: 8 },
  input: {
    borderWidth: 1,
    borderColor: "#334155",
    borderRadius: 8,
    padding: 12,
    color: "#f1f5f9",
    backgroundColor: "#0f172a",
  },
  error: { color: "#fb7185", fontSize: 13, marginTop: 4 },
  button: {
    marginTop: 16,
    backgroundColor: "#3b82f6",
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: "center",
  },
  buttonText: { color: "#0f172a", fontWeight: "700" },
  hint: { color: "#64748b", fontSize: 12, marginTop: 12, textAlign: "center" },
});
