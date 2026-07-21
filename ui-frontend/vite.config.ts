import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Port the `graph-mem ui --port 8080` dev workflow listens on. The CLI
// defaults to port 0 (OS-assigned), so a dev proxying to a fixed port must
// start the backend with this port explicitly — see CONTRIBUTING.md.
const DEV_API_PORT = 8080;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Build straight into the Python package rather than into a dist/ that
    // someone then copies by hand. The copy step was how the shipped bundle
    // could drift from the source it claims to be built from.
    outDir: "../src/graph_mem/ui/frontend",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": `http://127.0.0.1:${DEV_API_PORT}`,
    },
  },
});
