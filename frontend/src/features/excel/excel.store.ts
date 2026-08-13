import { computed, reactive, readonly, ref } from "vue";

import { excelApi, type ExcelApi, type ExcelJob, type ExcelJobEvent, type ExcelJobStatus } from "../../api/excel";
import { HttpError, type HttpErrorCode } from "../../api/client";

const CURRENT_KEY = "etsy-excel-current-job";
const TERMINAL = new Set<ExcelJobStatus>(["completed", "failed", "cancelled"]);
const ACTIVE = new Set<ExcelJobStatus>(["queued", "running"]);
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

interface Monitor { controller: AbortController; token: symbol }
export interface ExcelStoreOptions { pollIntervalMs?: number; pollAttempts?: number }

const safeErrorCode = (error: unknown): HttpErrorCode => error instanceof HttpError ? error.code : "network";
const safeWarnings = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  let total = 0;
  for (const item of value) {
    if (typeof item !== "string" || item.length > 500 || /[\u0000-\u001f\u007f]/.test(item) || /(?:https?:\/\/|www\.)/i.test(item)) continue;
    const cleaned = item.trim();
    if (!cleaned || total + cleaned.length > 5_000) continue;
    result.push(cleaned);
    total += cleaned.length;
    if (result.length === 40) break;
  }
  return result;
};

export const validateExcelFile = (file: File) => {
  if (!file.name.toLowerCase().endsWith(".xlsx")) return "unsupported" as const;
  if (file.size <= 0) return "empty" as const;
  if (file.size > MAX_UPLOAD_BYTES) return "too_large" as const;
  return null;
};

