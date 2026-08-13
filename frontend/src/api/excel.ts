import { API_BASE, HttpError, apiRequest, openEventStream } from "./client";

export type ExcelJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface ExcelArtifact {
  id: number;
  kind: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
}

export interface ExcelJob {
  id: string;
  source_filename: string;
  source_sha256: string;
  source_size_bytes: number;
  status: ExcelJobStatus;
  progress_percent: number;
  warnings: string[];
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
  artifact: ExcelArtifact | null;
}

export interface ExcelJobPage {
  items: ExcelJob[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExcelJobEvent {
  type: string;
  status?: ExcelJobStatus;
  progress_percent?: number;
  row_id?: string;
  row_number?: number;
  warnings?: unknown;
  error?: unknown;
}

export interface ExcelDownload {
  blob: Blob;
  filename: string;
}

export interface ExcelApi {
  createJob(file: File, signal?: AbortSignal): Promise<ExcelJob>;
  listJobs(signal?: AbortSignal, limit?: number, offset?: number): Promise<ExcelJobPage>;
  getJob(id: string, signal?: AbortSignal): Promise<ExcelJob>;
  streamJob(id: string, options: { lastEventId?: number; onEvent: (event: ExcelJobEvent, id: number) => void; signal: AbortSignal }): Promise<void>;
  cancelJob(id: string, signal?: AbortSignal): Promise<ExcelJob>;
  downloadJob(id: string, sourceFilename: string, signal?: AbortSignal): Promise<ExcelDownload>;
}

const safeJobId = (id: string) => encodeURIComponent(id);
const filenameFallback = (source: string) => {
  const basename = source.split(/[\\/]/).pop() || "etsy-listing.xlsx";
  const stem = basename.replace(/\.xlsx$/i, "").replace(/[\u0000-\u001f<>:"/\\|?*]/g, "_").trim().slice(0, 120) || "etsy-listing";
  return `${stem}_listing.xlsx`;
};

const safeDownloadFilename = (header: string | null, source: string) => {
  let candidate = "";
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { candidate = decodeURIComponent(encoded); } catch { candidate = ""; }
  }
  if (!candidate) {
    const quoted = header?.match(/filename="([^"]+)"/i)?.[1];
    candidate = quoted ?? "";
  }
  candidate = candidate.split(/[\\/]/).pop()?.replace(/[\u0000-\u001f<>:"/\\|?*]/g, "_").trim() ?? "";
  if (!candidate.toLowerCase().endsWith(".xlsx") || !candidate || candidate.length > 180) return filenameFallback(source);
  return candidate;
};

const boundedError = async (response: Response) => {
  try {
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (declared > 0 && declared <= 64 * 1024) await response.text();
  } catch { /* Raw server diagnostics are intentionally discarded. */ }
  const code = response.status === 404 ? "not_found" : response.status === 409 ? "conflict" : response.status === 422 ? "invalid_input" : response.status === 503 ? "employee_unavailable" : response.status >= 500 ? "server_error" : "bad_request";
  return new HttpError(code, response.status);
};

export const excelApi: ExcelApi = {
  createJob(file, signal) {
    const form = new FormData();
    form.set("file", file, file.name);
    return apiRequest<ExcelJob>("/excel-jobs", { method: "POST", body: form, signal, timeoutMs: 90_000 });
  },
  listJobs: (signal, limit = 20, offset = 0) => apiRequest<ExcelJobPage>(`/excel-jobs?limit=${limit}&offset=${offset}`, { signal }),
  getJob: (id, signal) => apiRequest<ExcelJob>(`/excel-jobs/${safeJobId(id)}`, { signal }),
  streamJob: (id, options) => openEventStream(`/excel-jobs/${safeJobId(id)}/events`, {
    ...options,
    onEvent: (value, eventId) => {
      if (!value || typeof value !== "object") return;
      const event = value as Record<string, unknown>;
      if (typeof event.type !== "string" || event.type.length > 64) return;
      options.onEvent(event as unknown as ExcelJobEvent, eventId);
    },
  }),
  cancelJob: (id, signal) => apiRequest<ExcelJob>(`/excel-jobs/${safeJobId(id)}/cancel`, { method: "POST", signal }),
  async downloadJob(id, sourceFilename, signal) {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/excel-jobs/${safeJobId(id)}/download`, { signal, headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new HttpError("network", 0);
    }
    if (!response.ok) throw await boundedError(response);
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (declared > 60 * 1024 * 1024) throw new HttpError("server_error", response.status);
    return { blob: await response.blob(), filename: safeDownloadFilename(response.headers.get("content-disposition"), sourceFilename) };
  },
};

export { safeDownloadFilename };
