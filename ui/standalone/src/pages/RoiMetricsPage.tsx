import React from 'react';
import {
  Card,
  CardBody,
  CardTitle,
  Content,
  Flex,
  FlexItem,
  Gallery,
  PageSection,
} from '@patternfly/react-core';
import { useScans, MetricCard, SEVERITY_COLORS } from '@apme/ui-shared';

export const RoiMetricsPage: React.FC = () => {
  const { data } = useScans({ page: 1, page_size: 1000 });
  const scans = data?.items ?? [];

  const totalScans = scans.length;
  const totalErrors = scans.reduce((acc, s) => acc + s.error_count, 0);
  const totalWarnings = scans.reduce((acc, s) => acc + s.warning_count, 0);
  const resolved = scans
    .filter((s) => s.status === 'completed')
    .reduce((acc, s) => acc + s.total_violations, 0);

  const avgMinutesPerError = 7.5;
  const hoursSaved = Math.round((resolved * avgMinutesPerError) / 60);
  const costPerHour = 75;
  const totalSavings = hoursSaved * costPerHour;
  const avgPerScan = totalScans > 0 ? Math.round(totalSavings / totalScans) : 0;

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Content component="h1">ROI Metrics</Content>
        </FlexItem>

        <FlexItem>
          <Gallery hasGutter minWidths={{ default: '180px' }}>
            <MetricCard
              title="Errors Resolved"
              value={resolved.toLocaleString()}
              color="#5ba352"
            />
            <MetricCard
              title="Estimated Time Saved"
              value={`${hoursSaved} hrs`}
              color={SEVERITY_COLORS.hint}
            />
            <MetricCard
              title="Total Errors Found"
              value={totalErrors.toLocaleString()}
              color={SEVERITY_COLORS.error}
            />
            <MetricCard
              title="Total Warnings"
              value={totalWarnings.toLocaleString()}
              color={SEVERITY_COLORS.warning}
            />
          </Gallery>
        </FlexItem>

        <FlexItem>
          <Gallery hasGutter minWidths={{ default: '220px' }}>
            <Card isPlain>
              <CardTitle>Cost Savings</CardTitle>
              <CardBody>
                <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsSm' }}>
                  <FlexItem>
                    <Content component="h2" style={{ color: '#5ba352' }}>
                      ${totalSavings.toLocaleString()}
                    </Content>
                    <Content component="small">Total estimated savings</Content>
                  </FlexItem>
                  <FlexItem>
                    <Content component="p">Scans run: {totalScans}</Content>
                    <Content component="p">Avg savings per scan: ${avgPerScan}</Content>
                    <Content component="p">Avg time per error: {avgMinutesPerError} min</Content>
                  </FlexItem>
                </Flex>
              </CardBody>
            </Card>

            <Card isPlain>
              <CardTitle>Error Breakdown</CardTitle>
              <CardBody>
                <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsSm' }}>
                  <FlexItem>
                    <Content component="p">
                      <span style={{ color: SEVERITY_COLORS.error }}>Errors:</span>{' '}
                      {totalErrors}
                    </Content>
                    <Content component="p">
                      <span style={{ color: SEVERITY_COLORS.warning }}>Warnings:</span>{' '}
                      {totalWarnings}
                    </Content>
                    <Content component="p">
                      <span style={{ color: '#5ba352' }}>Resolved:</span>{' '}
                      {resolved}
                    </Content>
                  </FlexItem>
                </Flex>
              </CardBody>
            </Card>
          </Gallery>
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
