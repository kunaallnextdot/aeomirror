import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production build tuning: split large vendors into their own cached chunks and
// keep source maps out of the public bundle.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // Vitest (dev-only; does not affect `vite build`).
  test: { environment: "jsdom", globals: true },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          charts: ["recharts"],
          icons: ["lucide-react"],
        },
      },
    },
  },
});
