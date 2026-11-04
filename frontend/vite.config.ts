import { defineConfig } from "vite";
export default defineConfig({
  root: "frontend",
  build: { outDir: "../src/indexweave/web", emptyOutDir: true },
  esbuild: { jsx: "automatic" },
});
