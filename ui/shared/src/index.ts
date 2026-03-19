/** @apme/ui-shared — shared types, hooks, components, and utilities. */

// API client
export { ApmeApiClient, ApiError } from './api-client/services';
export type { ApmeApiConfig } from './api-client/services';

// API models / types
export type {
  ScanStatus,
  ScanType,
  ProposalStatus,
  ViolationLevel,
  ScanCreate,
  ScanSummary,
  ScanDetail,
  Violation,
  ValidatorDiagnostics,
  RuleTiming,
  Diagnostics,
  PaginatedResponse,
  Rule,
  RuleDetail,
  FormatDiff,
  FixJobStatus,
  RemediationProposal,
  ServiceHealth,
  HealthStatus,
} from './api-client/models';

// React context
export { ApmeApiContext } from './hooks/useApiClient';
export { useApiClient } from './hooks/useApiClient';

// React hooks
export {
  useScans,
  useScan,
  useCreateScan,
  useDeleteScan,
  useRules,
  useRule,
  useHealth,
  useRemediationQueue,
  useAcceptProposal,
  useRejectProposal,
  useFixJob,
  useCreateFixJob,
} from './hooks';

// Components
export {
  SeverityBadge,
  StatusBadge,
  ViolationRow,
  DiagnosticsPanel,
  DiffViewer,
  MetricCard,
} from './components';

// Utils
export {
  SEVERITY_COLORS,
  STATUS_COLORS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  formatDuration,
  formatDate,
  formatRelativeTime,
  ruleCategory,
  groupByFile,
} from './utils';
