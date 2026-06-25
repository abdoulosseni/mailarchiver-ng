import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // En dev local (npm run dev), proxy des appels API vers l'API FastAPI.
    proxy: {
      '/auth': 'http://localhost:8000',
      '/search': 'http://localhost:8000',
      '/messages': 'http://localhost:8000',
      '/import': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
