/**
 * @apme/ui-workflow — shared scan → pause → choose → remediate UI.
 * Hosts: native APME SPA shell, thin Portal Quality tab.
 * No react-router dependency; hosts supply navigation callbacks.
 */

export {
  ApmeApiProvider,
  apmeApiUrl,
  apmeWsUrl,
  apmeSseUrl,
  createDefaultApmeApiAdapter,
  getApmeApiAdapter,
  setApmeApiAdapter,
  useApmeApi,
  type ApmeApiAdapter,
} from './api/apmeApiAdapter';

export {
  useProjectWorkflow,
  type ProjectWorkflowCheckOptions,
  type ProjectWorkflowController,
  type UseProjectWorkflowOptions,
} from './useProjectWorkflow';

export {
  ProjectWorkflowPanel,
  type ProjectWorkflowPanelProps,
} from './ProjectWorkflowPanel';

export { OperationPanel, type OperationPanelProps } from './components/OperationPanel';
export { CheckOptionsForm } from './components/CheckOptionsForm';
export { AssessFindingsPanel } from './components/AssessFindingsPanel';
export { AiEscalationPanel } from './components/AiEscalationPanel';
export { ProposalReviewPanel } from './components/ProposalReviewPanel';
export { OperationProgressPanel } from './components/OperationProgressPanel';
export { Tier1ResultsPanel } from './components/Tier1ResultsPanel';
export { OperationResultCard } from './components/OperationResultCard';

export {
  LIVE_OPERATION_STATUSES,
  fetchProjectOperationState,
  useProjectOperationState,
  type ProjectOperationState,
  type AssessFinding,
} from './hooks/useProjectOperationState';

export {
  useProjectOperationActions,
  SessionExpiredError,
  WorkingSetConflictError,
} from './hooks/useProjectOperationActions';

export {
  useSessionStream,
  getPersistedSession,
  type Patch,
  type SessionResult,
  type Tier1Result,
  type PersistedSession,
} from './hooks/useSessionStream';

export type {
  OperationStatus,
  OperationProgress,
  OperationProposal,
  OperationResult,
} from './types/operation';

export { AI_MODEL_STORAGE_KEY } from './shared/constants';
export { RuleId } from './shared/RuleId';
export { FeedbackModal, type FeedbackPayload } from './shared/FeedbackModal';
export {
  severityClass,
  severityLabel,
  severityOrder,
  SEVERITY_LABELS,
  SEVERITY_ORDER,
  SEVERITY_INT_OPTIONS,
  SEVERITY_INT_TO_API,
  healthColor,
  healthLabelColor,
  bareRuleId,
  ruleSource,
  scopeLabel,
} from './shared/severity';

export { DiffView, textsFromUnifiedDiff, CurrentYamlView } from './components/DiffView';
