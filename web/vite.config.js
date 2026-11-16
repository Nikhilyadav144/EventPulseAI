import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173 proxies /api and /checkin to the FastAPI backend on :8100.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8100", "/checkin": "http://127.0.0.1:8100" },
  },
  build: { outDir: "dist", assetsDir: "assets", emptyOutDir: true },
});
