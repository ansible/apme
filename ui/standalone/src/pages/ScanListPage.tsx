import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Content,
  Flex,
  FlexItem,
  Label,
  PageSection,
  Pagination,
  SearchInput,
  ToggleGroup,
  ToggleGroupItem,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import { Table, Thead, Tr, Th, Tbody, Td } from '@patternfly/react-table';
import {
  useScans,
  useDeleteScan,
  StatusBadge,
  formatDate,
  SEVERITY_COLORS,
  type ScanStatus,
} from '@apme/ui-shared';

const STATUS_OPTIONS = ['all', 'completed', 'failed', 'running'] as const;

export const ScanListPage: React.FC = () => {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  const { data, isLoading } = useScans({
    page,
    page_size: pageSize,
    status_filter: statusFilter === 'all' ? undefined : statusFilter,
    project: search || undefined,
  });
  const deleteScan = useDeleteScan();

  const scans = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }}>
            <FlexItem>
              <Content component="h1">Scans</Content>
            </FlexItem>
            <FlexItem>
              <Button variant="primary" onClick={() => navigate('/scans/new')}>
                New Scan
              </Button>
            </FlexItem>
          </Flex>
        </FlexItem>

        <FlexItem>
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
              <ToolbarItem>
                <ToggleGroup>
                  {STATUS_OPTIONS.map((s) => (
                    <ToggleGroupItem
                      key={s}
                      text={s.charAt(0).toUpperCase() + s.slice(1)}
                      isSelected={statusFilter === s}
                      onChange={() => { setStatusFilter(s); setPage(1); }}
                    />
                  ))}
                </ToggleGroup>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
        </FlexItem>

        <FlexItem>
          {isLoading ? (
            <Content component="p">Loading...</Content>
          ) : (
            <Table aria-label="Scan list" variant="compact">
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
                  <Th>Actions</Th>
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
                    <Td dataLabel="Date">{formatDate(scan.created_at)}</Td>
                    <Td dataLabel="Actions">
                      <Button
                        variant="link"
                        isDanger
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteScan.mutate(scan.id);
                        }}
                      >
                        Delete
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </FlexItem>

        <FlexItem>
          <Pagination
            itemCount={total}
            perPage={pageSize}
            page={page}
            onSetPage={(_e, p) => setPage(p)}
            onPerPageSelect={(_e, ps) => { setPageSize(ps); setPage(1); }}
            variant="bottom"
          />
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
