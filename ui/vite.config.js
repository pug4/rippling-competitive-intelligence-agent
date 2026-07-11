import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxies /api to the local FastAPI server so the UI consumes the same
// validated JSON packages the CLI renders (§40.6).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
