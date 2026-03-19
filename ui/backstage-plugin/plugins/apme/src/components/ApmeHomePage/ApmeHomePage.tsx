import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Content, Header, Page } from '@backstage/core-components';
import {
  Button,
  Flex,
  FlexItem,
  Gallery,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import {
  useScans,
  MetricCard,
  StatusBadge,
  formatRelativeTime,
  SEVERITY_COLORS,
  type ScanStatus,
} from '@apme/ui-shared';

export const ApmeHomePage: React.FC = () => {
  const navigate = useNavigate();
  const { data, isLoading } = useScans({ page: 1, page_size: 10 });
  const scans = data?.items ?? [];
  const totalScans = data?.total ?? 0;
  const openIssues = scans
    .filter((s) => s.status === 'failed')
    .reduce((acc, s) => acc + s.total_violations, 0);

  return (
    <Page themeId="tool">
      <Header title="APME Dashboard" subtitle="Ansible Policy & Modernization Engine" />
      <Content>
        <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
          <FlexItem>
            <Gallery hasGutter minWidths={{ default: '180px' }}>
              <MetricCard title="Total Scans" value={totalScans} />
              <MetricCard title="Open Issues" value={openIssues} color={SEVERITY_COLORS.warning} />
            </Gallery>
          </FlexItem>
          <FlexItem>
            <Flex justifyContent={{ default: 'justifyContentFlexEnd' }}>
              <FlexItem>
                <Button variant="primary" onClick={() => navigate('scans')}>
                  View All Scans
                </Button>
              </FlexItem>
            </Flex>
          </FlexItem>
          <FlexItem>
            {isLoading ? (
              <p>Loading...</p>
            ) : (
              <Table aria-label="Recent scans" variant="compact">
                <Thead>
                  <Tr>
                    <Th>Target</Th>
                    <Th>Status</Th>
                    <Th>Violations</Th>
                    <Th>Time</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {scans.map((scan) => (
                    <Tr
                      key={scan.id}
                      isClickable
                      onRowClick={() => navigate(`scans/${scan.id}`)}
                    >
                      <Td>{scan.project_name}</Td>
                      <Td><StatusBadge status={scan.status as ScanStatus} /></Td>
                      <Td>{scan.total_violations}</Td>
                      <Td>{formatRelativeTime(scan.created_at)}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            )}
          </FlexItem>
        </Flex>
      </Content>
    </Page>
  );
};
