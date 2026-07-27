/**
 * Workflow primary CTA: Next + short summary of what the next step does.
 */

import { Button, Flex } from '@patternfly/react-core';
import { ArrowRightIcon } from '@patternfly/react-icons';
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
      <Flex
        direction={{ default: 'column' }}
        alignItems={{
          default: isHeader ? 'alignItemsFlexEnd' : 'alignItemsFlexStart',
        }}
        gap={{ default: 'gapSm' }}
      >
        <Flex
          gap={{ default: 'gapSm' }}
          alignItems={{ default: 'alignItemsCenter' }}
        >
          {secondary}
          <Button
            variant="primary"
            onClick={onNext}
            isLoading={isLoading}
            isDisabled={isDisabled || isLoading}
            icon={<ArrowRightIcon />}
            iconPosition="end"
          >
            {label}
          </Button>
        </Flex>
        <p className="apme-workflow-next-summary">{summary}</p>
      </Flex>
    </div>
  );
}
