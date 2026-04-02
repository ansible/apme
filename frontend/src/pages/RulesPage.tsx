import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
  Label,
  SearchInput,
  Switch,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import {
  Table,
  Tbody,
  Td,
  Th,
  Thead,
  Tr,
} from '@patternfly/react-table';
import { getRuleStats, listRules, updateRuleConfig } from '../services/api';
import type { RuleDetail, RuleStats } from '../types/api';

function normalizeSeverityKey(sev: string): string {
  return sev
    .toLowerCase()
    .replace(/^severity_/, '')
    .replace(/-/g, '_');
}

/** PatternFly Label color for catalog severity strings (ADR-041 UI). */
function severityLabelColor(
  severity: string,
): 'red' | 'orange' | 'yellow' | 'blue' | 'grey' {
  const k = normalizeSeverityKey(severity);
  if (k === 'critical' || k === 'fatal' || k === 'error') return 'red';
  if (k === 'high' || k === 'very_high') return 'orange';
  if (k === 'medium' || k === 'warning' || k === 'warn') return 'yellow';
  if (k === 'low') return 'blue';
  if (k === 'info' || k === 'very_low' || k === 'hint') return 'grey';
  return 'grey';
}

function SeverityBadge({ severity }: { severity: string }) {
  const label = severity.replace(/^SEVERITY_/i, '').replace(/_/g, ' ') || severity;
  return (
    <Label color={severityLabelColor(severity)} isCompact>
      {label}
    </Label>
  );
}

