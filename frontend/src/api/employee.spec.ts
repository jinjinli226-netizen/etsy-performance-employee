import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchEmployeeStatus } from "./employee";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("employee status api", () => {
  it("returns a validated status for a legal payload", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "online" }));

    await expect(fetchEmployeeStatus()).resolves.toEqual({ status: "online" });
  });

  it("rejects an illegal status value", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "standby" }));

    await expect(fetchEmployeeStatus()).rejects.toThrow();
  });

  it("rejects a non-object payload", async () => {
    fetchMock.mockResolvedValue(jsonResponse("offline"));

    await expect(fetchEmployeeStatus()).rejects.toThrow();
  });

  it("rejects a failed HTTP response", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 503 }));

    await expect(fetchEmployeeStatus()).rejects.toThrow();
  });
});
