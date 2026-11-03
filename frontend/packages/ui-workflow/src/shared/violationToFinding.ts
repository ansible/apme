/** Shared native/plugin mapping from a durable Gateway finding to its renderer. */
import type { AssessFinding } from '../hooks/useProjectOperationState';

export interface FindingSource {
  rule_id: string;
  level: string;
  message: string;
  file: string;
  line?: number | null;
  path?: string;
  node_type?: string;
  remediation_class?: number;
  validator_source?: string;
  original_yaml?: string;
  fixed_yaml?: string;
  co_fixes?: string[];
  node_line_start?: number | null;
  review_status?: string | null;
}

export function violationToFinding(v: FindingSource): AssessFinding {
  return {
    rule_id: v.rule_id,
    severity: v.level,
    message: v.message,
    file: v.file,
    line: v.line,
    path: v.path,
    node_type: v.node_type,
    remediation_class: v.remediation_class,
    source: v.validator_source,
    original_yaml: v.original_yaml,
    fixed_yaml: v.fixed_yaml,
    co_fixes: v.co_fixes,
    node_line_start: v.node_line_start,
    review_status: v.review_status,
  };
}
