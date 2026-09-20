import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dashboard is a static SPA that polls the Flask backend's /api/* JSON
// endpoints directly (CORS is enabled there). Set VITE_API_BASE_URL at
// build time to point at the bot server, e.g. http://13.61.172.107:5000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