export function RulesPage() {
  const [rules, setRules] = useState<RuleDetail[]>([]);
  const [stats, setStats] = useState<RuleStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const fetchRules = useCallback(() => {
    setLoading(true);
    listRules({
      category: categoryFilter || undefined,
      source: sourceFilter || undefined,
    })
      .then(setRules)
      .catch(() => setRules([]))
      .finally(() => setLoading(false));
  }, [categoryFilter, sourceFilter]);

  useEffect(() => {
    fetchRules();
  }, [fetchRules]);

  useEffect(() => {
    getRuleStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const refreshStats = useCallback(() => {
    getRuleStats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const categoryOptions = useMemo(() => {
    const fromStats = stats ? Object.keys(stats.by_category) : [];
    if (fromStats.length > 0) return [...fromStats].sort();
    const s = new Set(rules.map((r) => r.category).filter(Boolean));
    return [...s].sort();
  }, [stats, rules]);

  const sourceOptions = useMemo(() => {
    const fromStats = stats ? Object.keys(stats.by_source) : [];
    if (fromStats.length > 0) return [...fromStats].sort();
    const s = new Set(rules.map((r) => r.source).filter(Boolean));
    return [...s].sort();
  }, [stats, rules]);

  const filtered = useMemo(() => {
    if (!searchText.trim()) return rules;
    const q = searchText.toLowerCase();
    return rules.filter(
      (r) =>
        r.rule_id.toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q),
    );
  }, [rules, searchText]);

  const handleEnabledChange = useCallback(
    async (rule: RuleDetail, enabled: boolean) => {
      setUpdatingId(rule.rule_id);
      try {
        await updateRuleConfig(rule.rule_id, { enabled_override: enabled });
        setRules((prev) =>
          prev.map((r) =>
            r.rule_id === rule.rule_id ? { ...r, enabled } : r,
          ),
        );
        refreshStats();
      } catch {
        fetchRules();
      } finally {
        setUpdatingId(null);
      }
    },
    [fetchRules, refreshStats],
  );

  return (
    <PageLayout>
      <PageHeader title="Rules" />

      <Toolbar style={{ padding: '8px 24px' }}>
        <ToolbarContent>
          <ToolbarItem>
            <SearchInput
              placeholder="Search by rule ID or description..."
              value={searchText}
              onChange={(_e, v) => setSearchText(v)}
              onClear={() => setSearchText('')}
              style={{ minWidth: 280 }}
            />
          </ToolbarItem>
          <ToolbarItem>
            <FormSelect
              value={categoryFilter}
              onChange={(_e, v) => setCategoryFilter(v)}
              aria-label="Filter by category"
              style={{ minWidth: 160 }}
            >
              <FormSelectOption value="" label="All categories" />
              {categoryOptions.map((c) => (
                <FormSelectOption key={c} value={c} label={c} />
              ))}
            </FormSelect>
          </ToolbarItem>
          <ToolbarItem>
            <FormSelect
              value={sourceFilter}
              onChange={(_e, v) => setSourceFilter(v)}
              aria-label="Filter by source"
              style={{ minWidth: 160 }}
            >
              <FormSelectOption value="" label="All sources" />
              {sourceOptions.map((s) => (
                <FormSelectOption key={s} value={s} label={s} />
              ))}
            </FormSelect>
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>

      <div style={{ padding: '0 24px 24px' }}>
        {stats && (
          <Flex gap={{ default: 'gapMd' }} style={{ marginBottom: 16, opacity: 0.85, fontSize: 13 }}>
            <FlexItem>
              <strong>{stats.total}</strong> registered
            </FlexItem>
            <FlexItem>
              <strong>{stats.override_count}</strong> with overrides
            </FlexItem>
          </Flex>
        )}

        {loading ? (
          <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
        ) : filtered.length === 0 ? (
          rules.length === 0 ? (
            <EmptyState>
              <EmptyStateBody>
                No rules in the catalog yet. When the engine registers with the Gateway, rules appear here.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <EmptyState>
              <EmptyStateBody>No rules match the current filters.</EmptyStateBody>
            </EmptyState>
          )
        ) : (
          <Table aria-label="Rule catalog" variant="compact">
            <Thead>
              <Tr>
                <Th>Rule ID</Th>
                <Th>Description</Th>
                <Th>Source</Th>
                <Th>Category</Th>
                <Th>Default severity</Th>
                <Th>Effective severity</Th>
                <Th>Status</Th>
                <Th>Enforced</Th>
              </Tr>
            </Thead>
            <Tbody>
              {filtered.map((rule) => (
                <Tr key={rule.rule_id}>
                  <Td dataLabel="Rule ID">
                    <span
                      style={{
                        fontFamily: 'var(--pf-t--global--font--family--mono)',
                        fontSize: 13,
                        fontWeight: 600,
                      }}
                    >
                      {rule.rule_id}
                    </span>
                  </Td>
                  <Td dataLabel="Description">
                    <span
                      title={rule.description}
                      style={{
                        display: 'block',
                        maxWidth: 360,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {rule.description || '—'}
                    </span>
                  </Td>
                  <Td dataLabel="Source">{rule.source}</Td>
                  <Td dataLabel="Category">{rule.category}</Td>
                  <Td dataLabel="Default severity">
                    <SeverityBadge severity={rule.default_severity} />
                  </Td>
                  <Td dataLabel="Effective severity">
                    <SeverityBadge severity={rule.effective_severity} />
                  </Td>
                  <Td dataLabel="Status">
                    <Switch
                      id={`rule-enabled-${rule.rule_id}`}
                      aria-label={`Enable ${rule.rule_id}`}
                      isChecked={rule.enabled}
                      isDisabled={updatingId === rule.rule_id}
                      onChange={(_event, checked) => {
                        void handleEnabledChange(rule, checked);
                      }}
                    />
                  </Td>
                  <Td dataLabel="Enforced">
                    {rule.enforced ? (
                      <Label color="green" isCompact>Yes</Label>
                    ) : (
                      <Label color="grey" variant="outline" isCompact>No</Label>
                    )}
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}

        <Flex justifyContent={{ default: 'justifyContentFlexEnd' }} style={{ marginTop: 8, opacity: 0.6, fontSize: 13 }}>
          <FlexItem>
            {filtered.length} rule{filtered.length !== 1 ? 's' : ''} shown
          </FlexItem>
        </Flex>
      </div>
    </PageLayout>
  );
}
