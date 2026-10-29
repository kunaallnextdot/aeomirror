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
        // Function form, matched on package path. The object form merged React
        // into the "charts" chunk (recharts depends on react), so every route —
        // the public homepage included — downloaded all of Recharts to get React.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          if (/node_modules\/(recharts|recharts-scale|d3-[^/]+|victory-vendor|internmap|decimal\.js-light|eventemitter3|lodash|react-smooth|fast-equals|tiny-invariant)\//.test(id)) return "charts";
          if (/node_modules\/lucide-react\//.test(id)) return "icons";
          return undefined;
        },
      },
    },
  },
});
