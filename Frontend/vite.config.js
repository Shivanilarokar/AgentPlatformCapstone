import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API and the UI are separate containers, so the dev server proxies the
// API paths through to FastAPI. That keeps the browser on one origin, which
// means no CORS setup and the auth cookie just works.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,           // listen on 0.0.0.0 so Docker can publish the port
    port: 5173,
    watch: { usePolling: true },   // file events do not cross the Windows mount
    proxy: {
      "/auth": { target: process.env.API_URL || "http://localhost:8000", changeOrigin: true },
      "/v1":   { target: process.env.API_URL || "http://localhost:8000", changeOrigin: true },
      "/health": { target: process.env.API_URL || "http://localhost:8000", changeOrigin: true },
    },
  },
});
