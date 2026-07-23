/**
 * Shared chrome for workflow review steps: header + Next + filters + node list.
 *
 * Assessment and Gate 1/2 proposal review use this so layout tweaks land once.
 */

import type { ReactNode } from 'react';
import { Button, Card, CardBody } from '@patternfly/react-core';
import {
  NodeReviewList,
  type NodeReviewListProps,
} from './NodeReviewList';
import {
  ReviewFilterBar,
  type ReviewFilterGroup,
} from './ReviewFilterBar';
import { WorkflowNextBar, type WorkflowNextConfig } from './WorkflowNextBar';

export interface ReviewStepShellProps {
  /** Optional; omit when the workflow tracker already names the step. */
  title?: ReactNode;
  description?: ReactNode;
  /** Omit for read-only history (no Next CTA). */
  next?: WorkflowNextConfig;
  onCancel?: () => void;
  filterGroups?: ReviewFilterGroup[];
  /** Main review list; omit or pass empty items with emptyMessage for empty state. */
  list?: NodeReviewListProps;
  /** Shown when ``list`` is omitted or ``list.items`` is empty. */
  emptyMessage?: string;
  /** Extra sections under the main list (explanation-only, Declined by AI, …). */
  children?: ReactNode;
  /** Rendered after CardBody (e.g. modals). */
  afterBody?: ReactNode;
}

export function ReviewStepShell({
  title,
  description,
  next,
  onCancel,
  filterGroups = [],
  list,
  emptyMessage = 'Nothing to show.',
  children,
  afterBody,
}: ReviewStepShellProps) {
  const hasItems = (list?.items.length ?? 0) > 0;
  const showHeaderActions = next != null || onCancel != null;

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <div className="apme-review-step-header">
          <div className="apme-review-step-header__text">
            {title != null && title !== '' ? (
              <h3 style={{ marginTop: 0 }}>{title}</h3>
            ) : null}
            {description != null && description !== '' && (
              <div className="apme-review-step-header__description">
                {description}
              </div>
            )}
          </div>
          {showHeaderActions && next != null && (
            <div className="apme-review-step-header__actions">
              <WorkflowNextBar
                placement="header"
                label={next.label}
                summary={next.summary}
                onNext={next.onNext}
                isDisabled={next.isDisabled}
                isLoading={next.isLoading}
                secondary={
                  onCancel ? (
                    <Button variant="link" onClick={onCancel}>
                      Cancel
                    </Button>
                  ) : (
                    next.secondary
                  )
                }
              />
            </div>
          )}
          {showHeaderActions && next == null && onCancel && (
            <div className="apme-review-step-header__actions">
              <Button variant="link" onClick={onCancel}>
                Cancel
              </Button>
            </div>
          )}
        </div>

        <ReviewFilterBar groups={filterGroups} />

        {!hasItems ? (
          <div style={{ padding: 24, textAlign: 'center', opacity: 0.65 }}>
            {emptyMessage}
          </div>
        ) : (
          list && <NodeReviewList {...list} />
        )}

        {children}
      </CardBody>
      {afterBody}
    </Card>
  );
}
