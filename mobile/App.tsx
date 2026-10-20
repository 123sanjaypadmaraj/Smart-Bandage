import { useState } from "react";
import { StatusBar } from "expo-status-bar";
import { DevicesScreen } from "./src/screens/DevicesScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { MonitorScreen } from "./src/screens/MonitorScreen";
import type { Device } from "./src/types";

/**
 * Phase "mobile" -- a React Native counterpart to the Phase 5 dashboard
 * (frontend/), against the same Phase 4 backend (see src/api.ts,
 * src/config.ts). No navigation library: three screens, one piece of
 * state deciding which is shown, matching this app's scope.
 */
export default function App() {
  const [token, setToken] = useState<string | null>(null);
  // The whole Device, not just its id -- MonitorScreen's DT-6 twin panel
  // needs `channels` to start a twin-backed simulation.
  const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);

  function handleLogout() {
    setToken(null);
    setSelectedDevice(null);
  }

  return (
    <>
      {!token ? (
        <LoginScreen onLoggedIn={setToken} />
      ) : selectedDevice ? (
        <MonitorScreen
          token={token}
          deviceId={selectedDevice.device_id}
          channels={selectedDevice.channels}
          onBack={() => setSelectedDevice(null)}
        />
      ) : (
        <DevicesScreen token={token} onSelectDevice={setSelectedDevice} onLogout={handleLogout} />
      )}
      <StatusBar style="light" />
    </>
  );
}
