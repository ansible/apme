import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { ViolationStatusBar } from '../components/ViolationStatusBar';
import { severityClass, FIX_AI_TRIED } from '../components/severity';
import { RESOLUTION_AI_ABSTAINED } from '../types/constants';
import { SeverityStatusBar } from '../components/SeverityStatusBar';
import { ViolationOutputToolbar } from '../components/ViolationOutputToolbar';
import { ViolationOutput } from '../components/ViolationOutput';
import { PipelineLogOutput } from '../components/PipelineLogOutput';
import { DependencyHealthOutput, isDepHealthViolation } from '../components/DependencyHealthOutput';
import {
  Alert,
  AlertActionCloseButton,
  Button,
  ExpandableSection,
  Flex,
  FlexItem,
} from '@patternfly/react-core';
import { ExternalLinkAltIcon } from '@patternfly/react-icons';
import { createSuppression, deleteActivity, getActivity, submitActivity } from '../services/api';
import { useFeedbackEnabled } from '../hooks/useFeedbackEnabled';
import type { ActivityDetail, ViolationDetail } from '../types/api';


function displayType(scanType: string): string {
  if (scanType === 'scan') return 'check';
  if (scanType === 'fix') return 'remediate';
  return scanType;
}

export function ActivityDetailPage() {
  const { activityId } = useParams<{ activityId: string }>();
  const navigate = useNavigate();
  const feedbackEnabled = useFeedbackEnabled();
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const [sevFilters, setSevFilters] = useState<Set<string>>(new Set());
  const [ruleFilters, setRuleFilters] = useState<Set<string>>(new Set());
  const [scopeFilters, setScopeFilters] = useState<Set<number>>(new Set());
  const [fixFilters, setFixFilters] = useState<Set<number>>(new Set());
  const [searchText, setSearchText] = useState('');
  const [resultsOpen, setResultsOpen] = useState(true);
  const [prCreating, setPrCreating] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [acknowledgedIds, setAcknowledgedIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!activityId) return;
    setLoading(true);
    getActivity(activityId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [activityId]);

  const projectViolations = useMemo(() => {
    if (!detail) return [];
    return detail.violations.filter((v) => !isDepHealthViolation(v) && !v.suppressed);
  }, [detail]);

  const depHealthCount = useMemo(() => {
    if (!detail) return 0;
    return detail.violations.filter(
      (v) => isDepHealthViolation(v) && !v.suppressed && !acknowledgedIds.has(v.id),
    ).length;
  }, [detail, acknowledgedIds]);

  const sevCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const v of projectViolations) {
      const cls = severityClass(v.level, v.rule_id);
      counts.set(cls, (counts.get(cls) ?? 0) + 1);
    }
    return counts;
  }, [projectViolations]);

  const uniqueRules = useMemo(() => {
    const set = new Set<string>();
    for (const v of projectViolations) set.add(v.rule_id);
    return Array.from(set).sort();
  }, [projectViolations]);

  const scopeCounts = useMemo(() => {
    const counts = new Map<number, number>();
    for (const v of projectViolations) {
      if (v.scope != null) {
        counts.set(v.scope, (counts.get(v.scope) ?? 0) + 1);
      }
    }
    return counts;
  }, [projectViolations]);

  const fixCounts = useMemo(() => {
    const counts = new Map<number, number>();
    for (const v of projectViolations) {
      if (v.remediation_resolution === RESOLUTION_AI_ABSTAINED) {
        counts.set(FIX_AI_TRIED, (counts.get(FIX_AI_TRIED) ?? 0) + 1);
      } else if (v.remediation_class > 0) {
        counts.set(v.remediation_class, (counts.get(v.remediation_class) ?? 0) + 1);
      }
    }
    return counts;
  }, [projectViolations]);

  const filtered = useMemo(() => {
    let violations = projectViolations;
    if (sevFilters.size > 0) {
      violations = violations.filter((v) => sevFilters.has(severityClass(v.level, v.rule_id)));
    }
    if (ruleFilters.size > 0) {
      violations = violations.filter((v) => ruleFilters.has(v.rule_id));
    }
    if (scopeFilters.size > 0) {
      violations = violations.filter((v) => v.scope != null && scopeFilters.has(v.scope));
    }
    if (fixFilters.size > 0) {
      violations = violations.filter((v) => {
        if (v.remediation_resolution === RESOLUTION_AI_ABSTAINED) {
          return fixFilters.has(FIX_AI_TRIED);
        }
        return fixFilters.has(v.remediation_class);
      });
    }
    if (searchText.trim()) {
      const q = searchText.toLowerCase();
      violations = violations.filter((v) =>
        v.message.toLowerCase().includes(q) ||
        v.rule_id.toLowerCase().includes(q) ||
        v.file.toLowerCase().includes(q) ||
        (v.path && v.path.toLowerCase().includes(q))
      );
    }
    return violations;
  }, [projectViolations, sevFilters, ruleFilters, scopeFilters, fixFilters, searchText]);

  if (loading) return <PageLayout><div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div></PageLayout>;
  if (!detail) return <PageLayout><div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Activity not found.</div></PageLayout>;

  const hasFilters = sevFilters.size > 0 || ruleFilters.size > 0 || scopeFilters.size > 0 || fixFilters.size > 0 || searchText.length > 0;

  const handleDelete = async () => {
    if (!activityId || !confirm('Delete this activity record? This cannot be undone.')) return;
    try {
      await deleteActivity(activityId);
      navigate('/activity');
    } catch {
      alert('Failed to delete activity record.');
    }
  };

  const handleCreatePR = async () => {
    if (!activityId || !detail?.project_id) return;
    setPrCreating(true);
    setPrError(null);
    try {
      const result = await submitActivity(detail.project_id, activityId);
      if (result.pr_url) {
        setDetail((prev) => prev ? { ...prev, pr_url: result.pr_url } : prev);
      }
    } catch (err) {
      setPrError(err instanceof Error ? err.message : 'Failed to create pull request');
    } finally {
      setPrCreating(false);
    }
  };

  const handleAcknowledge = async (violation: ViolationDetail) => {
    setAckError(null);
    try {
      const hasYaml = !!violation.original_yaml?.trim();
      await createSuppression({
        rule_id: violation.rule_id,
        original_yaml: hasYaml ? violation.original_yaml! : '',
        fingerprint_mode: hasYaml ? 'full' : 'rule_only',
        scope: detail?.project_id ? `project:${detail.project_id}` : 'global',
        reason: 'Acknowledged via activity detail',
      });
      setAcknowledgedIds(prev => new Set(prev).add(violation.id));
    } catch (err: unknown) {
      const status = err != null && typeof err === 'object' && 'status' in err
        ? (err as { status: number }).status
        : undefined;
      if (status === 409) {
        setAcknowledgedIds(prev => new Set(prev).add(violation.id));
      } else {
        setAckError(`Failed to acknowledge violation: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
  };

  const isRemediate = detail.scan_type === 'fix' || detail.scan_type === 'remediate';
  const canCreatePR = isRemediate && detail.patches.length > 0 && !detail.pr_url && !!detail.project_id;

  return (
    <PageLayout>
      <PageHeader
        title={detail.project_path}
        breadcrumbs={[
          { label: 'Activity', to: '/activity' },
          { label: detail.project_path },
        ]}
        description={`${displayType(detail.scan_type)} via ${detail.source} — ${new Date(detail.created_at).toLocaleString()}`}
        headerActions={
          <Flex gap={{ default: 'gapSm' }} alignItems={{ default: 'alignItemsCenter' }}>
            {detail.pr_url && (
              <FlexItem>
                <Button
                  variant="link"
                  component="a"
                  href={detail.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  icon={<ExternalLinkAltIcon />}
                  iconPosition="end"
                  size="sm"
                >
                  View PR
                </Button>
              </FlexItem>
            )}
            {canCreatePR && (
              <FlexItem>
                <Button
                  variant="secondary"
                  onClick={handleCreatePR}
                  isLoading={prCreating}
                  isDisabled={prCreating}
                  size="sm"
                >
                  {prCreating ? 'Creating PR...' : 'Create PR'}
                </Button>
              </FlexItem>
            )}
            <FlexItem>
              <Button variant="danger" onClick={handleDelete} size="sm">
                Delete
              </Button>
            </FlexItem>
          </Flex>
        }
      />

      {prError && (
        <div style={{ padding: '16px 24px 0' }}>
          <Alert
            variant="danger"
            isInline
            title="Pull request creation failed"
            actionClose={<AlertActionCloseButton onClose={() => setPrError(null)} />}
          >
            {prError}
          </Alert>
        </div>
      )}

      {ackError && (
        <div style={{ padding: '16px 24px 0' }}>
          <Alert
            variant="warning"
            isInline
            title="Acknowledge failed"
            actionClose={<AlertActionCloseButton onClose={() => setAckError(null)} />}
          >
            {ackError}
          </Alert>
        </div>
      )}

      {/* Unified layout — all panels share viewport height */}
      <div className="apme-activity-layout">
        {/* Status bar + severity bar (fixed height, always visible) */}
        <ViolationStatusBar
          detail={{
            ...detail,
            violations: projectViolations,
            total_violations: projectViolations.length + depHealthCount,
            fixable: projectViolations.filter(v => v.remediation_class === 1).length,
            ai_candidate: projectViolations.filter(v => v.remediation_class === 2).length,
            manual_review: projectViolations.filter(v => v.remediation_class === 3).length,
          }}
          depHealthCount={depHealthCount}
        />
        <SeverityStatusBar sevCounts={sevCounts} />
        <ViolationOutputToolbar
          searchText={searchText}
          onSearchChange={setSearchText}
          sevFilters={sevFilters}
          ruleFilters={ruleFilters}
          scopeFilters={scopeFilters}
          fixFilters={fixFilters}
          sevCounts={sevCounts}
          scopeCounts={scopeCounts}
          fixCounts={fixCounts}
          uniqueRules={uniqueRules}
          onSevChange={setSevFilters}
          onRuleChange={setRuleFilters}
          onScopeChange={setScopeFilters}
          onFixChange={setFixFilters}
          isRemediate={isRemediate}
          filteredCount={filtered.length}
          totalCount={projectViolations.length}
        />

        {/* Results panel */}
        <div className={`apme-output-panel ${resultsOpen ? 'apme-panel-open' : 'apme-panel-closed'}`}>
          <ViolationOutput
            violations={filtered}
            hasFilters={hasFilters}
            scanType={detail.scan_type}
            onSectionToggle={setResultsOpen}
            scanId={activityId}
            feedbackEnabled={feedbackEnabled}
          />
        </div>

        {/* Dependencies panel */}
        <DependencyHealthOutput
          violations={detail.violations}
          scanType={detail.scan_type}
          scanId={activityId}
          feedbackEnabled={feedbackEnabled}
          onAcknowledge={handleAcknowledge}
          acknowledgedIds={acknowledgedIds}
        />

        {/* Pipeline log panel */}
        <PipelineLogOutput logs={detail.logs} />
      </div>

      <div style={{ padding: '16px 24px 24px' }}>
        {detail.diagnostics_json && (
          <ExpandableSection toggleText="Diagnostics (raw)" style={{ marginTop: 16 }}>
            <pre style={{ padding: 16, fontSize: 12, overflow: 'auto', maxHeight: 400, background: 'var(--pf-t--global--background--color--secondary--default)' }}>
              {(() => {
                try { return JSON.stringify(JSON.parse(detail.diagnostics_json), null, 2); }
                catch { return detail.diagnostics_json; }
              })()}
            </pre>
          </ExpandableSection>
        )}
      </div>
    </PageLayout>
  );
}
