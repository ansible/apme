import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  Label,
  SearchInput,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import {
  SortAmountDownIcon,
  SortAmountUpIcon,
} from '@patternfly/react-icons';
import { listPythonPackages } from '../services/api';
import type { PythonPackageSummary } from '../types/api';

type SortField = 'name' | 'version' | 'project_count';

export function PythonPackagesPage() {
  const navigate = useNavigate();
  const [packages, setPackages] = useState<PythonPackageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [sortField, setSortField] = useState<SortField>('project_count');
  const [sortAsc, setSortAsc] = useState(false);

  const fetchPackages = useCallback(() => {
    setLoading(true);
    listPythonPackages(500, 0)
      .then((data) => setPackages(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchPackages(); }, [fetchPackages]);

  const filtered = useMemo(() => {
    let items = [...packages];
    if (searchText.trim()) {
      const q = searchText.toLowerCase();
      items = items.filter(p =>
        p.name.toLowerCase().includes(q) ||
        p.version.toLowerCase().includes(q)
      );
    }
    items.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'name':
          cmp = a.name.localeCompare(b.name);
          break;
        case 'version':
          cmp = a.version.localeCompare(b.version);
          break;
        case 'project_count':
          cmp = a.project_count - b.project_count;
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
    return items;
  }, [packages, searchText, sortField, sortAsc]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortAsc(prev => !prev);
    } else {
      setSortField(field);
      setSortAsc(field === 'name');
    }
  };

  const SortIcon = sortAsc ? SortAmountUpIcon : SortAmountDownIcon;

  const sortableHeader = (label: string, field: SortField) => {
    const active = sortField === field;
    const ariaSortValue = active ? (sortAsc ? 'ascending' : 'descending') : undefined;
    return (
      <th
        role="columnheader"
        aria-sort={ariaSortValue}
        tabIndex={0}
        style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
        onClick={() => handleSort(field)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort(field); } }}
      >
        {label}
        {active && (
          <SortIcon style={{ marginLeft: 4, fontSize: 12, opacity: 0.7 }} />
        )}
      </th>
    );
  };

  return (
    <PageLayout>
      <PageHeader
        title="Python Packages"
        description="Python packages used across all projects"
      />

      <Toolbar style={{ padding: '8px 24px' }}>
        <ToolbarContent>
          <ToolbarItem>
            <SearchInput
              placeholder="Filter by package name..."
              value={searchText}
              onChange={(_e, v) => setSearchText(v)}
              onClear={() => setSearchText('')}
              style={{ minWidth: 280 }}
            />
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>

      <div style={{ padding: '0 24px 24px' }}>
        {loading ? (
          <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
        ) : filtered.length === 0 ? (
          packages.length === 0 ? (
            <EmptyState>
              <EmptyStateBody>
                No Python packages found. Run checks on projects to collect dependency information.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <EmptyState>
              <EmptyStateBody>
                No packages match the current filter.
              </EmptyStateBody>
            </EmptyState>
          )
        ) : (
          <table className="pf-v6-c-table pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                {sortableHeader('Package', 'name')}
                {sortableHeader('Version', 'version')}
                {sortableHeader('Projects', 'project_count')}
              </tr>
            </thead>
            <tbody>
              {filtered.map((pkg) => (
                <tr
                  key={`${pkg.name}-${pkg.version}`}
                  role="row"
                  tabIndex={0}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/python-packages/${encodeURIComponent(pkg.name)}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/python-packages/${encodeURIComponent(pkg.name)}`); }}
                >
                  <td role="cell">
                    <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                      {pkg.name}
                    </span>
                  </td>
                  <td role="cell">
                    <Label isCompact>{pkg.version}</Label>
                  </td>
                  <td role="cell">
                    <Label color="blue" isCompact>{pkg.project_count}</Label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Flex justifyContent={{ default: 'justifyContentFlexEnd' }} style={{ marginTop: 8, opacity: 0.6, fontSize: 13 }}>
          <FlexItem>{filtered.length} package{filtered.length !== 1 ? 's' : ''}</FlexItem>
        </Flex>
      </div>
    </PageLayout>
  );
}