export const createExcelStore = (api: ExcelApi = excelApi, options: ExcelStoreOptions = {}) => {
  const jobs = ref<ExcelJob[]>([]);
  const currentJobId = ref<string | null>(null);
  const warnings = reactive(new Map<string, string[]>());
  const lastEventIds = reactive(new Map<string, number>());
  const loading = ref(false);
  const loadingMore = ref(false);
  const uploading = ref(false);
  const cancelling = ref(false);
  const downloading = ref(false);
  const errorCode = ref<HttpErrorCode | "capacity" | "unsupported" | "empty" | "too_large" | null>(null);
  const disposed = ref(false);
  const monitors = new Map<string, Monitor>();
  const cancelLocks = new Set<string>();
  const localFiles = new Map<string, File>();
  let listController: AbortController | null = null;
  let loadToken: symbol | null = null;
  const total = ref(0);
  const pollIntervalMs = options.pollIntervalMs ?? 700;
  const pollAttempts = options.pollAttempts ?? 8;

  const currentJob = computed(() => jobs.value.find((job) => job.id === currentJobId.value) ?? null);
  const hasMore = computed(() => jobs.value.length < total.value);
  const currentWarnings = computed(() => currentJobId.value ? warnings.get(currentJobId.value) ?? [] : []);

  const persistSelection = (id: string | null) => {
    try {
      if (id) localStorage.setItem(CURRENT_KEY, id);
      else localStorage.removeItem(CURRENT_KEY);
    } catch { /* Persistence is optional; server history remains authoritative. */ }
  };

  const upsert = (incoming: ExcelJob) => {
    const receivedWarnings = safeWarnings(incoming.warnings);
    if (receivedWarnings.length) warnings.set(incoming.id, receivedWarnings);
    const index = jobs.value.findIndex((job) => job.id === incoming.id);
    if (index < 0) jobs.value = [incoming, ...jobs.value];
    else {
      const previous = jobs.value[index];
      jobs.value[index] = { ...incoming, progress_percent: Math.max(previous.progress_percent, incoming.progress_percent) };
    }
    return jobs.value.find((job) => job.id === incoming.id)!;
  };

  const delay = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { window.clearTimeout(timer); reject(signal.reason); }, { once: true });
  });

  const applyEvent = (job: ExcelJob, event: ExcelJobEvent, eventId: number, handle: Monitor) => {
    if (monitors.get(job.id)?.token !== handle.token || eventId <= (lastEventIds.get(job.id) ?? 0)) return;
    lastEventIds.set(job.id, eventId);
    if (typeof event.progress_percent === "number" && Number.isFinite(event.progress_percent)) {
      job.progress_percent = Math.max(job.progress_percent, Math.min(100, Math.max(0, Math.round(event.progress_percent))));
    }
    if (event.status && ["queued", "running"].includes(event.status)) job.status = event.status;
    const receivedWarnings = safeWarnings(event.warnings);
    if (receivedWarnings.length) warnings.set(job.id, [...new Set([...(warnings.get(job.id) ?? []), ...receivedWarnings])]);
  };

  const refreshTerminal = async (job: ExcelJob, handle: Monitor) => {
    for (let attempt = 0; attempt < pollAttempts && !handle.controller.signal.aborted; attempt += 1) {
      try {
        const received = await api.getJob(job.id, handle.controller.signal);
        if (monitors.get(job.id)?.token !== handle.token) return false;
        if (received.status === "completed" && !received.artifact) {
          if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, handle.controller.signal).catch(() => undefined);
          continue;
        }
        const updated = upsert(received);
        if (TERMINAL.has(updated.status)) {
          errorCode.value = null;
          return true;
        }
      } catch (error) {
        if (handle.controller.signal.aborted) return false;
        errorCode.value = safeErrorCode(error);
      }
      if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, handle.controller.signal).catch(() => undefined);
    }
    return false;
  };

  const poll = async (job: ExcelJob, handle: Monitor) => {
    for (let attempt = 0; attempt < pollAttempts && !handle.controller.signal.aborted; attempt += 1) {
      try {
        const received = await api.getJob(job.id, handle.controller.signal);
        if (monitors.get(job.id)?.token !== handle.token) return;
        if (received.status === "completed" && !received.artifact) {
          if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, handle.controller.signal).catch(() => undefined);
          continue;
        }
        const updated = upsert(received);
        if (TERMINAL.has(updated.status)) {
          errorCode.value = null;
          return;
        }
      } catch (error) {
        if (handle.controller.signal.aborted) return;
        errorCode.value = safeErrorCode(error);
      }
      if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, handle.controller.signal).catch(() => undefined);
    }
  };

  const monitor = async (job: ExcelJob) => {
    if (disposed.value || monitors.has(job.id)) return;
    const handle: Monitor = { controller: new AbortController(), token: Symbol("excel-monitor") };
    monitors.set(job.id, handle);
    let terminalSignal = false;
    for (let attempt = 0; attempt < 2 && !handle.controller.signal.aborted && !terminalSignal; attempt += 1) {
      try {
        await api.streamJob(job.id, {
          lastEventId: lastEventIds.get(job.id) || undefined,
          signal: handle.controller.signal,
          onEvent: (event, id) => {
            applyEvent(job, event, id, handle);
            terminalSignal = Boolean(event.status && TERMINAL.has(event.status));
          },
        });
        if (!terminalSignal) throw new HttpError("network", 0);
      } catch (error) {
        if (handle.controller.signal.aborted || monitors.get(job.id)?.token !== handle.token) return;
        errorCode.value = safeErrorCode(error);
      }
    }
    if (!handle.controller.signal.aborted && monitors.get(job.id)?.token === handle.token) {
      const refreshed = terminalSignal ? await refreshTerminal(job, handle) : false;
      if (!refreshed) await poll(job, handle);
    }
    if (monitors.get(job.id)?.token === handle.token) monitors.delete(job.id);
  };

  const beginMonitoring = () => {
    jobs.value.filter((job) => ACTIVE.has(job.status)).forEach((job) => void monitor(job));
    const selected = currentJob.value;
    if (selected && !ACTIVE.has(selected.status)) void monitor(selected);
  };

  const initialize = async () => {
    if (disposed.value) return;
    listController?.abort();
    const controller = new AbortController();
    listController = controller;
    const token = Symbol("excel-load");
    loadToken = token;
    loading.value = true;
    try {
      const page = await api.listJobs(controller.signal, 20, 0);
      if (disposed.value || loadToken !== token) return;
      jobs.value = page.items;
      page.items.forEach((job) => {
        const receivedWarnings = safeWarnings(job.warnings);
        if (receivedWarnings.length) warnings.set(job.id, receivedWarnings);
      });
      total.value = page.total;
      let preferred: string | null = null;
      try { preferred = localStorage.getItem(CURRENT_KEY); } catch { /* Ignore unavailable storage. */ }
      currentJobId.value = preferred && jobs.value.some((job) => job.id === preferred) ? preferred : jobs.value[0]?.id ?? null;
      persistSelection(currentJobId.value);
      errorCode.value = null;
      beginMonitoring();
    } catch (error) {
      if (!controller.signal.aborted) errorCode.value = safeErrorCode(error);
    } finally {
      if (loadToken === token) loading.value = false;
    }
  };

  const loadMore = async () => {
    if (!hasMore.value || loadingMore.value || disposed.value) return false;
    loadingMore.value = true;
    try {
      const page = await api.listJobs(undefined, 20, jobs.value.length);
      const known = new Set(jobs.value.map((job) => job.id));
      jobs.value = [...jobs.value, ...page.items.filter((job) => !known.has(job.id))];
      page.items.forEach((job) => {
        const receivedWarnings = safeWarnings(job.warnings);
        if (receivedWarnings.length) warnings.set(job.id, receivedWarnings);
      });
      total.value = page.total;
      beginMonitoring();
      return true;
    } catch (error) {
      errorCode.value = safeErrorCode(error);
      return false;
    } finally { loadingMore.value = false; }
  };

  const selectJob = async (id: string) => {
    if (!jobs.value.some((job) => job.id === id)) return;
    currentJobId.value = id;
    persistSelection(id);
    const job = jobs.value.find((item) => item.id === id)!;
    void monitor(job);
  };

  const upload = async (file: File) => {
    if (uploading.value || disposed.value) return false;
    const invalid = validateExcelFile(file);
    if (invalid) { errorCode.value = invalid; return false; }
    uploading.value = true;
    errorCode.value = null;
    try {
      const job = upsert(await api.createJob(file));
      total.value = Math.max(total.value + 1, jobs.value.length);
      localFiles.set(job.id, file);
      await selectJob(job.id);
      void monitor(job);
      return true;
    } catch (error) {
      errorCode.value = error instanceof HttpError && error.status === 507
        || Boolean(error && typeof error === "object" && "status" in error && error.status === 507)
        ? "capacity" : safeErrorCode(error);
      return false;
    } finally { uploading.value = false; }
  };

  const retryCurrent = async () => {
    const job = currentJob.value;
    const file = job ? localFiles.get(job.id) : undefined;
    if (!file || job?.status !== "failed") return false;
    return upload(file);
  };

  const cancelCurrent = async () => {
    const job = currentJob.value;
    if (!job || !ACTIVE.has(job.status) || cancelLocks.has(job.id)) return false;
    cancelLocks.add(job.id);
    cancelling.value = true;
    try {
      const updated = upsert(await api.cancelJob(job.id));
      monitors.get(job.id)?.controller.abort();
      monitors.delete(job.id);
      return updated.status === "cancelled";
    } catch (error) {
      errorCode.value = safeErrorCode(error);
      return false;
    } finally {
      cancelLocks.delete(job.id);
      cancelling.value = false;
    }
  };

  const downloadCurrent = async () => {
    const job = currentJob.value;
    if (!job || job.status !== "completed" || !job.artifact || downloading.value) return false;
    downloading.value = true;
    try {
      const result = await api.downloadJob(job.id, job.source_filename);
      const url = URL.createObjectURL(result.blob);
      try {
        const link = document.createElement("a");
        link.href = url;
        link.download = result.filename;
        link.rel = "noopener";
        link.click();
      } finally { URL.revokeObjectURL(url); }
      errorCode.value = null;
      return true;
    } catch (error) {
      errorCode.value = safeErrorCode(error);
      return false;
    } finally { downloading.value = false; }
  };

  const clearError = () => { errorCode.value = null; };
  const dispose = () => {
    disposed.value = true;
    listController?.abort();
    monitors.forEach((handle) => handle.controller.abort());
    monitors.clear();
    cancelLocks.clear();
  };

  return reactive({
    jobs, currentJobId, currentJob, currentWarnings, total, hasMore, loading, loadingMore, uploading, cancelling, downloading,
    errorCode, disposed: readonly(disposed), initialize, loadMore, selectJob, upload, retryCurrent, cancelCurrent, downloadCurrent, clearError, dispose,
    hasLocalFile: (id: string) => localFiles.has(id),
  });
};

export type ExcelStore = ReturnType<typeof createExcelStore>;
export const defaultExcelStore = createExcelStore();
