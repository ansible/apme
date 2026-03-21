import type {
  HealthOut,
  RuleListOut,
  ScanListOut,
  ScanOut,
} from "../types/api";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function getHealth(): Promise<HealthOut> {
  return request("/health");
}

export function listScans(
  page = 1,
  pageSize = 20
): Promise<ScanListOut> {
  return request(`/scans?page=${page}&page_size=${pageSize}`);
}

export function getScan(scanId: string): Promise<ScanOut> {
  return request(`/scans/${scanId}`);
}

export function deleteScan(scanId: string): Promise<void> {
  return request(`/scans/${scanId}`, { method: "DELETE" });
}

export function createScan(projectPath: string): Promise<ScanOut> {
  return request("/scans", {
    method: "POST",
    body: JSON.stringify({ project_path: projectPath }),
  });
}

export function listRules(): Promise<RuleListOut> {
  return request("/rules");
}
