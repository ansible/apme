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
import { useApmeApi } from '../api/apmeApiAdapter';
import { deleteRuleConfig, getRule, getRuleStats, listRules, updateRuleConfig } from '../services/api';
import type { RuleDetail, RuleStats } from '../types/api';
import { severityClass, severityLabel, SEVERITY_INT_OPTIONS, SEVERITY_INT_TO_API, SEVERITY_LABELS } from '../components/severity';

const SEVERITY_OPTIONS = SEVERITY_INT_OPTIONS;

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
  const api = useApmeApi();
  const [rules, setRules] = useState<RuleDetail[]>([]);
  const [stats, setStats] = useState<RuleStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [updatingIds, setUpdatingIds] = useState<Set<string>>(new Set());
  const [selectedRule, setSelectedRule] = useState<RuleDetail | null>(null);
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);
  const [detailReload, setDetailReload] = useState(0);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailRequestRef = useRef(0);
  const listRequestRef = useRef(0);
  const statsRequestRef = useRef(0);
  const loadingRequestRef = useRef(0);

  const startUpdating = useCallback((id: string) => {
    setUpdatingIds((prev) => new Set(prev).add(id));
  }, []);

  const stopUpdating = useCallback((id: string) => {
    setUpdatingIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
  }, []);

  const fetchRules = useCallback(() => {
    const requestId = ++listRequestRef.current;
    const loadingId = ++loadingRequestRef.current;
    setLoading(true);
    listRules({
      category: categoryFilter || undefined,
      source: sourceFilter || undefined,
    }, api)
      .then((data) => { if (listRequestRef.current === requestId) setRules(data); })
      .catch(() => { if (listRequestRef.current === requestId) setRules([]); })
      .finally(() => {
        if (loadingRequestRef.current === loadingId) setLoading(false);
      });
  }, [categoryFilter, sourceFilter, api]);

  const refreshRules = useCallback(() => {
    const requestId = ++listRequestRef.current;
    listRules({
      category: categoryFilter || undefined,
      source: sourceFilter || undefined,
    }, api)
      .then((data) => { if (listRequestRef.current === requestId) setRules(data); })
      .catch(() => {});
  }, [categoryFilter, sourceFilter, api]);

  useEffect(() => {
    fetchRules();
    return () => {
      listRequestRef.current += 1;
      loadingRequestRef.current += 1;
    };
  }, [fetchRules]);

  useEffect(() => {
    const requestId = ++statsRequestRef.current;
    setStats(null);
    getRuleStats(api)
      .then((data) => {
        if (statsRequestRef.current === requestId) setStats(data);
      })
      .catch(() => {
        if (statsRequestRef.current === requestId) setStats(null);
      });
    return () => { statsRequestRef.current += 1; };
  }, [api]);

  const refreshStats = useCallback(() => {
    const requestId = ++statsRequestRef.current;
    getRuleStats(api)
      .then((data) => {
        if (statsRequestRef.current === requestId) setStats(data);
      })
      .catch(() => {});
  }, [api]);

  const openRuleDetail = useCallback((ruleId: string) => {
    const local = rules.find((r) => r.rule_id === ruleId) ?? null;
    setSelectedRule(local);
    setSelectedRuleId(ruleId);
    setDetailReload((value) => value + 1);
  }, [rules]);

  useEffect(() => {
    if (!selectedRuleId) return;
    let active = true;
    const requestId = ++detailRequestRef.current;
    setSelectedRule(null);
    setDetailLoading(true);
    getRule(selectedRuleId, api)
      .then((data) => {
        if (active && detailRequestRef.current === requestId) setSelectedRule(data);
      })
      .catch(() => {
        if (active && detailRequestRef.current === requestId) setSelectedRule(null);
      })
      .finally(() => {
        if (active && detailRequestRef.current === requestId) setDetailLoading(false);
      });
    return () => {
      active = false;
      detailRequestRef.current += 1;
    };
  }, [api, selectedRuleId, detailReload]);

  const handleResetOverride = useCallback(async (ruleId: string) => {
    startUpdating(ruleId);
    try {
      await deleteRuleConfig(ruleId, api);
      refreshRules();
      refreshStats();
      if (selectedRuleId === ruleId) {
        openRuleDetail(ruleId);
      }
    } catch {
      // 404 means no override existed
    } finally {
      stopUpdating(ruleId);
    }
  }, [selectedRuleId, refreshRules, refreshStats, openRuleDetail, startUpdating, stopUpdating, api]);

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
      const prev = { enabled: rule.enabled, has_override: rule.has_override };
      const patch = { enabled, has_override: true };
      setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...patch } : r));
      setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...patch } : cur);
      startUpdating(rule.rule_id);
      try {
        await updateRuleConfig(rule.rule_id, { enabled_override: enabled }, api);
        refreshStats();
      } catch {
        setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...prev } : r));
        setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...prev } : cur);
      } finally {
        stopUpdating(rule.rule_id);
      }
    },
    [refreshStats, startUpdating, stopUpdating, api],
  );

  const handleSeverityChange = useCallback(
    async (rule: RuleDetail, severityInt: number) => {
      if (severityInt === rule.effective_severity_int) return;
      const prev = { effective_severity_int: rule.effective_severity_int, effective_severity: rule.effective_severity, has_override: rule.has_override };
      const nextSlug = SEVERITY_INT_TO_API[severityInt] ?? 'medium';
      const patch = { effective_severity_int: severityInt, effective_severity: SEVERITY_LABELS[nextSlug] ?? 'Medium', has_override: true };
      setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...patch } : r));
      setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...patch } : cur);
      startUpdating(rule.rule_id);
      try {
        await updateRuleConfig(rule.rule_id, { severity_override: severityInt }, api);
        refreshStats();
      } catch {
        setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...prev } : r));
        setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...prev } : cur);
      } finally {
        stopUpdating(rule.rule_id);
      }
    },
    [refreshStats, startUpdating, stopUpdating, api],
  );

  const handleEnforcedChange = useCallback(
    async (rule: RuleDetail, enforced: boolean) => {
      const prev = { enforced: rule.enforced, has_override: rule.has_override };
      const patch = { enforced, has_override: true };
      setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...patch } : r));
      setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...patch } : cur);
      startUpdating(rule.rule_id);
      try {
        await updateRuleConfig(rule.rule_id, { enforced }, api);
        refreshStats();
      } catch {
        setRules((cur) => cur.map((r) => r.rule_id === rule.rule_id ? { ...r, ...prev } : r));
        setSelectedRule((cur) => cur?.rule_id === rule.rule_id ? { ...cur, ...prev } : cur);
      } finally {
        stopUpdating(rule.rule_id);
      }
    },
    [refreshStats, startUpdating, stopUpdating, api],
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
                    <FormSelect
                      value={rule.effective_severity_int}
                      onChange={(_e, v) => {
                        void handleSeverityChange(rule, Number(v));
                      }}
                      aria-label={`Severity for ${rule.rule_id}`}
                      isDisabled={updatingIds.has(rule.rule_id)}
                      style={{ minWidth: 110, maxWidth: 130 }}
                    >
                      {SEVERITY_OPTIONS.map((opt) => (
                        <FormSelectOption key={opt.value} value={opt.value} label={opt.label} />
                      ))}
                    </FormSelect>
                  </Td>
                  <Td dataLabel="Status">
                    <Switch
                      id={`rule-enabled-${rule.rule_id}`}
                      aria-label={`Enable ${rule.rule_id}`}
                      isChecked={rule.enabled}
                      isDisabled={updatingIds.has(rule.rule_id)}
                      onChange={(_event, checked) => {
                        void handleEnabledChange(rule, checked);
                      }}
                    />
                  </Td>
                  <Td dataLabel="Enforced">
                    <Switch
                      id={`rule-enforced-${rule.rule_id}`}
                      aria-label={`Enforce ${rule.rule_id}`}
                      isChecked={rule.enforced}
                      isDisabled={updatingIds.has(rule.rule_id)}
                      onChange={(_event, checked) => {
                        void handleEnforcedChange(rule, checked);
                      }}
                    />
                  </Td>
                  <Td dataLabel="Actions">
                    {rule.has_override && (
                      <Button
                        variant="link"
                        isInline
                        size="sm"
                        isDisabled={updatingIds.has(rule.rule_id)}
                        onClick={() => handleResetOverride(rule.rule_id)}
                      >
                        Reset
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
          onClose={() => {
            detailRequestRef.current++;
            setSelectedRuleId(null);
            setSelectedRule(null);
            setDetailLoading(false);
          }}
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
                        <FormSelect
                          value={selectedRule.effective_severity_int}
                          onChange={(_e, v) => {
                            void handleSeverityChange(selectedRule, Number(v));
                          }}
                          aria-label={`Override severity for ${selectedRule.rule_id}`}
                          isDisabled={updatingIds.has(selectedRule.rule_id)}
                          style={{ maxWidth: 160 }}
                        >
                          {SEVERITY_OPTIONS.map((opt) => (
                            <FormSelectOption key={opt.value} value={opt.value} label={opt.label} />
                          ))}
                        </FormSelect>
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Enabled</DescriptionListTerm>
                      <DescriptionListDescription>
                        <Switch
                          id="detail-rule-enabled"
                          aria-label={`Enable ${selectedRule.rule_id}`}
                          isChecked={selectedRule.enabled}
                          isDisabled={updatingIds.has(selectedRule.rule_id)}
                          onChange={(_event, checked) => {
                            void handleEnabledChange(selectedRule, checked);
                          }}
                        />
                      </DescriptionListDescription>
                    </DescriptionListGroup>
                    <DescriptionListGroup>
                      <DescriptionListTerm>Enforced</DescriptionListTerm>
                      <DescriptionListDescription>
                        <Switch
                          id="detail-rule-enforced"
                          aria-label={`Enforce ${selectedRule.rule_id}`}
                          isChecked={selectedRule.enforced}
                          isDisabled={updatingIds.has(selectedRule.rule_id)}
                          onChange={(_event, checked) => {
                            void handleEnforcedChange(selectedRule, checked);
                          }}
                        />
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
                isDisabled={updatingIds.has(selectedRule.rule_id)}
                onClick={() => handleResetOverride(selectedRule.rule_id)}
              >
                Reset Override
              </Button>
            )}
            <Button variant="link" onClick={() => {
              detailRequestRef.current++;
              setSelectedRuleId(null);
              setSelectedRule(null);
              setDetailLoading(false);
            }}>Close</Button>
          </ModalFooter>
        </Modal>
      )}
    </PageLayout>
  );
}
