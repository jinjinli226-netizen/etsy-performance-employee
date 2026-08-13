const backend = "http://127.0.0.1:58765";

export default async function globalTeardown() {
  const response = await fetch(`${backend}/__e2e__/shutdown`, { method: "POST" });
  if (!response.ok) throw new Error(`E2E backend refused graceful shutdown: ${response.status}`);

  for (let attempt = 0; attempt < 100; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    try {
      await fetch(`${backend}/api/health`);
    } catch {
      return;
    }
  }
  throw new Error("E2E backend did not stop within five seconds");
}
