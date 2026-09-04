import { useState } from "react";
import { StatusBar } from "expo-status-bar";
import { DevicesScreen } from "./src/screens/DevicesScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { MonitorScreen } from "./src/screens/MonitorScreen";

/**
 * Phase "mobile" -- a React Native counterpart to the Phase 5 dashboard
 * (frontend/), against the same Phase 4 backend (see src/api.ts,
 * src/config.ts). No navigation library: three screens, one piece of
 * state deciding which is shown, matching this app's scope.
 */
export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  function handleLogout() {
    setToken(null);
    setSelectedDeviceId(null);
  }

  return (
    <>
      {!token ? (
        <LoginScreen onLoggedIn={setToken} />
      ) : selectedDeviceId ? (
        <MonitorScreen token={token} deviceId={selectedDeviceId} onBack={() => setSelectedDeviceId(null)} />
      ) : (
        <DevicesScreen token={token} onSelectDevice={setSelectedDeviceId} onLogout={handleLogout} />
      )}
      <StatusBar style="light" />
    </>
  );
}
