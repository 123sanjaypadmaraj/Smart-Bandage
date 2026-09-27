import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Phase 5 dashboard. VITE_API_BASE_URL / VITE_WS_BASE_URL point it at the
// Phase 4 backend -- see .env.example.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: { input: { main: "index.html", bandageTwin: "bandage-twin.html" } },
    // BandageTwinView is React.lazy()-loaded (App.tsx) specifically so its
    // three.js-heavy chunk splits out of the main entry and only downloads
    // when a session opens the 3D twin tab. That chunk is still >500kB on
    // its own account of three.js, which is expected, not a regression --
    // raise the warning limit instead of chasing an unavoidable number.
    chunkSizeWarningLimit: 800,
  },
  server: {
    port: 5173,
  },
});
