import React from 'react';
import { Content, Header, Page } from '@backstage/core-components';
import { Gallery } from '@patternfly/react-core';
import { useScans, MetricCard, SEVERITY_COLORS } from '@apme/ui-shared';

export const RoiMetricsPage: React.FC = () => {
  const { data } = useScans({ page: 1, page_size: 1000 });
  const scans = data?.items ?? [];

  const totalErrors = scans.reduce((acc, s) => acc + s.error_count, 0);
  const resolved = scans
    .filter((s) => s.status === 'completed')
    .reduce((acc, s) => acc + s.total_violations, 0);
  const hoursSaved = Math.round((resolved * 7.5) / 60);
  const totalSavings = hoursSaved * 75;

  return (
    <Page themeId="tool">
      <Header title="ROI Metrics" subtitle="Return on investment from automated scanning" />
      <Content>
        <Gallery hasGutter minWidths={{ default: '180px' }}>
          <MetricCard title="Errors Resolved" value={resolved.toLocaleString()} color="#5ba352" />
          <MetricCard title="Time Saved" value={`${hoursSaved} hrs`} color={SEVERITY_COLORS.hint} />
          <MetricCard title="Cost Savings" value={`$${totalSavings.toLocaleString()}`} color="#5ba352" />
          <MetricCard title="Total Errors" value={totalErrors.toLocaleString()} color={SEVERITY_COLORS.error} />
        </Gallery>
      </Content>
    </Page>
  );
};
