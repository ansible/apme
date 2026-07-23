/**
 * Commit step — branch, push remediation patches, optionally open a PR (ADR-050).
 */

import { useMemo, useState } from 'react';
import {
  Button,
  Card,
  CardBody,
  Checkbox,
  Flex,
  FormGroup,
  TextInput,
  Label,
} from '@patternfly/react-core';
import { ExternalLinkAltIcon } from '@patternfly/react-icons';
import { WorkflowNextBar } from './WorkflowNextBar';

export interface CommitSubmitOptions {
  create_pr?: boolean;
  branch_name?: string;
  title?: string;
}

export interface CommitSubmitResult {
  branch_name: string;
  commit_sha: string;
  pr_url: string | null;
  provider: string;
}

export interface CommitChangesPanelProps {
  remediatedCount: number;
  scanId?: string;
  onSubmit: (options: CommitSubmitOptions) => Promise<CommitSubmitResult>;
  onFinish: () => void;
  /** Leave the workflow without finishing the commit step. */
  onDismiss?: () => void;
  submitting?: boolean;
  error?: string | null;
  /** Already-known PR from a prior submit. */
  prUrl?: string | null;
}

export function CommitChangesPanel({
  remediatedCount,
  scanId,
  onSubmit,
  onFinish,
  onDismiss,
  submitting,
  error,
  prUrl: existingPrUrl,
}: CommitChangesPanelProps) {
  const defaultBranch = useMemo(
    () => `apme/remediate-${(scanId ?? 'fix').slice(0, 8)}`,
    [scanId],
  );
  const [branchName, setBranchName] = useState(defaultBranch);
  const [createPr, setCreatePr] = useState(true);
  const [result, setResult] = useState<CommitSubmitResult | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const prUrl = existingPrUrl || result?.pr_url || null;
  const pushed = Boolean(result) || Boolean(existingPrUrl);
  const commitHeading =
    remediatedCount > 0
      ? `Commit ${remediatedCount} remediated change${remediatedCount !== 1 ? 's' : ''}`
      : 'Commit remediation changes';
  const defaultPrTitle =
    remediatedCount > 0
      ? `fix: APME remediation — ${remediatedCount} finding${remediatedCount !== 1 ? 's' : ''} resolved`
      : 'fix: APME remediation changes';

  const handleCommit = async () => {
    setLocalError(null);
    try {
      const res = await onSubmit({
        branch_name: branchName.trim() || defaultBranch,
        create_pr: createPr,
        title: defaultPrTitle,
      });
      setResult(res);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Commit failed');
    }
  };

  const handleOpenPrOnly = async () => {
    setLocalError(null);
    try {
      const res = await onSubmit({
        branch_name: (result?.branch_name || branchName).trim() || defaultBranch,
        create_pr: true,
      });
      setResult(res);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Failed to open PR');
    }
  };

  const displayError = localError || error;
  const submitLabel = createPr ? 'Commit & open PR' : 'Commit & push';

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <div className="apme-review-step-header" style={{ marginBottom: 16 }}>
          <div className="apme-review-step-header__text">
            <h3 style={{ marginTop: 0 }}>{commitHeading}</h3>
            <span style={{ fontSize: 13, opacity: 0.7 }}>
              Create a branch, push the fixes, and optionally open a pull request.
            </span>
          </div>
          <div className="apme-review-step-header__actions">
            <WorkflowNextBar
              placement="header"
              label="Next"
              summary={
                pushed
                  ? 'Continue to the complete step.'
                  : 'Continue without pushing. Use Commit below to push or open a PR first.'
              }
              onNext={onFinish}
              secondary={
                onDismiss ? (
                  <Button variant="link" onClick={onDismiss}>
                    Cancel
                  </Button>
                ) : undefined
              }
            />
          </div>
        </div>

        {displayError && (
          <div
            style={{
              marginBottom: 12,
              padding: '8px 16px',
              borderRadius: 6,
              background: 'var(--pf-t--global--color--status--danger--default)',
              color: '#fff',
              fontSize: 13,
            }}
          >
            {displayError}
          </div>
        )}

        {pushed ? (
          <div>
            <Label color="green" isCompact>
              Pushed
            </Label>
            <div style={{ marginTop: 8, fontSize: 14 }}>
              Branch{' '}
              <code>{result?.branch_name || branchName}</code>
              {result?.commit_sha ? (
                <>
                  {' '}
                  @ <code>{result.commit_sha.slice(0, 8)}</code>
                </>
              ) : null}
            </div>
            <Flex gap={{ default: 'gapSm' }} style={{ marginTop: 16 }}>
              {prUrl ? (
                <Button
                  variant="secondary"
                  component="a"
                  href={prUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  icon={<ExternalLinkAltIcon />}
                  iconPosition="end"
                >
                  View pull request
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => {
                    handleOpenPrOnly().catch(() => {});
                  }}
                  isLoading={submitting}
                  isDisabled={submitting}
                >
                  Open pull request
                </Button>
              )}
            </Flex>
          </div>
        ) : (
          <div style={{ maxWidth: 420 }}>
            <FormGroup label="Branch name" fieldId="apme-commit-branch">
              <TextInput
                id="apme-commit-branch"
                value={branchName}
                onChange={(_e, v) => setBranchName(v)}
                isDisabled={submitting}
              />
            </FormGroup>
            <div style={{ marginTop: 12 }}>
              <Checkbox
                id="apme-commit-create-pr"
                label="Open a pull request after push"
                isChecked={createPr}
                onChange={(_e, checked) => setCreatePr(checked)}
                isDisabled={submitting}
              />
            </div>
            <div style={{ marginTop: 16 }}>
              <Button
                variant="primary"
                onClick={() => {
                  handleCommit().catch(() => {});
                }}
                isLoading={submitting}
                isDisabled={submitting}
              >
                {submitLabel}
              </Button>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
