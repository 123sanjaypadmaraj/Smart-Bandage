import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Phase 5 dashboard. VITE_API_BASE_URL / VITE_WS_BASE_URL point it at the
// Phase 4 backend -- see .env.example.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
  },
});
