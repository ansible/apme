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
import { listCollections } from '../services/api';
import type { CollectionSummary } from '../types/api';

type SortField = 'fqcn' | 'version' | 'project_count';

export function CollectionsPage() {
  const navigate = useNavigate();
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [sortField, setSortField] = useState<SortField>('project_count');
  const [sortAsc, setSortAsc] = useState(false);

  const fetchCollections = useCallback(() => {
    setLoading(true);
    listCollections(500, 0)
      .then((data) => setCollections(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchCollections(); }, [fetchCollections]);

  const filtered = useMemo(() => {
    let items = [...collections];
    if (searchText.trim()) {
      const q = searchText.toLowerCase();
      items = items.filter(c =>
        c.fqcn.toLowerCase().includes(q) ||
        c.version.toLowerCase().includes(q) ||
        c.source.toLowerCase().includes(q)
      );
    }
    items.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'fqcn':
          cmp = a.fqcn.localeCompare(b.fqcn);
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
  }, [collections, searchText, sortField, sortAsc]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortAsc(prev => !prev);
    } else {
      setSortField(field);
      setSortAsc(field === 'fqcn');
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
        title="Collections"
        description="Ansible collections used across all projects"
      />

      <Toolbar style={{ padding: '8px 24px' }}>
        <ToolbarContent>
          <ToolbarItem>
            <SearchInput
              placeholder="Filter by FQCN or source..."
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
          collections.length === 0 ? (
            <EmptyState>
              <EmptyStateBody>
                No collections found. Run checks on projects to collect dependency information.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <EmptyState>
              <EmptyStateBody>
                No collections match the current filter.
              </EmptyStateBody>
            </EmptyState>
          )
        ) : (
          <table className="pf-v6-c-table pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                {sortableHeader('FQCN', 'fqcn')}
                {sortableHeader('Version', 'version')}
                <th role="columnheader">Source</th>
                {sortableHeader('Projects', 'project_count')}
              </tr>
            </thead>
            <tbody>
              {filtered.map((coll) => (
                <tr
                  key={`${coll.fqcn}-${coll.version}`}
                  role="row"
                  tabIndex={0}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/collections/${encodeURIComponent(coll.fqcn)}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/collections/${encodeURIComponent(coll.fqcn)}`); }}
                >
                  <td role="cell">
                    <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                      {coll.fqcn}
                    </span>
                  </td>
                  <td role="cell">
                    <Label isCompact>{coll.version}</Label>
                  </td>
                  <td role="cell" style={{ opacity: 0.7 }}>{coll.source}</td>
                  <td role="cell">
                    <Label color="blue" isCompact>{coll.project_count}</Label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Flex justifyContent={{ default: 'justifyContentFlexEnd' }} style={{ marginTop: 8, opacity: 0.6, fontSize: 13 }}>
          <FlexItem>{filtered.length} collection{filtered.length !== 1 ? 's' : ''}</FlexItem>
        </Flex>
      </div>
    </PageLayout>
  );
}
