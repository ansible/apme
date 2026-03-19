import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Content,
  Flex,
  FlexItem,
  Gallery,
  Label,
  PageSection,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import {
  useScans,
  MetricCard,
  StatusBadge,
  formatDate,
  SEVERITY_COLORS,
  type ScanStatus,
} from '@apme/ui-shared';

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const { data, isLoading } = useScans({ page: 1, page_size: 10 });

  const scans = data?.items ?? [];
  const totalScans = data?.total ?? 0;
  const openIssues = scans
    .filter((s) => s.status === 'failed')
    .reduce((acc, s) => acc + s.total_violations, 0);
  const completedThisWeek = scans.filter((s) => {
    const d = new Date(s.created_at);
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return s.status === 'completed' && d >= weekAgo;
  }).length;

  return (
    <PageSection>
      <Flex
        direction={{ default: 'column' }}
        spaceItems={{ default: 'spaceItemsLg' }}
      >
        <FlexItem>
          <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }}>
            <FlexItem>
              <Content component="h1">Dashboard</Content>
            </FlexItem>
            <FlexItem>
              <Button variant="primary" onClick={() => navigate('/scans/new')}>
                New Scan
              </Button>
            </FlexItem>
          </Flex>
        </FlexItem>

        <FlexItem>
          <Gallery hasGutter minWidths={{ default: '180px' }}>
            <MetricCard title="Total Scans" value={totalScans} />
            <MetricCard
              title="Open Issues"
              value={openIssues}
              color={SEVERITY_COLORS.warning}
            />
            <MetricCard
              title="Completed This Week"
              value={completedThisWeek}
              color="#5ba352"
            />
          </Gallery>
        </FlexItem>

        <FlexItem>
          <Content component="h3">Recent Scans</Content>
          {isLoading ? (
            <Content component="p">Loading...</Content>
          ) : (
            <Table aria-label="Recent scans" variant="compact">
              <Thead>
                <Tr>
                  <Th>Target</Th>
                  <Th>Type</Th>
                  <Th>Status</Th>
                  <Th>Errors</Th>
                  <Th>Warnings</Th>
                  <Th>Hints</Th>
                  <Th>Fixed</Th>
                  <Th>Date</Th>
                </Tr>
              </Thead>
              <Tbody>
                {scans.map((scan) => (
                  <Tr
                    key={scan.id}
                    isClickable
                    onRowClick={() => navigate(`/scans/${scan.id}`)}
                  >
                    <Td dataLabel="Target">
                      <span className="apme-mono">{scan.project_name}</span>
                    </Td>
                    <Td dataLabel="Type">
                      <Label
                        color={scan.scan_type === 'fix' ? 'teal' : 'grey'}
                        isCompact
                      >
                        {scan.scan_type === 'fix' ? 'Fix' : 'Scan'}
                      </Label>
                    </Td>
                    <Td dataLabel="Status">
                      <StatusBadge status={scan.status as ScanStatus} />
                    </Td>
                    <Td dataLabel="Errors">
                      <span style={{ color: SEVERITY_COLORS.error }}>
                        {scan.error_count}
                      </span>
                    </Td>
                    <Td dataLabel="Warnings">
                      <span style={{ color: SEVERITY_COLORS.warning }}>
                        {scan.warning_count}
                      </span>
                    </Td>
                    <Td dataLabel="Hints">
                      <span style={{ color: SEVERITY_COLORS.hint }}>
                        {scan.hint_count}
                      </span>
                    </Td>
                    <Td dataLabel="Fixed">
                      {scan.fixed_count > 0 ? (
                        <span style={{ color: '#5ba352' }}>{scan.fixed_count}</span>
                      ) : (
                        <span style={{ opacity: 0.5 }}>{scan.scan_type === 'fix' ? '0' : '\u2014'}</span>
                      )}
                    </Td>
                    <Td dataLabel="Date">
                      {formatDate(scan.created_at)}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
