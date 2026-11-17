/**
 * Workflow primary CTA: Next + short summary of what the next step does.
 */

import { Button, Flex } from '@backstage/ui';
import { RiArrowRightLine } from '@remixicon/react';
import type { ReactNode } from 'react';

export interface WorkflowNextConfig {
  label?: string;
  summary: string;
  onNext: () => void;
  isLoading?: boolean;
  isDisabled?: boolean;
  secondary?: ReactNode;
}

export interface WorkflowNextBarProps extends WorkflowNextConfig {
  /**
   * ``header`` — top-right of a review panel (no border).
   * ``panel`` — block under panel content (bordered).
   */
  placement?: 'panel' | 'header';
}

export function WorkflowNextBar({
  label = 'Next',
  summary,
  onNext,
  isLoading,
  isDisabled,
  secondary,
  placement = 'panel',
}: WorkflowNextBarProps) {
  const isHeader = placement === 'header';
  return (
    <div
      className={
        isHeader
          ? 'apme-workflow-next apme-workflow-next--header'
          : 'apme-workflow-next'
      }
    >
      <Flex direction="column" align={isHeader ? 'end' : 'start'} gap="2">
        <Flex gap="2" align="center">
          {secondary}
          <Button
            variant="primary"
            onPress={onNext}
            isPending={isLoading}
            isDisabled={isDisabled || isLoading}
            iconEnd={<RiArrowRightLine aria-hidden />}
          >
            {label}
          </Button>
        </Flex>
        <p className="apme-workflow-next-summary">{summary}</p>
      </Flex>
    </div>
  );
}
