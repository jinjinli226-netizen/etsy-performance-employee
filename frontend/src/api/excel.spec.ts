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
