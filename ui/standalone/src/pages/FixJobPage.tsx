import React from 'react';
import { useParams } from 'react-router-dom';
import {
  Card,
  CardBody,
  Content,
  Flex,
  FlexItem,
  Label,
  PageSection,
  ProgressStepper,
  ProgressStep,
} from '@patternfly/react-core';
import { useFixJob } from '@apme/ui-shared';

export const FixJobPage: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading } = useFixJob(jobId);

  if (isLoading || !job) {
    return (
      <PageSection>
        <Content component="p">Loading fix job...</Content>
      </PageSection>
    );
  }

  const statusColor =
    job.status === 'completed'
      ? 'green'
      : job.status === 'failed'
        ? 'red'
        : 'blue';

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Flex alignItems={{ default: 'alignItemsCenter' }} spaceItems={{ default: 'spaceItemsMd' }}>
            <FlexItem>
              <Content component="h1">Fix Job</Content>
            </FlexItem>
            <FlexItem>
              <Label color={statusColor} isCompact>
                {job.status.toUpperCase()}
              </Label>
            </FlexItem>
          </Flex>
          <Content component="small" className="apme-mono">{job.job_id}</Content>
        </FlexItem>

        <FlexItem>
          <Card isPlain>
            <CardBody>
              <ProgressStepper isVertical>
                {job.progress.map((step, i) => {
                  const phase = (step as Record<string, unknown>).phase as string ?? `Step ${i + 1}`;
                  const stepStatus = (step as Record<string, unknown>).status as string ?? 'pending';
                  const variant =
                    stepStatus === 'completed'
                      ? 'success'
                      : stepStatus === 'running'
                        ? 'info'
                        : 'pending';
                  return (
                    <ProgressStep
                      key={i}
                      variant={variant}
                      id={`step-${i}`}
                      titleId={`step-${i}-title`}
                      aria-label={phase}
                    >
                      {phase}
                    </ProgressStep>
                  );
                })}
              </ProgressStepper>
            </CardBody>
          </Card>
        </FlexItem>

        {job.result && (
          <FlexItem>
            <Card isPlain>
              <CardBody>
                <Content component="h4">Result</Content>
                <Content component="pre" className="apme-mono">
                  {JSON.stringify(job.result, null, 2)}
                </Content>
              </CardBody>
            </Card>
          </FlexItem>
        )}
      </Flex>
    </PageSection>
  );
};
