/**
 * Commit step — branch, push remediation patches, optionally open a PR (ADR-050).
 */

import { useMemo, useState, type ReactNode } from 'react';
import {
  Button,
  ButtonLink,
  Alert,
  Card,
  CardBody,
  Checkbox,
  Flex,
  TextField,
} from '@backstage/ui';
import { RiExternalLinkLine } from '@remixicon/react';
import { WorkflowNextBar } from './WorkflowNextBar';
import { toPrFilesDiffUrl } from '../utils/prDiffUrl';

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
  /** Host controls beside the pull request action after a successful push. */
  hostShipActions?: ReactNode;
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
  hostShipActions,
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
        branch_name:
          (result?.branch_name || branchName).trim() || defaultBranch,
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
              Create a branch, push the fixes, and optionally open a pull
              request.
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
                  <Button variant="tertiary" onPress={onDismiss}>
                    Cancel
                  </Button>
                ) : undefined
              }
            />
          </div>
        </div>

        {displayError && (
          <Alert
            status="danger"
            title={displayError}
            style={{ marginBottom: 12 }}
          />
        )}

        {pushed ? (
          <div>
            <Alert status="success" title="Pushed" />
            <div style={{ marginTop: 8, fontSize: 14 }}>
              Branch <code>{result?.branch_name || branchName}</code>
              {result?.commit_sha ? (
                <>
                  {' '}
                  @ <code>{result.commit_sha.slice(0, 8)}</code>
                </>
              ) : null}
            </div>
            <Flex gap="2" style={{ marginTop: 16 }}>
              {hostShipActions}
              {prUrl ? (
                <ButtonLink
                  variant="secondary"
                  href={toPrFilesDiffUrl(prUrl)}
                  target="_blank"
                  rel="noopener noreferrer"
                  iconEnd={<RiExternalLinkLine />}
                >
                  View pull request
                </ButtonLink>
              ) : (
                <Button
                  variant="secondary"
                  onPress={() => {
                    handleOpenPrOnly().catch(() => {});
                  }}
                  isPending={submitting}
                  isDisabled={submitting}
                >
                  Open pull request
                </Button>
              )}
            </Flex>
          </div>
        ) : (
          <div style={{ maxWidth: 420 }}>
            <TextField
              label="Branch name"
              id="apme-commit-branch"
              value={branchName}
              onChange={setBranchName}
              isDisabled={submitting}
            />
            <div style={{ marginTop: 12 }}>
              <Checkbox
                id="apme-commit-create-pr"
                isSelected={createPr}
                onChange={setCreatePr}
                isDisabled={submitting}
              >
                Open a pull request after push
              </Checkbox>
            </div>
            <Flex gap="2" style={{ marginTop: 16 }}>
              <Button
                variant="primary"
                onPress={() => {
                  handleCommit().catch(() => {});
                }}
                isPending={submitting}
                isDisabled={submitting}
              >
                {submitLabel}
              </Button>
            </Flex>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
