/** Typed API client for the APME Gateway REST API. */

import type {
  ScanCreate,
  ScanDetail,
  ScanSummary,
  PaginatedResponse,
  Rule,
  RuleDetail,
  FormatDiff,
  FixJobStatus,
  RemediationProposal,
  HealthStatus,
} from '../models';

export interface ApmeApiConfig {
  baseUrl: string;
  getHeaders?: () => Record<string, string>;
}

export class ApmeApiClient {
  private baseUrl: string;
  private getHeaders: () => Record<string, string>;

  constructor(config: ApmeApiConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, '');
    this.getHeaders = config.getHeaders ?? (() => ({}));
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...this.getHeaders(),
      ...(init?.headers as Record<string, string> | undefined),
    };
    const res = await fetch(url, { ...init, headers });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new ApiError(res.status, body || res.statusText);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
  }

  // --- Scans ---

  async createScan(body: ScanCreate): Promise<ScanDetail> {
    return this.request('/api/v1/scans', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async listScans(params?: {
    page?: number;
    page_size?: number;
    status_filter?: string;
    project?: string;
  }): Promise<PaginatedResponse<ScanSummary>> {
    const qs = new URLSearchParams();
    if (params?.page) qs.set('page', String(params.page));
    if (params?.page_size) qs.set('page_size', String(params.page_size));
    if (params?.status_filter) qs.set('status_filter', params.status_filter);
    if (params?.project) qs.set('project', params.project);
    const query = qs.toString();
    return this.request(`/api/v1/scans${query ? `?${query}` : ''}`);
  }

  async getScan(scanId: string): Promise<ScanDetail> {
    return this.request(`/api/v1/scans/${scanId}`);
  }

  async deleteScan(scanId: string): Promise<void> {
    return this.request(`/api/v1/scans/${scanId}`, { method: 'DELETE' });
  }

  // --- Rules ---

  async listRules(params?: {
    validator?: string;
    has_fixer?: boolean;
  }): Promise<Rule[]> {
    const qs = new URLSearchParams();
    if (params?.validator) qs.set('validator', params.validator);
    if (params?.has_fixer !== undefined) qs.set('has_fixer', String(params.has_fixer));
    const query = qs.toString();
    return this.request(`/api/v1/rules${query ? `?${query}` : ''}`);
  }

  async getRule(ruleId: string): Promise<RuleDetail> {
    return this.request(`/api/v1/rules/${ruleId}`);
  }

  // --- Format ---

  async formatFiles(files: Record<string, string>): Promise<FormatDiff[]> {
    return this.request('/api/v1/format', {
      method: 'POST',
      body: JSON.stringify({ files }),
    });
  }

  // --- Fix ---

  async createFixJob(body: {
    project_name: string;
    files: Record<string, string>;
    ansible_core_version?: string;
    collection_specs?: string[];
    apply?: boolean;
    ai?: boolean;
    max_passes?: number;
  }): Promise<FixJobStatus> {
    return this.request('/api/v1/fix', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async getFixJob(jobId: string): Promise<FixJobStatus> {
    return this.request(`/api/v1/fix/${jobId}`);
  }

  createFixStream(jobId: string): WebSocket {
    const wsUrl = this.baseUrl.replace(/^http/, 'ws');
    return new WebSocket(`${wsUrl}/api/v1/fix/${jobId}/stream`);
  }

  // --- Remediation ---

  async listRemediationQueue(scanId?: string): Promise<RemediationProposal[]> {
    const qs = scanId ? `?scan_id=${scanId}` : '';
    return this.request(`/api/v1/remediation/queue${qs}`);
  }

  async acceptProposal(
    proposalId: string,
    reviewedBy?: string,
  ): Promise<RemediationProposal> {
    return this.request(`/api/v1/remediation/${proposalId}/accept`, {
      method: 'POST',
      body: JSON.stringify({ reviewed_by: reviewedBy ?? '' }),
    });
  }

  async rejectProposal(
    proposalId: string,
    reviewedBy?: string,
  ): Promise<RemediationProposal> {
    return this.request(`/api/v1/remediation/${proposalId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reviewed_by: reviewedBy ?? '' }),
    });
  }

  // --- Health ---

  async getHealth(): Promise<HealthStatus> {
    return this.request('/api/v1/health');
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API error ${status}: ${body}`);
    this.name = 'ApiError';
  }
}
