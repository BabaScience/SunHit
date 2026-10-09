import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The api has no CORS in Phase 0: the dev server proxies /v1 to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": process.env.API_URL ?? "http://localhost:3000" },
  },
});
