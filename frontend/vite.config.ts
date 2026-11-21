/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// External APIs go through our server: bypasses client-side Wi-Fi limits, VPN/Private Relay blocks, and mobile CORS issues.
const proxy = {
  "/om/archive": { target: "https://archive-api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/archive/, "/v1/archive") },
  "/om/forecast": { target: "https://api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/forecast/, "/v1/forecast") },
  "/uldk": { target: "https://uldk.gugik.gov.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/uldk\/?/, "/") },
  "/uug": { target: "https://services.gugik.gov.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/uug\/?/, "/uug/") },
  "/iung": { target: "https://sgeo.iung.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/iung\/?/, "/geoserver/Susza/wms") },
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
