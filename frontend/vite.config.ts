/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Weather goes through our server: the user's network (e.g. a shared hackathon Wi-Fi) no longer meets the Open-Meteo per-address limit.
const proxy = {
  "/om/archive": { target: "https://archive-api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/archive/, "/v1/archive") },
  "/om/forecast": { target: "https://api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/forecast/, "/v1/forecast") },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true, proxy },
  preview: { port: 4173, host: true, proxy },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 900,
    // MapLibre changes rarely: its own chunk stays in the browser cache across app releases.
    rollupOptions: { output: { manualChunks: { maplibre: ["maplibre-gl"], react: ["react", "react-dom", "react-router-dom"] } } },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
