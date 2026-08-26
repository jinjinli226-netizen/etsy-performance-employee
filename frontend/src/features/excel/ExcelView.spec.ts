import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { excelApi, safeDownloadFilename, type ExcelApi, type ExcelJob, type ExcelJobEvent } from "../../api/excel";
import ExcelView from "../../views/ExcelView.vue";
import { createExcelStore, validateExcelFile } from "./excel.store";

const now = "2026-08-13T08:00:00Z";
const makeJob = (status: ExcelJob["status"] = "queued", overrides: Partial<ExcelJob> = {}): ExcelJob => ({
  id: "11111111-1111-4111-8111-111111111111",
  source_filename: "表演服上新.xlsx",
  source_sha256: "a".repeat(64),
  source_size_bytes: 2_048,
  status,
  progress_percent: status === "completed" ? 100 : 0,
  error: status === "failed" ? { code: "worker_failed", message: "The worker failed at C:\\secret" } : null,
  created_at: now,
  updated_at: now,
  artifact: status === "completed" ? {
    id: 1,
    kind: "excel_output",
    filename: "表演服上新_listing.xlsx",
    sha256: "b".repeat(64),
    size_bytes: 4_096,
    created_at: now,
  } : null,
  warnings: [],
  ...overrides,
});

class FakeExcelApi implements ExcelApi {
  jobs: ExcelJob[] = [];
  events = new Map<string, Array<{ id: number; event: ExcelJobEvent }>>();
  uploads: File[] = [];
  cancels: string[] = [];
  downloads: string[] = [];
  streamCalls = new Map<string, number[]>();
  streamFailures = new Map<string, number>();
  listCalls = 0;
  getCalls = 0;
  downloadResult = { blob: new Blob(["xlsx"]), filename: "表演服上新_listing.xlsx" };
  uploadGate?: Promise<void>;

  async createJob(file: File, _signal?: AbortSignal) {
    await this.uploadGate;
    this.uploads.push(file);
    const job = makeJob("queued", { id: `11111111-1111-4111-8111-${String(this.uploads.length).padStart(12, "0")}`, source_filename: file.name });
    this.jobs.unshift(job);
    return job;
  }

  async listJobs(_signal?: AbortSignal, limit = 20, offset = 0) {
    this.listCalls += 1;
    return { items: this.jobs.slice(offset, offset + limit), total: this.jobs.length, limit, offset };
  }

  async getJob(id: string) {
    this.getCalls += 1;
    const job = this.jobs.find((item) => item.id === id);
    if (!job) throw new Error("missing");
    return job;
  }

  async streamJob(id: string, options: { lastEventId?: number; onEvent: (event: ExcelJobEvent, id: number) => void; signal: AbortSignal }) {
    const calls = this.streamCalls.get(id) ?? [];
    calls.push(options.lastEventId ?? 0);
    this.streamCalls.set(id, calls);
    const remainingFailures = this.streamFailures.get(id) ?? 0;
    const rows = this.events.get(id) ?? [];
    if (remainingFailures > 0) {
      const first = rows.find((row) => row.id > (options.lastEventId ?? 0));
      if (first) options.onEvent(first.event, first.id);
      this.streamFailures.set(id, remainingFailures - 1);
      throw new TypeError("offline");
    }
    rows.filter((row) => row.id > (options.lastEventId ?? 0)).forEach((row) => options.onEvent(row.event, row.id));
  }

  async cancelJob(id: string) {
    this.cancels.push(id);
    const job = this.jobs.find((item) => item.id === id)!;
    Object.assign(job, { status: "cancelled", error: null, updated_at: now });
    return job;
  }

  async downloadJob(id: string, sourceFilename: string) {
    this.downloads.push(`${id}:${sourceFilename}`);
    return this.downloadResult;
  }
}

