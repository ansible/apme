import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Badge,
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
  ExclamationCircleIcon,
  SortAmountDownIcon,
  SortAmountUpIcon,
} from '@patternfly/react-icons';
import { listPythonPackages, getDepHealthSummary } from '../services/api';
import type { PythonPackageSummary, PythonCveSummary } from '../types/api';
import { severityClass } from '../components/severity';

type SortField = 'name' | 'version' | 'project_count' | 'cves';

interface PkgCveInfo {
  count: number;
  hasCritical: boolean;
}

function buildPkgCveMap(cveList: PythonCveSummary[]): Map<string, PkgCveInfo> {
  const map = new Map<string, PkgCveInfo>();
  for (const cve of cveList) {
    const match = cve.message.match(/^([a-zA-Z0-9_.-]+)==/);
    if (!match?.[1]) continue;
    const pkg = match[1].toLowerCase();
    const existing = map.get(pkg) ?? { count: 0, hasCritical: false };
    existing.count += cve.occurrence_count;
    const cls = severityClass(cve.level);
    if (cls === 'critical' || cls === 'error' || cls === 'high') {
      existing.hasCritical = true;
    }
    map.set(pkg, existing);
  }
  return map;
}

export function PythonPackagesPage() {
  const navigate = useNavigate();
  const [packages, setPackages] = useState<PythonPackageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [sortField, setSortField] = useState<SortField>('project_count');
  const [sortAsc, setSortAsc] = useState(false);
  const [cveList, setCveList] = useState<PythonCveSummary[]>([]);

  const fetchPackages = useCallback(() => {
    setLoading(true);
    Promise.all([
      listPythonPackages(500, 0),
      getDepHealthSummary().catch(() => ({ collection_findings: [], python_cves: [], suppressed_count: 0 })),
    ])
      .then(([data, health]) => {
        setPackages(data);
        setCveList(health.python_cves);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchPackages(); }, [fetchPackages]);

  const pkgCveMap = useMemo(() => buildPkgCveMap(cveList), [cveList]);

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
        case 'cves':
          cmp = (pkgCveMap.get(a.name.toLowerCase())?.count ?? 0) - (pkgCveMap.get(b.name.toLowerCase())?.count ?? 0);
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
    return items;
  }, [packages, searchText, sortField, sortAsc, pkgCveMap]);

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

  const cvesBySeverity = useMemo(() => {
    const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0, total: 0 };
    for (const cve of cveList) {
      counts.total += cve.occurrence_count;
      const cls = severityClass(cve.level);
      if (cls === 'critical' || cls === 'error') counts.critical += cve.occurrence_count;
      else if (cls === 'high') counts.high += cve.occurrence_count;
      else if (cls === 'medium') counts.medium += cve.occurrence_count;
      else if (cls === 'low') counts.low += cve.occurrence_count;
      else counts.info += cve.occurrence_count;
    }
    return counts;
  }, [cveList]);

  return (
    <PageLayout>
      <PageHeader
        title="Python Packages"
        description="Python packages used across all projects"
      />

      {cveList.length > 0 && (
        <div style={{ padding: '0 24px 12px' }}>
          <div style={{
            padding: '12px 16px',
            borderRadius: 8,
            background: 'var(--pf-t--global--background--color--secondary--default)',
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            flexWrap: 'wrap',
          }}>
            <Flex alignItems={{ default: 'alignItemsCenter' }} style={{ gap: 8 }}>
              <FlexItem>
                <ExclamationCircleIcon style={{ color: 'var(--pf-t--global--color--status--danger--default)' }} />
              </FlexItem>
              <FlexItem style={{ fontWeight: 600 }}>
                {cveList.length} CVE{cveList.length !== 1 ? 's' : ''} detected across {cvesBySeverity.total} occurrence{cvesBySeverity.total !== 1 ? 's' : ''}
              </FlexItem>
            </Flex>
            <Flex style={{ gap: 12 }}>
              {cvesBySeverity.critical > 0 && (
                <FlexItem>
                  <Badge style={{ background: 'var(--pf-t--global--color--status--danger--default)', color: '#fff' }}>
                    {cvesBySeverity.critical} Critical
                  </Badge>
                </FlexItem>
              )}
              {cvesBySeverity.high > 0 && (
                <FlexItem>
                  <Badge style={{ background: 'var(--pf-t--global--color--status--warning--default)' }}>
                    {cvesBySeverity.high} High
                  </Badge>
                </FlexItem>
              )}
              {cvesBySeverity.medium > 0 && (
                <FlexItem><Badge isRead>{cvesBySeverity.medium} Medium</Badge></FlexItem>
              )}
              {cvesBySeverity.low > 0 && (
                <FlexItem><Badge isRead>{cvesBySeverity.low} Low</Badge></FlexItem>
              )}
            </Flex>
          </div>

          <table className="pf-v6-c-table pf-m-compact" role="grid" style={{ marginTop: 12 }}>
            <thead>
              <tr role="row">
                <th role="columnheader" style={{ width: 90 }}>Severity</th>
                <th role="columnheader" style={{ width: 160 }}>CVE / Rule</th>
                <th role="columnheader">Details</th>
                <th role="columnheader" style={{ width: 100 }}>Occurrences</th>
              </tr>
            </thead>
            <tbody>
              {cveList.map((cve, i) => {
                const cls = severityClass(cve.level);
                const cveId = cve.message.match(/CVE-\d{4}-\d+/)?.[0] ?? cve.rule_id;
                return (
                  <tr key={`${cve.rule_id}-${i}`} role="row">
                    <td role="cell">
                      <Label
                        color={cls === 'critical' || cls === 'error' ? 'red' : cls === 'high' ? 'orange' : cls === 'medium' ? 'yellow' : 'blue'}
                        isCompact
                      >
                        {cls.toUpperCase()}
                      </Label>
                    </td>
                    <td role="cell">
                      <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                        {cveId}
                      </span>
                    </td>
                    <td role="cell" style={{ fontSize: 13 }}>{cve.message}</td>
                    <td role="cell">
                      <Badge isRead>{cve.occurrence_count}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

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
                {sortableHeader('CVEs', 'cves')}
              </tr>
            </thead>
            <tbody>
              {filtered.map((pkg) => {
                const cveInfo = pkgCveMap.get(pkg.name.toLowerCase());
                return (
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
                  <td role="cell">
                    {cveInfo ? (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        {cveInfo.hasCritical && <ExclamationCircleIcon style={{ color: 'var(--pf-t--global--color--status--danger--default)' }} />}
                        <Badge isRead={!cveInfo.hasCritical}>{cveInfo.count}</Badge>
                      </span>
                    ) : (
                      <span style={{ opacity: 0.4 }}>&mdash;</span>
                    )}
                  </td>
                </tr>
                );
              })}
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
