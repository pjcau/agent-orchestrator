import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3001,
    proxy: {
      "/api": {
        target: "https://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
      "/ws": {
        target: "wss://localhost:5005",
        ws: true,
        changeOrigin: true,
        secure: false,
      },
      "/static": {
        target: "https://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
      "/auth": {
        target: "https://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Playwright tests live in e2e/ and run separately via `npm run e2e`.
    exclude: ["node_modules", "dist", ".idea", ".git", ".cache", "e2e/**"],
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom"],
          query: ["@tanstack/react-query"],
          flow: ["@xyflow/react"],
          markdown: ["react-markdown", "remark-gfm", "rehype-katex", "remark-math"],
        },
      },
    },
  },
});
