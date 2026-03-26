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
        target: "http://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
      "/ws": {
        target: "ws://localhost:5005",
        ws: true,
        changeOrigin: true,
        secure: false,
      },
      "/static": {
        target: "http://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
      "/auth": {
        target: "http://localhost:5005",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
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
