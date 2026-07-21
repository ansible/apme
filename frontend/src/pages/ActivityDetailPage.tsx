import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import { AssessFindingsPanel } from '../components/AssessFindingsPanel';
import { PipelineLogOutput } from '../components/PipelineLogOutput';
import {
  DependencyHealthOutput,
  isDepHealthViolation,
} from '../components/DependencyHealthOutput';
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
import type { AssessFinding } from '../hooks/useProjectOperationState';
import type { ActivityDetail, ViolationDetail } from '../types/api';

function displayType(scanType: string): string {
  if (scanType === 'scan') return 'check';
  if (scanType === 'fix') return 'remediate';
  return scanType;
}

function violationToFinding(v: ViolationDetail): AssessFinding {
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

export function ActivityDetailPage() {
  const { activityId } = useParams<{ activityId: string }>();
  const navigate = useNavigate();
  const feedbackEnabled = useFeedbackEnabled();
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [loading, setLoading] = useState(true);

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

  const findings = useMemo(() => {
    if (!detail) return [];
    return detail.violations
      .filter((v) => !isDepHealthViolation(v) && !v.suppressed)
      .map(violationToFinding);
  }, [detail]);

  if (loading) {
    return (
      <PageLayout>
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
      </PageLayout>
    );
  }
  if (!detail) {
    return (
      <PageLayout>
        <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>
          Activity not found.
        </div>
      </PageLayout>
    );
  }

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
    if (!activityId || !detail.project_id) return;
    setPrCreating(true);
    setPrError(null);
    try {
      const result = await submitActivity(detail.project_id, activityId);
      if (result.pr_url) {
        setDetail((prev) => (prev ? { ...prev, pr_url: result.pr_url } : prev));
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
        scope: detail.project_id ? `project:${detail.project_id}` : 'global',
        reason: 'Acknowledged via activity detail',
      });
      setAcknowledgedIds((prev) => new Set(prev).add(violation.id));
    } catch (err: unknown) {
      const status =
        err != null && typeof err === 'object' && 'status' in err
          ? (err as { status: number }).status
          : undefined;
      if (status === 409) {
        setAcknowledgedIds((prev) => new Set(prev).add(violation.id));
      } else {
        setAckError(
          `Failed to acknowledge violation: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  };

  const isRemediate = detail.scan_type === 'fix' || detail.scan_type === 'remediate';
  const canCreatePR =
    isRemediate && detail.patches.length > 0 && !detail.pr_url && !!detail.project_id;

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

      <div style={{ padding: '16px 24px 0' }}>
        <AssessFindingsPanel findings={findings} />
      </div>

      <div style={{ padding: '0 24px' }}>
        <DependencyHealthOutput
          violations={detail.violations}
          scanType={detail.scan_type}
          scanId={activityId}
          feedbackEnabled={feedbackEnabled}
          onAcknowledge={handleAcknowledge}
          acknowledgedIds={acknowledgedIds}
        />
        <PipelineLogOutput logs={detail.logs} />
      </div>

      <div style={{ padding: '16px 24px 24px' }}>
        {detail.diagnostics_json && (
          <ExpandableSection toggleText="Diagnostics (raw)" style={{ marginTop: 16 }}>
            <pre
              style={{
                padding: 16,
                fontSize: 12,
                overflow: 'auto',
                maxHeight: 400,
                background: 'var(--pf-t--global--background--color--secondary--default)',
              }}
            >
              {(() => {
                try {
                  return JSON.stringify(JSON.parse(detail.diagnostics_json), null, 2);
                } catch {
                  return detail.diagnostics_json;
                }
              })()}
            </pre>
          </ExpandableSection>
        )}
      </div>
    </PageLayout>
  );
}
