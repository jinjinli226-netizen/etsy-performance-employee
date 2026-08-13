import { configDefaults, defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

const apiTarget = process.env.ETSY_E2E_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  preview: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  test: {
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
