import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // API_PROXY: where the backend runs (docker compose points it at the backend service)
    proxy: { '/api': process.env.API_PROXY ?? 'http://localhost:8000' },
  },
})
