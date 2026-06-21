import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Vite dev-server configuration for Mighty Jarvis MKII
 *
 * Proxy rules forward all /api and /ws traffic to the FastAPI backend.
 *
 * Backend port:
 *   - Docker Compose  → port 80   (nginx in-container, same host)
 *   - Direct uvicorn  → port 8000 (default main.py / uvicorn --port 8000)
 *   - Override via env: VITE_BACKEND_PORT=8001 npm run dev
 *
 * The original vite.config.js had port 8001 which matched neither default.
 * Changed to 8000 (direct uvicorn). Set VITE_BACKEND_PORT=80 when running
 * via Docker Compose and accessing the Vite dev-server from outside the container.
 */

const BACKEND_PORT = process.env.VITE_BACKEND_PORT || "8000";
const BACKEND_HTTP = `http://localhost:${BACKEND_PORT}`;
const BACKEND_WS   = `ws://localhost:${BACKEND_PORT}`;

export default defineConfig({
  plugins: [react()],

  server: {
    host:        '0.0.0.0',
    port:        5173,
    strictPort:  true,

    proxy: {
      // ── REST API ──────────────────────────────────────────────────────────
      '/api': {
        target:       BACKEND_HTTP,
        changeOrigin: true,
        secure:       false,
        // Increase timeout for long-running SSE streams (evolve, RCA, chat)
        proxyTimeout: 300_000,   // 5 minutes
        timeout:      300_000,
      },

      // ── WebSocket ─────────────────────────────────────────────────────────
      '/ws': {
        target:       BACKEND_WS,
        ws:           true,
        changeOrigin: true,
        secure:       false,
      },
    },
  },

  build: {
    // Raise chunk size warning from 500KB → 2MB (the HUD theme + icons are large)
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        // Split vendor code so the main bundle stays small
        manualChunks: {
          react:  ['react', 'react-dom'],
        },
      },
    },
  },

  // Surface the backend URL to the app at build-time if needed
  // (App.tsx reads import.meta.env.VITE_API_URL)
  // Example: VITE_API_URL=https://jarvis.example.com npm run build
})