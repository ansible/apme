import type {
  AiAcceptanceEntry,
  FixRateEntry,
  HealthStatus,
  PaginatedResponse,
  ScanDetail,
  ScanSummary,
  SessionSummary,
  TopViolation,
  TrendPoint,
} from "../types/api";

const BASE = "/api/v1";

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function getHealth(): Promise<HealthStatus> {
  return request("/health");
}

export function listSessions(
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<SessionSummary>> {
  return request(`/sessions?limit=${limit}&offset=${offset}`);
}

export function listScans(
  limit = 50,
  offset = 0,
  sessionId?: string,
): Promise<PaginatedResponse<ScanSummary>> {
  let url = `/scans?limit=${limit}&offset=${offset}`;
  if (sessionId) url += `&session_id=${sessionId}`;
  return request(url);
}

export function getScan(scanId: string): Promise<ScanDetail> {
  return request(`/scans/${scanId}`);
}

export function getTopViolations(limit = 20): Promise<TopViolation[]> {
  return request(`/violations/top?limit=${limit}`);
}

export function getSessionTrend(sessionId: string): Promise<TrendPoint[]> {
  return request(`/sessions/${sessionId}/trend`);
}

export function getFixRates(limit = 20): Promise<FixRateEntry[]> {
  return request(`/stats/fix-rates?limit=${limit}`);
}

export function getAiAcceptance(): Promise<AiAcceptanceEntry[]> {
  return request(`/stats/ai-acceptance`);
}
