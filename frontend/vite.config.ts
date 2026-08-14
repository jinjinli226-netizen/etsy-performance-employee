import { configDefaults, defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

const productionPort = /^(?:[1-9][0-9]{3,4})$/.test(process.env.ETSY_EMPLOYEE_BACKEND_PORT ?? "")
  ? process.env.ETSY_EMPLOYEE_BACKEND_PORT
  : "8765";
const apiTarget = process.env.ETSY_E2E_TEST_MODE === "1"
  ? process.env.ETSY_E2E_BACKEND ?? "http://127.0.0.1:8765"
  : `http://127.0.0.1:${productionPort}`;

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  preview: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  test: {
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
