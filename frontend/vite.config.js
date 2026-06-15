import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // FIX: proxy /api/ HTTP requests to backend
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // FIX: proxy /ws/ WebSocket connections to backend in dev mode
      '/ws': {
        target: 'ws://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
