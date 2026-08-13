const backend = "http://127.0.0.1:58765";
import { fileURLToPath } from "node:url";
import { chmod, readFile, rm, unlink } from "node:fs/promises";

const e2eRoot = fileURLToPath(new URL("../../.e2e-data/", import.meta.url));
const manifestPath = fileURLToPath(new URL("../../.e2e-data/e2e-run-manifest.json", import.meta.url));

async function removeReadonly(path: string) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await chmod(path, 0o666).catch(() => undefined);
    try {
      await rm(path, { recursive: true, force: true });
      return;
    } catch (error) {
      if (!error || typeof error !== "object" || !("code" in error) || !["EBUSY", "EPERM"].includes(String(error.code))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error("Owned E2E run directory remained locked after five seconds");
}

export default async function globalTeardown() {
  const response = await fetch(`${backend}/__e2e__/shutdown`, { method: "POST" });
  if (!response.ok) throw new Error(`E2E backend refused graceful shutdown: ${response.status}`);

  for (let attempt = 0; attempt < 100; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    try {
      await fetch(`${backend}/api/health`);
    } catch {
      const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as { schemaVersion: number; runDirectory: string };
      const runUrl = new URL(`file:///${manifest.runDirectory.replaceAll("\\", "/")}`);
      const runDirectory = fileURLToPath(runUrl);
      const expectedPrefix = e2eRoot.endsWith("\\") ? e2eRoot : `${e2eRoot}\\`;
      if (manifest.schemaVersion !== 1 || !runDirectory.startsWith(expectedPrefix) || !/run-[0-9a-f]{32}$/.test(runDirectory)) {
        throw new Error("E2E cleanup manifest did not name an owned unique run directory");
      }
      await removeReadonly(runDirectory);
      await unlink(manifestPath);
      return;
    }
  }
  throw new Error("E2E backend did not stop within five seconds");
}
