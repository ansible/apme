import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Breadcrumb,
  BreadcrumbItem,
  Card,
  CardBody,
  CardTitle,
  Content,
  Flex,
  FlexItem,
  Gallery,
  PageSection,
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
  type ViolationLevel,
} from '@apme/ui-shared';

export const ScanDetailPage: React.FC = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const { data: scan, isLoading } = useScan(scanId);

  if (isLoading) {
    return (
      <PageSection>
        <Content component="p">Loading...</Content>
      </PageSection>
    );
  }

  if (!scan) {
    return (
      <PageSection>
        <Content component="p">Scan not found.</Content>
      </PageSection>
    );
  }

  const fileGroups = groupByFile(scan.violations);

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Breadcrumb>
            <BreadcrumbItem onClick={() => navigate('/scans')}>
              All Scans
            </BreadcrumbItem>
            <BreadcrumbItem isActive>
              <span className="apme-mono">{scan.project_name}</span>
            </BreadcrumbItem>
          </Breadcrumb>
        </FlexItem>

        <FlexItem>
          <Flex alignItems={{ default: 'alignItemsCenter' }} spaceItems={{ default: 'spaceItemsMd' }}>
            <FlexItem>
              <Content component="h1">{scan.project_name}</Content>
            </FlexItem>
            <FlexItem>
              <StatusBadge status={scan.status as ScanStatus} />
            </FlexItem>
          </Flex>
        </FlexItem>

        <FlexItem>
          <Gallery hasGutter minWidths={{ default: '140px' }}>
            <MetricCard
              title="Errors"
              value={scan.error_count}
              color={SEVERITY_COLORS.error}
            />
            <MetricCard
              title="Warnings"
              value={scan.warning_count}
              color={SEVERITY_COLORS.warning}
            />
            <MetricCard
              title="Hints"
              value={scan.hint_count}
              color={SEVERITY_COLORS.hint}
            />
            <MetricCard title="Total" value={scan.total_violations} />
          </Gallery>
        </FlexItem>

        <FlexItem>
          {Array.from(fileGroups.entries()).map(([file, violations]) => (
            <Card key={file} isPlain style={{ marginBottom: 16 }}>
              <CardTitle>
                <span className="apme-mono">{file}</span>
                <span style={{ marginLeft: 8, opacity: 0.6 }}>
                  ({violations.length} issue{violations.length !== 1 ? 's' : ''})
                </span>
              </CardTitle>
              <CardBody style={{ padding: 0 }}>
                {violations.map((v) => (
                  <ViolationRow key={v.id} violation={v} />
                ))}
              </CardBody>
            </Card>
          ))}
        </FlexItem>

        {scan.diagnostics && (
          <FlexItem>
            <DiagnosticsPanel diagnostics={scan.diagnostics as Diagnostics} />
          </FlexItem>
        )}
      </Flex>
    </PageSection>
  );
};
