import { apiRequest } from "./client";

export type EmployeeStatus = "online" | "busy" | "offline";

export interface EmployeeStatusResponse {
  status: EmployeeStatus;
}

const STATUS_VALUES: readonly string[] = ["online", "busy", "offline"];

export const parseEmployeeStatus = (value: unknown): EmployeeStatus | null => {
  if (typeof value === "string" && STATUS_VALUES.includes(value)) {
    return value as EmployeeStatus;
  }
  return null;
};

export const fetchEmployeeStatus = async (signal?: AbortSignal): Promise<EmployeeStatusResponse> => {
  const payload = await apiRequest<unknown>("/employee/status", { signal, timeoutMs: 10_000 });
  const raw =
    payload && typeof payload === "object" ? (payload as { status?: unknown }).status : undefined;
  const status = parseEmployeeStatus(raw);
  if (status === null) throw new Error("Invalid employee status response");
  return { status };
};
