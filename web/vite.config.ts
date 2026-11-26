/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, /api goes to the local API (uv run uvicorn book_agent.main:app --port 8000).
// In production, VITE_API_BASE_URL points the client at the deployed API.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // An absolute base URL: Node's Request can't parse "/api".
    env: { VITE_API_BASE_URL: "http://api.test" },
  },
});
