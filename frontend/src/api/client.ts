export const API_BASE = "/api";
const MAX_JSON_REQUEST_BYTES = 256 * 1024;
const MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024;

export type HttpErrorCode =
  | "bad_request"
  | "not_found"
  | "conflict"
  | "invalid_input"
  | "employee_unavailable"
  | "timeout"
  | "network"
  | "server_error";

const statusCodes: Record<number, HttpErrorCode> = {
  400: "bad_request",
  404: "not_found",
  409: "conflict",
  422: "invalid_input",
  503: "employee_unavailable",
};

export class HttpError extends Error {
  constructor(
    public readonly code: HttpErrorCode,
    public readonly status: number,
    public readonly details?: string,
  ) {
    super(code);
    this.name = "HttpError";
  }
}

const responseError = async (response: Response) => {
  // Consume a bounded body so the connection can be reused, but never surface raw
  // backend/employee output (which may contain paths or provider diagnostics).
  try {
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (!declared || declared <= MAX_JSON_RESPONSE_BYTES) {
      const text = await response.text();
      if (new TextEncoder().encode(text).byteLength <= MAX_JSON_RESPONSE_BYTES) JSON.parse(text);
    }
  } catch {
    // An unstructured server body is intentionally not surfaced to the UI.
  }
  const code = statusCodes[response.status] ?? (response.status >= 500 ? "server_error" : "bad_request");
  return new HttpError(code, response.status);
};

const withTimeout = (external: AbortSignal | undefined, timeoutMs: number) => {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(external?.reason);
  external?.addEventListener("abort", abort, { once: true });
  if (external?.aborted) abort();
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    dispose: () => {
      window.clearTimeout(timer);
      external?.removeEventListener("abort", abort);
    },
  };
};

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  timeoutMs?: number;
}

export const apiRequest = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  const { timeoutMs = 15_000, signal: external, body, headers, ...init } = options;
  const timeout = withTimeout(external ?? undefined, timeoutMs);
  let encoded: BodyInit | undefined;
  const requestHeaders = new Headers(headers);
  if (body instanceof FormData) {
    encoded = body;
  } else if (body !== undefined) {
    const json = JSON.stringify(body);
    if (new TextEncoder().encode(json).byteLength > MAX_JSON_REQUEST_BYTES) {
      timeout.dispose();
      throw new HttpError("invalid_input", 0, "请求内容过大");
    }
    requestHeaders.set("Content-Type", "application/json");
    encoded = json;
  }
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      body: encoded,
      headers: requestHeaders,
      signal: timeout.signal,
    });
    if (!response.ok) throw await responseError(response);
    if (response.status === 204) return undefined as T;
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (declared > MAX_JSON_RESPONSE_BYTES) throw new HttpError("server_error", response.status);
    const text = await response.text();
    if (new TextEncoder().encode(text).byteLength > MAX_JSON_RESPONSE_BYTES) throw new HttpError("server_error", response.status);
    return JSON.parse(text) as T;
  } catch (error) {
    if (error instanceof HttpError) throw error;
    if (timeout.timedOut()) throw new HttpError("timeout", 0);
    if (external?.aborted) throw error;
    throw new HttpError("network", 0);
  } finally {
    timeout.dispose();
  }
};

export const openEventStream = async (
  path: string,
  options: {
    lastEventId?: number;
    onEvent: (data: unknown, eventId: number) => void;
    signal: AbortSignal;
    timeoutMs?: number;
  },
) => {
  const timeout = withTimeout(options.signal, options.timeoutMs ?? 190_000);
  const headers = new Headers({ Accept: "text/event-stream" });
  if (options.lastEventId) headers.set("Last-Event-ID", String(options.lastEventId));
  try {
    const response = await fetch(`${API_BASE}${path}`, { headers, signal: timeout.signal });
    if (!response.ok) throw await responseError(response);
    if (!response.body) throw new HttpError("network", 0);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let generatedId = options.lastEventId ?? 0;
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      if (new TextEncoder().encode(buffer).byteLength > 64 * 1024) throw new HttpError("server_error", response.status);
      for (const frame of frames) {
        if (new TextEncoder().encode(frame).byteLength > 64 * 1024) continue;
        const lines = frame.split(/\r?\n/);
        const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const supplied = lines.find((line) => line.startsWith("id:"))?.slice(3).trim();
        const parsedId = supplied ? Number(supplied) : ++generatedId;
        if (!Number.isSafeInteger(parsedId) || parsedId < 1) continue;
        try {
          options.onEvent(JSON.parse(data), parsedId);
        } catch {
          // Malformed and internal frames are ignored, never rendered.
        }
      }
      if (done) break;
    }
  } catch (error) {
    if (error instanceof HttpError) throw error;
    if (options.signal.aborted) throw error;
    if (timeout.timedOut()) throw new HttpError("timeout", 0);
    throw new HttpError("network", 0);
  } finally {
    timeout.dispose();
  }
};
