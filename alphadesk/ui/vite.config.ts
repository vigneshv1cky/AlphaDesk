import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // built bundle is served by FastAPI (alphadesk/app/dashboard.py)
    outDir: path.resolve(__dirname, "../app/static"),
    emptyOutDir: true,
  },
  server: {
    // local dev: proxy API to the running engine
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
})
