import { afterEach, describe, expect, it, vi } from "vitest";

import { excelApi } from "./excel";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("excel api event stream", () => {
  it("does not impose an absolute timeout on long-running jobs", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: 1\nevent: running\ndata: {"type":"running","status":"running"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));
    const timeoutSpy = vi.spyOn(window, "setTimeout");

    await excelApi.streamJob("00000000-0000-4000-8000-000000000001", {
      signal: new AbortController().signal,
      onEvent: () => undefined,
    });

    expect(timeoutSpy).not.toHaveBeenCalled();
  });
});

describe("excel api download", () => {
  it("accepts a generated workbook between 100 MB and the 200 MB upload limit", async () => {
    const zipSignature = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(zipSignature, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Length": String(150 * 1024 * 1024),
        "Content-Disposition": 'attachment; filename="result.xlsx"',
      },
    })));

    const result = await excelApi.downloadJob(
      "00000000-0000-4000-8000-000000000001",
      "source.xlsx",
    );

    expect(result.filename).toBe("result.xlsx");
    expect(result.blob.size).toBe(zipSignature.byteLength);
  });
});
