import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Button,
  Card,
  CardBody,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
  Label,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
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
import { deleteRuleConfig, getRule, getRuleStats, listRules, updateRuleConfig } from '../services/api';
import type { RuleDetail, RuleStats } from '../types/api';
import { severityClass, severityLabel } from '../components/severity';

function catalogSeverityToApi(sev: string): string {
  return sev.replace(/^SEVERITY_/i, '').toLowerCase();
}

function SeverityBadge({ severity }: { severity: string }) {
  const apiLevel = catalogSeverityToApi(severity);
  const cls = severityClass(apiLevel);
  const label = severityLabel(apiLevel);
  return <span className={`apme-severity ${cls}`}>{label}</span>;
}

export function RulesPage() {
  const [rules, setRules] = useState<RuleDetail[]>([]);
  const [stats, setStats] = useState<RuleStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [selectedRule, setSelectedRule] = useState<RuleDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [resettingId, setResettingId] = useState<string | null>(null);
  const detailRequestRef = useRef(0);

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

  const openRuleDetail = useCallback((ruleId: string) => {
    const token = ++detailRequestRef.current;
    const local = rules.find((r) => r.rule_id === ruleId) ?? null;
    setSelectedRule(local);
    setDetailLoading(true);
    getRule(ruleId)
      .then((data) => {
        if (detailRequestRef.current === token) setSelectedRule(data);
      })
      .catch(() => {})
      .finally(() => {
        if (detailRequestRef.current === token) setDetailLoading(false);
      });
  }, [rules]);

  const handleResetOverride = useCallback(async (ruleId: string) => {
    setResettingId(ruleId);
    try {
      await deleteRuleConfig(ruleId);
      fetchRules();
      refreshStats();
      if (selectedRule?.rule_id === ruleId) {
        openRuleDetail(ruleId);
      }
    } catch {
      // 404 means no override existed
    } finally {
      setResettingId(null);
    }
  }, [selectedRule, fetchRules, refreshStats, openRuleDetail]);

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
                <Th>Actions</Th>
              </Tr>
            </Thead>
            <Tbody>
              {filtered.map((rule) => (
                <Tr key={rule.rule_id}>
                  <Td dataLabel="Rule ID">
                    <Button
                      variant="link"
                      isInline
                      onClick={() => openRuleDetail(rule.rule_id)}
                      style={{
                        fontFamily: 'var(--pf-t--global--font--family--mono)',
                        fontSize: 13,
                        fontWeight: 600,
                      }}
                    >
                      {rule.rule_id}
                    </Button>
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
                  <Td dataLabel="Actions">
                    {rule.has_override && (
                      <Button
                        variant="link"
                        isInline
                        size="sm"
                        isDisabled={resettingId === rule.rule_id}
                        onClick={() => handleResetOverride(rule.rule_id)}
                      >
                        {resettingId === rule.rule_id ? 'Resetting...' : 'Reset'}
                      </Button>
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

      {selectedRule && (
        <Modal
          isOpen
          onClose={() => { detailRequestRef.current++; setSelectedRule(null); }}
          variant="medium"
        >
          <ModalHeader title={`Rule: ${selectedRule.rule_id}`} />
          <ModalBody>
            {detailLoading ? (
              <div style={{ padding: 24, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
            ) : (
              <Card>
                <CardBody>
                  <DescriptionList isHorizontal>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Rule ID</DescriptionListTerm>
                      <DescriptionListDescription>
                        <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontWeight: 600 }}>
                          {selectedRule.rule_id}
                        </span>
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Description</DescriptionListTerm>
                      <DescriptionListDescription>{selectedRule.description || '—'}</DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Category</DescriptionListTerm>
                      <DescriptionListDescription>{selectedRule.category}</DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Source</DescriptionListTerm>
                      <DescriptionListDescription>{selectedRule.source}</DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Scope</DescriptionListTerm>
                      <DescriptionListDescription>{selectedRule.scope}</DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Default Severity</DescriptionListTerm>
                      <DescriptionListDescription>
                        <SeverityBadge severity={selectedRule.default_severity} />
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Effective Severity</DescriptionListTerm>
                      <DescriptionListDescription>
                        <SeverityBadge severity={selectedRule.effective_severity} />
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Enabled</DescriptionListTerm>
                      <DescriptionListDescription>
                        <Label color={selectedRule.enabled ? 'green' : 'grey'} isCompact>
                          {selectedRule.enabled ? 'Yes' : 'No'}
                        </Label>
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Enforced</DescriptionListTerm>
                      <DescriptionListDescription>
                        <Label color={selectedRule.enforced ? 'green' : 'grey'} isCompact>
                          {selectedRule.enforced ? 'Yes' : 'No'}
                        </Label>
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Has Override</DescriptionListTerm>
                      <DescriptionListDescription>
                        {selectedRule.has_override ? (
                          <Label color="blue" isCompact>Yes</Label>
                        ) : (
                          <Label color="grey" variant="outline" isCompact>No</Label>
                        )}
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Registered</DescriptionListTerm>
                      <DescriptionListDescription>
                        {new Date(selectedRule.registered_at).toLocaleString()}
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                  </DescriptionList>
                </CardBody>
              </Card>
            )}
          </ModalBody>
          <ModalFooter>
            {selectedRule.has_override && (
              <Button
                variant="warning"
                isDisabled={resettingId === selectedRule.rule_id}
                onClick={() => handleResetOverride(selectedRule.rule_id)}
              >
                {resettingId === selectedRule.rule_id ? 'Resetting...' : 'Reset Override'}
              </Button>
            )}
            <Button variant="link" onClick={() => { detailRequestRef.current++; setSelectedRule(null); }}>Close</Button>
          </ModalFooter>
        </Modal>
      )}
    </PageLayout>
  );
}
