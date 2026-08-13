import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { FullConfig } from "@playwright/test";

export default async function globalSetup(config: FullConfig) {
  const runDirectory = String(config.metadata.dataDir);
  const manifestPath = String(config.metadata.manifestPath);
  const e2eRoot = fileURLToPath(new URL("../../.e2e-data/", import.meta.url));
  const expectedPrefix = e2eRoot.endsWith("\\") ? e2eRoot : `${e2eRoot}\\`;
  if (!runDirectory.startsWith(expectedPrefix) || !/run-[0-9a-f]{32}$/.test(runDirectory)) {
    throw new Error("Refusing to create an E2E manifest for an unowned directory");
  }
  await mkdir(e2eRoot, { recursive: true });
  await writeFile(manifestPath, JSON.stringify({ schemaVersion: 1, runDirectory }), { encoding: "utf8", flag: "wx" });
}
