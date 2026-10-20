/**
 * Where the app finds the Phase 4 backend. Unlike the dashboard
 * (frontend/.env.local, read at build time by Vite), Expo apps running on
 * a physical device or emulator can't reach your dev machine via
 * "localhost" -- that resolves to the device itself. Point these at your
 * machine's LAN IP instead (e.g. http://192.168.1.23:8000), or leave the
 * localhost defaults when running in a browser via `npm run web`.
 */
export const API_BASE = process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
export const WS_BASE = process.env.EXPO_PUBLIC_WS_BASE_URL ?? "ws://localhost:8000";
