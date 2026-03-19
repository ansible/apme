import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Content, Header, Page } from '@backstage/core-components';
import {
  Pagination,
  SearchInput,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import {
  useScans,
  StatusBadge,
  formatDate,
  SEVERITY_COLORS,
  type ScanStatus,
} from '@apme/ui-shared';

export const ScanListPage: React.FC = () => {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState('');

  const { data, isLoading } = useScans({
    page,
    page_size: pageSize,
    project: search || undefined,
  });
  const scans = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <Page themeId="tool">
      <Header title="Scans" subtitle="APME scan history" />
      <Content>
        <Toolbar>
          <ToolbarContent>
            <ToolbarItem>
              <SearchInput
                placeholder="Filter by project..."
                value={search}
                onChange={(_e, val) => { setSearch(val); setPage(1); }}
                onClear={() => { setSearch(''); setPage(1); }}
              />
            </ToolbarItem>
          </ToolbarContent>
        </Toolbar>
        {isLoading ? (
          <p>Loading...</p>
        ) : (
          <Table aria-label="Scan list" variant="compact">
            <Thead>
              <Tr>
                <Th>Target</Th>
                <Th>Status</Th>
                <Th>Errors</Th>
                <Th>Warnings</Th>
                <Th>Hints</Th>
                <Th>Date</Th>
              </Tr>
            </Thead>
            <Tbody>
              {scans.map((scan) => (
                <Tr
                  key={scan.id}
                  isClickable
                  onRowClick={() => navigate(`${scan.id}`)}
                >
                  <Td>{scan.project_name}</Td>
                  <Td><StatusBadge status={scan.status as ScanStatus} /></Td>
                  <Td style={{ color: SEVERITY_COLORS.error }}>{scan.error_count}</Td>
                  <Td style={{ color: SEVERITY_COLORS.warning }}>{scan.warning_count}</Td>
                  <Td style={{ color: SEVERITY_COLORS.hint }}>{scan.hint_count}</Td>
                  <Td>{formatDate(scan.created_at)}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
        <Pagination
          itemCount={total}
          perPage={pageSize}
          page={page}
          onSetPage={(_e, p) => setPage(p)}
          onPerPageSelect={(_e, ps) => { setPageSize(ps); setPage(1); }}
          variant="bottom"
        />
      </Content>
    </Page>
  );
};
