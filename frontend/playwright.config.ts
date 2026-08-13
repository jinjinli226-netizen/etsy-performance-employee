import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const frontendDir = fileURLToPath(new URL(".", import.meta.url));
const repositoryDir = fileURLToPath(new URL("..", import.meta.url));
const backendDir = fileURLToPath(new URL("../backend", import.meta.url));
const runId = randomUUID().replaceAll("-", "");
const dataDir = fileURLToPath(new URL(`../.e2e-data/run-${runId}`, import.meta.url));
const manifestPath = fileURLToPath(new URL("../.e2e-data/e2e-run-manifest.json", import.meta.url));
const backendPython = fileURLToPath(new URL("../backend/.venv/Scripts/python.exe", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  outputDir: "../test-results/playwright",
  use: {
    baseURL: "http://127.0.0.1:54173",
    trace: "retain-on-failure",
    acceptDownloads: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${backendPython}" -m tests.run_e2e_server`,
      cwd: backendDir,
      env: {
        ETSY_EMPLOYEE_DATA_DIR: dataDir,
        ETSY_EMPLOYEE_DATABASE_URL: `sqlite:///${dataDir.replaceAll("\\", "/")}/e2e.db`,
        ETSY_EMPLOYEE_TEST_MODE: "1",
      },
      url: "http://127.0.0.1:58765/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "pnpm exec vite --host 127.0.0.1 --port 54173 --strictPort",
      cwd: frontendDir,
      url: "http://127.0.0.1:54173/chat",
      reuseExistingServer: false,
      timeout: 30_000,
      env: { ETSY_E2E_BACKEND: "http://127.0.0.1:58765" },
    },
  ],
  metadata: { repositoryDir, dataDir, manifestPath },
});
