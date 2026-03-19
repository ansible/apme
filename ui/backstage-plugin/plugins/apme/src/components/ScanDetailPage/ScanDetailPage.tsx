import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Content, Header, Page } from '@backstage/core-components';
import {
  Card,
  CardBody,
  CardTitle,
  Flex,
  FlexItem,
  Gallery,
} from '@patternfly/react-core';
import {
  useScan,
  StatusBadge,
  ViolationRow,
  DiagnosticsPanel,
  MetricCard,
  groupByFile,
  SEVERITY_COLORS,
  type ScanStatus,
  type Diagnostics,
} from '@apme/ui-shared';

export const ScanDetailPage: React.FC = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const { data: scan, isLoading } = useScan(scanId);

  if (isLoading) return <Page themeId="tool"><Header title="Loading..." /><Content><p>Loading...</p></Content></Page>;
  if (!scan) return <Page themeId="tool"><Header title="Not Found" /><Content><p>Scan not found.</p></Content></Page>;

  const fileGroups = groupByFile(scan.violations);

  return (
    <Page themeId="tool">
      <Header
        title={scan.project_name}
        subtitle={<StatusBadge status={scan.status as ScanStatus} />}
      />
      <Content>
        <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
          <FlexItem>
            <Gallery hasGutter minWidths={{ default: '140px' }}>
              <MetricCard title="Errors" value={scan.error_count} color={SEVERITY_COLORS.error} />
              <MetricCard title="Warnings" value={scan.warning_count} color={SEVERITY_COLORS.warning} />
              <MetricCard title="Hints" value={scan.hint_count} color={SEVERITY_COLORS.hint} />
            </Gallery>
          </FlexItem>

          {Array.from(fileGroups.entries()).map(([file, violations]) => (
            <FlexItem key={file}>
              <Card isPlain>
                <CardTitle><code>{file}</code> ({violations.length})</CardTitle>
                <CardBody style={{ padding: 0 }}>
                  {violations.map((v) => <ViolationRow key={v.id} violation={v} />)}
                </CardBody>
              </Card>
            </FlexItem>
          ))}

          {scan.diagnostics && (
            <FlexItem>
              <DiagnosticsPanel diagnostics={scan.diagnostics as Diagnostics} />
            </FlexItem>
          )}
        </Flex>
      </Content>
    </Page>
  );
};