const wrappers: VueWrapper[] = [];
const render = async (api = new FakeExcelApi()) => {
  const store = createExcelStore(api, { pollIntervalMs: 1, pollAttempts: 2 });
  const wrapper = mount(ExcelView, { props: { store }, attachTo: document.body });
  wrappers.push(wrapper);
  await flushPromises();
  return { api, store, wrapper };
};

const chooseFile = async (wrapper: VueWrapper, file: File) => {
  const input = wrapper.get<HTMLInputElement>('[data-testid="excel-file-input"]');
  Object.defineProperty(input.element, "files", { value: [file], configurable: true });
  await input.trigger("change");
  await flushPromises();
};

const sizedFile = (size: number, name = "products.xlsx") => {
  const file = new File(["x"], name, { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  Object.defineProperty(file, "size", { value: size, configurable: true });
  return file;
};

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:download"),
    revokeObjectURL: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Excel automation workspace", () => {
  it("accepts the 50.68 MiB production workbook and caps uploads at 200 MiB", () => {
    expect(validateExcelFile(sizedFile(53_142_485))).toBeNull();
    expect(validateExcelFile(sizedFile(200 * 1024 * 1024))).toBeNull();
    expect(validateExcelFile(sizedFile(200 * 1024 * 1024 + 1))).toBe("too_large");
  });

  it("accepts only a real-sized .xlsx selection and never asks for header configuration", async () => {
    const { wrapper, api } = await render();
    expect(wrapper.text()).toContain("员工会自动识别表头");
    expect(wrapper.text()).toContain("最大 200 MB");
    expect(wrapper.text()).not.toMatch(/配置表头|选择列|映射字段/);
    expect(wrapper.get('[data-testid="excel-file-input"]').attributes("accept")).toBe(".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");

    for (const file of [
      new File(["x"], "bad.xlsm", { type: "application/vnd.ms-excel.sheet.macroEnabled.12" }),
      new File(["x"], "bad.xls"),
      new File(["x"], "bad.csv", { type: "text/csv" }),
      sizedFile(200 * 1024 * 1024 + 1, "large.xlsx"),
    ]) {
      await chooseFile(wrapper, file);
      expect(api.uploads).toHaveLength(0);
    }
    expect(wrapper.text()).toContain("仅支持 .xlsx");
  });

  it("supports keyboard upload and starts exactly one job while acceptance is pending", async () => {
    const api = new FakeExcelApi();
    let release!: () => void;
    api.uploadGate = new Promise<void>((resolve) => { release = resolve; });
    const { wrapper } = await render(api);
    const picker = wrapper.get('[data-testid="excel-dropzone"]');
    await picker.trigger("keydown", { key: "Enter" });
    const file = new File(["PK"], "costume.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const input = wrapper.get<HTMLInputElement>('[data-testid="excel-file-input"]');
    Object.defineProperty(input.element, "files", { value: [file], configurable: true });
    await input.trigger("change");
    await input.trigger("change");
    await nextTick();
    expect(wrapper.get('[data-testid="excel-upload-button"]').attributes("disabled")).toBeDefined();
    release();
    await flushPromises();
    expect(api.uploads).toHaveLength(1);
  });

  it("keeps a visible new-task entry above history and selects the uploaded job", async () => {
    const api = new FakeExcelApi();
    api.jobs = [makeJob("completed", { source_filename: "old-costume.xlsx" })];
    const { wrapper, store } = await render(api);

    expect(wrapper.get('[data-testid="new-excel-job"]').text()).toContain("新建 Listing 任务");
    expect(wrapper.text()).not.toContain("上传另一个工作簿");

    await chooseFile(wrapper, new File(["PK"], "new-costume.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }));

    expect(api.uploads).toHaveLength(1);
    expect(store.currentJob?.source_filename).toBe("new-costume.xlsx");
    expect(wrapper.text()).toContain("new-costume.xlsx");
  });

  it("disables the visible new-task entry while a workbook is being accepted", async () => {
    const api = new FakeExcelApi();
    api.jobs = [makeJob("running")];
    let release!: () => void;
    api.uploadGate = new Promise<void>((resolve) => { release = resolve; });
    const { wrapper } = await render(api);
    const input = wrapper.get<HTMLInputElement>('[data-testid="excel-file-input"]');
    Object.defineProperty(input.element, "files", {
      value: [new File(["PK"], "next.xlsx")],
      configurable: true,
    });

    await input.trigger("change");
    await nextTick();

    const entry = wrapper.get('[data-testid="new-excel-job"]');
    expect(entry.attributes("disabled")).toBeDefined();
    expect(entry.text()).toContain("正在创建任务");

    release();
    await flushPromises();
  });

  it.each([
    ["queued", "排队中"],
    ["running", "生成中"],
    ["needs_review", "待复核（历史任务）"],
    ["completed", "已完成"],
    ["failed", "生成失败"],
    ["cancelled", "已取消"],
  ] as const)("renders the %s state with public copy", async (status, copy) => {
    const api = new FakeExcelApi();
    api.jobs = [makeJob(status)];
    const { wrapper } = await render(api);
    expect(wrapper.text()).toContain(copy);
    expect(wrapper.text()).not.toContain("C:\\secret");
  });

  it("reloads persisted work, paginates history, and keeps the store across route remounts", async () => {
    const api = new FakeExcelApi();
    api.jobs = Array.from({ length: 22 }, (_, index) => makeJob(index === 0 ? "running" : "completed", {
      id: `11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`,
      source_filename: `costume-${index + 1}.xlsx`,
    }));
    localStorage.setItem("etsy-excel-current-job", api.jobs[1].id);
    const store = createExcelStore(api, { pollIntervalMs: 1, pollAttempts: 1 });
    const first = mount(ExcelView, { props: { store } });
    await flushPromises();
    expect(store.currentJob?.id).toBe(api.jobs[1].id);
    await first.get('[data-testid="load-more-jobs"]').trigger("click");
    await flushPromises();
    expect(store.jobs).toHaveLength(22);
    first.unmount();
    const second = mount(ExcelView, { props: { store } });
    wrappers.push(second);
    await flushPromises();
    expect(second.text()).toContain("costume-2.xlsx");
    expect(api.listCalls).toBe(2);
  });

  it("deduplicates progress, reconnects once with Last-Event-ID, then falls back to polling", async () => {
    const api = new FakeExcelApi();
    const job = makeJob("running");
    api.jobs = [job];
    api.streamFailures.set(job.id, 2);
    api.events.set(job.id, [
      { id: 7, event: { type: "worker_row_started", status: "running", progress_percent: 20, row_id: "internal-a", row_number: 8 } },
      { id: 7, event: { type: "worker_row_completed", status: "running", progress_percent: 10, row_id: "internal-a", row_number: 8 } },
      { id: 8, event: { type: "worker_row_completed", status: "running", progress_percent: 35, row_id: "internal-a", row_number: 8, warnings: ["尺码信息较少"] } },
    ]);
    const { store, wrapper } = await render(api);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flushPromises();
    expect(api.streamCalls.get(job.id)).toEqual([0, 7]);
    expect(store.currentJob?.progress_percent).toBeGreaterThanOrEqual(20);
    expect(wrapper.text()).not.toContain("internal-a");
    expect(api.getCalls).toBeGreaterThan(0);
  });

  it("shows a deduplicated missing-image skip count without exposing row identity", async () => {
    const api = new FakeExcelApi();
    const job = makeJob("running");
    api.jobs = [job];
    api.events.set(job.id, [
      {
        id: 2,
        event: {
          type: "worker_row_skipped",
          status: "running",
          progress_percent: 2,
          row_id: "internal-row-6",
          row_number: 6,
          skip_reason: "missing_product_image",
          message: "已跳过：缺少商品图片",
          warnings: ["已跳过：缺少商品图片"],
        },
      },
    ]);

    const { store, wrapper } = await render(api);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flushPromises();

    expect(store.currentSkippedCount).toBe(1);
    expect(wrapper.text()).toContain("已跳过：缺少商品图片");
    expect(wrapper.text()).toContain("已跳过 1 行");
    expect(wrapper.text()).not.toContain("internal-row-6");
  });

  it("monitors multiple active jobs without stale controllers and cancels idempotently", async () => {
    const api = new FakeExcelApi();
    const first = makeJob("running");
    const second = makeJob("queued", { id: "22222222-2222-4222-8222-222222222222", source_filename: "second.xlsx" });
    api.jobs = [first, second];
    api.events.set(first.id, [{ id: 2, event: { type: "completed", status: "completed", progress_percent: 100 } }]);
    api.events.set(second.id, [{ id: 3, event: { type: "running", status: "running", progress_percent: 1 } }]);
    const { store } = await render(api);
    await flushPromises();
    expect(api.streamCalls.has(first.id)).toBe(true);
    expect(api.streamCalls.has(second.id)).toBe(true);
    await store.selectJob(second.id);
    await Promise.all([store.cancelCurrent(), store.cancelCurrent()]);
    expect(api.cancels).toEqual([second.id]);
  });

  it("shows completed warnings safely and exposes exactly one guarded primary download", async () => {
    const api = new FakeExcelApi();
    const job = makeJob("completed");
    api.jobs = [job];
    api.events.set(job.id, [{ id: 3, event: { type: "completed", status: "completed", progress_percent: 100, warnings: ["中文标题信息较少", { internal: "hide" }] } }]);
    const { wrapper } = await render(api);
    await flushPromises();
    expect(wrapper.findAll('[data-testid="download-result"]')).toHaveLength(1);
    const download = wrapper.get('[data-testid="download-result"]');
    await Promise.all([download.trigger("click"), download.trigger("click")]);
    await flushPromises();
    expect(api.downloads).toHaveLength(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download");
    expect(wrapper.text()).not.toContain("sha256");
    expect(wrapper.text()).not.toContain(job.source_sha256);
  });

  it("bounds and deduplicates warnings across many replayed events", async () => {
    const api = new FakeExcelApi();
    const job = makeJob("running");
    api.jobs = [job];
    const events = Array.from({ length: 60 }, (_, index) => ({
      id: index + 1,
      event: { type: "worker_row_completed", status: "running", progress_percent: index, warnings: [`warning-${index}`, "same-warning"] },
    } satisfies { id: number; event: ExcelJobEvent }));
    api.streamJob = async (_id, options) => {
      events.forEach(({ id, event }) => options.onEvent(event, id));
      await new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }));
    };
    const { store } = await render(api);
    await flushPromises();
    expect(store.currentWarnings).toHaveLength(40);
    expect(store.currentWarnings.filter((warning) => warning === "same-warning")).toHaveLength(1);
    expect(store.currentWarnings.join("").length).toBeLessThanOrEqual(5_000);
  });

  it("refreshes authoritative detail after terminal SSE before exposing an artifact", async () => {
    const api = new FakeExcelApi();
    const running = makeJob("running", { artifact: null });
    api.jobs = [running];
    api.events.set(running.id, [{ id: 4, event: { type: "completed", status: "completed", progress_percent: 100 } }]);
    api.getJob = async () => makeJob("completed", { id: running.id, warnings: ["Confirm measurements"] });
    const { wrapper } = await render(api);
    await flushPromises();
    expect(wrapper.findAll('[data-testid="download-result"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("Confirm measurements");
  });

  it("replaces streamed warnings with an authoritative empty terminal detail", async () => {
    const api = new FakeExcelApi();
    const running = makeJob("running", { artifact: null });
    api.jobs = [running];
    api.events.set(running.id, [
      { id: 3, event: { type: "worker_row_completed", status: "running", progress_percent: 50, warnings: ["streamed warning"] } },
      { id: 4, event: { type: "completed", status: "completed", progress_percent: 100 } },
    ]);
    api.getJob = async () => makeJob("completed", { id: running.id, warnings: [] });
    const { store, wrapper } = await render(api);
    await flushPromises();
    expect(store.currentWarnings).toEqual([]);
    expect(wrapper.text()).not.toContain("streamed warning");
  });

  it("retries a transient terminal refresh and clears the recovered connection error", async () => {
    const api = new FakeExcelApi();
    const running = makeJob("running", { artifact: null });
    api.jobs = [running];
    api.events.set(running.id, [{ id: 4, event: { type: "completed", status: "completed", progress_percent: 100 } }]);
    let calls = 0;
    api.getJob = async () => {
      calls += 1;
      if (calls === 1) throw new TypeError("temporary disconnect");
      return makeJob("completed", { id: running.id });
    };
    const { wrapper, store } = await render(api);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flushPromises();
    expect(calls).toBe(2);
    expect(store.errorCode).toBeNull();
    expect(wrapper.findAll('[data-testid="download-result"]')).toHaveLength(1);
  });

  it("focuses a visible safe alert after an upload error and announces progress meaningfully", async () => {
    const api = new FakeExcelApi();
    api.createJob = async () => { throw Object.assign(new Error("private"), { code: "server_error", status: 507 }); };
    const { wrapper } = await render(api);
    await chooseFile(wrapper, new File(["PK"], "capacity.xlsx"));
    const alert = wrapper.get<HTMLElement>('[role="alert"]');
    expect(document.activeElement).toBe(alert.element);
    expect(wrapper.get('[aria-live="polite"]').text()).not.toContain("private");
  });

  it("offers a real same-session retry and asks for a file again after refresh", async () => {
    const api = new FakeExcelApi();
    const { wrapper, store } = await render(api);
    await chooseFile(wrapper, new File(["PK"], "retry.xlsx"));
    const failed = store.currentJob!;
    failed.status = "failed";
    await nextTick();
    expect(wrapper.text()).toContain("使用原文件重试");
    await wrapper.get('[data-testid="retry-job"]').trigger("click");
    await flushPromises();
    expect(api.uploads).toHaveLength(2);
    expect(store.retainedFileCount).toBe(1);

    const refreshedApi = new FakeExcelApi();
    refreshedApi.jobs = [makeJob("failed")];
    const refreshed = await render(refreshedApi);
    expect(refreshed.wrapper.text()).toContain("重新选择文件");
    expect(refreshedApi.uploads).toHaveLength(0);
    const pickerSpy = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
    await refreshed.wrapper.get('[data-testid="reselect-job"]').trigger("click");
    await nextTick();
    expect(refreshed.wrapper.get('[data-testid="new-excel-job"]').text()).toContain("新建 Listing 任务");
    expect(pickerSpy).toHaveBeenCalledOnce();
  });

  it("releases large local files for completed and cancelled jobs and on dispose", async () => {
    const api = new FakeExcelApi();
    const { wrapper, store } = await render(api);
    await chooseFile(wrapper, new File(["PK"], "memory.xlsx"));
    expect(store.retainedFileCount).toBe(1);
    await store.cancelCurrent();
    await flushPromises();
    expect(store.retainedFileCount).toBe(0);

    const completedApi = new FakeExcelApi();
    const completedId = "11111111-1111-4111-8111-000000000001";
    completedApi.events.set(completedId, [{ id: 10, event: { type: "completed", status: "completed", progress_percent: 100 } }]);
    completedApi.getJob = async () => makeJob("completed", { id: completedId });
    const completed = await render(completedApi);
    await chooseFile(completed.wrapper, new File(["PK"], "completed-memory.xlsx"));
    await flushPromises();
    expect(completed.store.retainedFileCount).toBe(0);
    store.dispose();
    expect(store.retainedFileCount).toBe(0);
  });

  it("maps capacity failures to safe workspace copy and disposes all monitoring", async () => {
    const api = new FakeExcelApi();
    api.createJob = async () => { throw Object.assign(new Error("C:\\private\\db"), { code: "server_error", status: 507 }); };
    const { wrapper, store } = await render(api);
    await chooseFile(wrapper, new File(["PK"], "capacity.xlsx"));
    expect(wrapper.text()).toContain("知识库容量");
    expect(wrapper.text()).not.toContain("private");
    store.dispose();
    expect(store.disposed).toBe(true);
  });

  it("aborts an owned pending upload on dispose without retaining the file or result", async () => {
    const api = new FakeExcelApi();
    let observedSignal: AbortSignal | undefined;
    api.createJob = (_file, signal) => {
      observedSignal = signal;
      return new Promise((_resolve, reject) => {
        if (!signal) { reject(new Error("missing signal")); return; }
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    };
    const store = createExcelStore(api);
    const pending = store.upload(new File(["PK"], "pending.xlsx"));
    await nextTick();
    store.dispose();
    await expect(pending).resolves.toBe(false);
    expect(observedSignal?.aborted).toBe(true);
    expect(store.jobs).toHaveLength(0);
    expect(store.retainedFileCount).toBe(0);
    expect(store.errorCode).toBeNull();
  });
});

describe("Excel API safety", () => {
  it("uses a safe Content-Disposition filename or a sanitized Chinese fallback", () => {
    expect(safeDownloadFilename("attachment; filename*=UTF-8''%E8%A1%A8%E6%BC%94%E6%9C%8D_listing.xlsx", "source.xlsx")).toBe("表演服_listing.xlsx");
    expect(safeDownloadFilename('attachment; filename="../../evil.exe"', "中文:标题.xlsx")).toBe("中文_标题_listing.xlsx");
  });

  it("sends Last-Event-ID through the real SSE API and hides malformed events", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: 9\nevent: progress\ndata: {"type":"worker_row_completed","status":"running","progress_percent":40}\n\nid: 10\nevent: internal\ndata: {"path":"C:/secret"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Last-Event-ID")).toBe("8");
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }));
    const received: ExcelJobEvent[] = [];
    await excelApi.streamJob("11111111-1111-4111-8111-111111111111", {
      lastEventId: 8,
      signal: new AbortController().signal,
      onEvent: (event) => received.push(event),
    });
    expect(received).toEqual([
      { type: "worker_row_completed", status: "running", progress_percent: 40 },
    ]);
  });

  it("downloads a blob once and maps a 409 without exposing the response body", async () => {
    const validZip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1]);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(validZip, { status: 200, headers: { "Content-Disposition": 'attachment; filename="listing.xlsx"', "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }))
      .mockResolvedValueOnce(new Response('{"detail":"C:/private/path"}', { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(excelApi.downloadJob("11111111-1111-4111-8111-111111111111", "source.xlsx")).resolves.toMatchObject({ filename: "listing.xlsx" });
    await expect(excelApi.downloadJob("11111111-1111-4111-8111-111111111111", "source.xlsx")).rejects.toMatchObject({ code: "conflict", status: 409 });
  });

  it("rejects empty, oversized, non-ZIP, and unsafe media downloads", async () => {
    for (const response of [
      new Response(new Blob([]), { status: 200, headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }),
      new Response(new TextEncoder().encode("not zip"), { status: 200, headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }),
      new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]), { status: 200, headers: { "Content-Type": "text/html" } }),
      new Response(null, { status: 200, headers: { "Content-Length": String(100 * 1024 * 1024 + 1), "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }),
    ]) {
      vi.stubGlobal("fetch", vi.fn(async () => response));
      await expect(excelApi.downloadJob("11111111-1111-4111-8111-111111111111", "source.xlsx")).rejects.toMatchObject({ code: "server_error" });
    }
  });
});
