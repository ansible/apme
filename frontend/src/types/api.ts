export interface ViolationOut {
  id: string;
  rule_id: string;
  level: string;
  message: string;
  file: string;
  line: number | null;
  path: string;
}

export interface ScanSummary {
  total: number;
  auto_fixable: number;
  ai_candidate: number;
  manual_review: number;
}

export interface ScanOut {
  id: string;
  project_path: string;
  created_at: string;
  status: string;
  total_violations: number;
  source: string;
  violations: ViolationOut[];
  diagnostics: Record<string, unknown> | null;
  summary: ScanSummary | null;
}

export interface ScanListItem {
  id: string;
  project_path: string;
  created_at: string;
  status: string;
  total_violations: number;
  source: string;
  secrets: number;
  errors: number;
  very_high: number;
  high: number;
  medium: number;
  warnings: number;
  low: number;
  very_low: number;
  hints: number;
  fixed: number;
  fixable: number;
}

export interface ScanListOut {
  items: ScanListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ServiceHealth {
  name: string;
  status: string;
  address: string;
}

export interface HealthOut {
  gateway: string;
  primary: ServiceHealth | null;
  downstream: ServiceHealth[];
}

export interface RuleOut {
  rule_id: string;
  description: string;
  level: string;
  validator: string;
  fixable: boolean;
  scope: string;
  tags: string[];
}

export interface RuleListOut {
  rules: RuleOut[];
  total: number;
}
