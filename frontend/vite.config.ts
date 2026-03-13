import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Proxy API calls to Go gateway in local dev
      "/api": {
        target:      "http://localhost:8080",
        changeOrigin: true,
        rewrite:     (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
