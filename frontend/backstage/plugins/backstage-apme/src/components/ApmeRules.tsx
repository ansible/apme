/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeDialogFooter as DialogFooter } from "./layout";
import { ApmeButton as Button, ApmeDialog as Dialog } from "./layout";
import { useCallback, useState } from "react";
import {
  Alert,
  Cell,
  Column,
  DialogBody,
  DialogHeader,
  Flex,
  Row,
  SearchField,
  Select,
  Switch,
  TableBody,
  TableHeader,
  Text,
} from "@backstage/ui";
import {
  deleteRuleConfig,
  listRules,
  updateRuleConfig,
  type RuleDetail,
  type RuleOverrideRequest,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { ApmeTable, SectionHeader } from "./layout";
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";
import { SEVERITY_INT_OPTIONS } from "@apme/ui-workflow/headless";

export interface ApmeRulesProps {
  adapter: ApmeApiAdapter;
  /** Host permission decision. Backend authorization is still required. */
  canManage?: boolean;
}

export function ApmeRules({ adapter, canManage = false }: ApmeRulesProps) {
  const load = useCallback(() => listRules(undefined, adapter), [adapter]);
  const state = useLoad(load);
  const mutation = useMutation(adapter);
  async function configure(rule: RuleDetail, patch?: RuleOverrideRequest) {
    if (!canManage) return;
    await mutation.run(
      () =>
        patch
          ? updateRuleConfig(rule.rule_id, patch, adapter)
          : deleteRuleConfig(rule.rule_id, adapter),
      state.reload,
    );
  }
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [category, setCategory] = useState("all");
  const [source, setSource] = useState("all");
  const [selectedId, setSelectedId] = useState<string>();
  const selected = state.value?.find((rule) => rule.rule_id === selectedId);
  const query = search.trim().toLowerCase();
  const rules =
    state.value?.filter(
      (rule) =>
        (category === "all" || rule.category === category) &&
        (source === "all" || rule.source === source) &&
        `${rule.rule_id} ${rule.description} ${rule.category} ${rule.source} ${rule.effective_severity}`
          .toLowerCase()
          .includes(query),
    ) ?? [];
  // Clamp when a refresh or an override changes the matching result count.
  const page = Math.min(
    offset,
    Math.max(0, Math.ceil(rules.length / 25) - 1) * 25,
  );
  return (
    <Flex className="apme-ui" direction="column" gap="4">
      <SectionHeader
        title="Rule catalog"
        description="Browse checks and manage rule overrides."
        actions={
          <Button
            variant="secondary"
            onPress={state.reload}
            isDisabled={state.loading && !state.value}
          >
            Refresh rules
          </Button>
        }
      />
      <DataStatus {...state} subject="rules" />
      {mutation.error && (
        <Alert
          status="danger"
          title="Could not update rule"
          description={mutation.error}
        />
      )}
      <Text color="secondary">
        {canManage
          ? "Rule changes save immediately and apply to future checks in all projects."
          : "Rule configuration is read-only for this account."}
      </Text>
      {state.value && (
        <>
          <div className="apme-filters">
            <SearchField
              label="Search rules"
              placeholder="Search by ID, description or severity"
              value={search}
              onChange={(value) => {
                setSearch(value);
                setOffset(0);
              }}
            />

            <Select
              selectionMode="single"
              label="Category"
              value={category}
              options={[
                { id: "all", label: "All categories" },
                ...[
                  ...new Set(
                    state.value.map((rule) => rule.category).filter(Boolean),
                  ),
                ]
                  .sort()
                  .map((value) => ({ id: value, label: value })),
              ]}
              onChange={(value) => {
                setCategory(String(value));
                setOffset(0);
              }}
            />
            <Select
              selectionMode="single"
              label="Source"
              value={source}
              options={[
                { id: "all", label: "All sources" },
                ...[
                  ...new Set(
                    state.value.map((rule) => rule.source).filter(Boolean),
                  ),
                ]
                  .sort()
                  .map((value) => ({ id: value, label: value })),
              ]}
              onChange={(value) => {
                setSource(String(value));
                setOffset(0);
              }}
            />
          </div>
          <Text color="secondary">
            {rules.length} matching rules ·{" "}
            {state.value.filter((rule) => rule.has_override).length} overrides
          </Text>
          <ApmeTable
            className="apme-rules-table"
            aria-label="APME rule catalog"
          >
            <TableHeader>
              <Column isRowHeader style={{ width: 80 }}>
                Rule
              </Column>
              <Column>Description</Column>
              <Column style={{ width: 85 }}>Category</Column>
              <Column style={{ width: 85 }}>Source</Column>
              <Column style={{ width: 160 }}>Severity</Column>
              <Column style={{ width: 135 }}>Status</Column>
              <Column style={{ width: 130 }}>Enforced</Column>
              <Column style={{ width: 110 }}>Overrides</Column>
            </TableHeader>
            <TableBody
              items={rules.slice(page, page + 25)}
              renderEmptyState={() =>
                query ? "No matching rules." : "No rules registered."
              }
            >
              {(rule) => (
                <Row id={rule.rule_id}>
                  <Cell>
                    <Button
                      variant="tertiary"
                      className="apme-table-link"
                      onPress={() => setSelectedId(rule.rule_id)}
                    >
                      {rule.rule_id}
                    </Button>
                  </Cell>
                  <Cell>{rule.description}</Cell>
                  <Cell>{rule.category}</Cell>
                  <Cell>{rule.source || "Not reported"}</Cell>
                  <Cell>
                    {canManage ? (
                      <RuleSeverity
                        rule={rule}
                        saving={mutation.saving}
                        onChange={(value) =>
                          void configure(rule, { severity_override: value })
                        }
                      />
                    ) : (
                      rule.effective_severity
                    )}
                  </Cell>
                  <Cell>
                    {canManage ? (
                      <Switch
                        label={rule.enabled ? "Enabled" : "Disabled"}
                        aria-label={`Enable ${rule.rule_id}`}
                        isSelected={rule.enabled}
                        isDisabled={mutation.saving}
                        onChange={(enabled) =>
                          void configure(rule, { enabled_override: enabled })
                        }
                      />
                    ) : rule.enabled ? (
                      "Enabled"
                    ) : (
                      "Disabled"
                    )}
                  </Cell>
                  <Cell>
                    {canManage ? (
                      <Switch
                        label={rule.enforced ? "Enforced" : "Off"}
                        aria-label={`Enforce ${rule.rule_id} despite inline ignores`}
                        isSelected={rule.enforced}
                        isDisabled={mutation.saving}
                        onChange={(enforced) =>
                          void configure(rule, { enforced })
                        }
                      />
                    ) : rule.enforced ? (
                      "Yes"
                    ) : (
                      "No"
                    )}
                  </Cell>
                  <Cell>
                    {rule.has_override ? (
                      canManage ? (
                        <Button
                          variant="tertiary"
                          aria-label={`Reset overrides for ${rule.rule_id}`}
                          isDisabled={mutation.saving}
                          onPress={() => void configure(rule)}
                        >
                          Reset
                        </Button>
                      ) : (
                        "Yes"
                      )
                    ) : (
                      "Default"
                    )}
                  </Cell>
                </Row>
              )}
            </TableBody>
          </ApmeTable>
          <PageControls
            offset={page}
            size={25}
            hasNext={page + 25 < rules.length}
            busy={state.loading}
            onChange={setOffset}
            subject="rules"
          />
        </>
      )}
      {selected && (
        <RuleDetails
          key={selected.rule_id}
          rule={selected}
          adapter={adapter}
          canManage={canManage}
          onClose={() => setSelectedId(undefined)}
          onSaved={() => {
            setSelectedId(undefined);
            state.reload();
          }}
        />
      )}
    </Flex>
  );
}

function RuleSeverity({
  rule,
  saving,
  onChange,
}: {
  rule: RuleDetail;
  saving: boolean;
  onChange: (value: number) => void;
}) {
  const knownSeverity = SEVERITY_INT_OPTIONS.some(
    (option) => option.value === rule.effective_severity_int,
  );
  return (
    <Select
      className="apme-rule-severity"
      selectionMode="single"
      aria-label={`Severity for ${rule.rule_id}`}
      value={knownSeverity ? String(rule.effective_severity_int) : ""}
      options={[
        ...(!knownSeverity
          ? [
              {
                id: "",
                label: `Choose severity (current: ${rule.effective_severity || "unknown"})`,
              },
            ]
          : []),
        ...SEVERITY_INT_OPTIONS.map(({ value, label }) => ({
          id: String(value),
          label,
        })),
      ]}
      isDisabled={saving}
      onChange={(value) => {
        const severity = Number(value);
        if (
          SEVERITY_INT_OPTIONS.some((option) => option.value === severity) &&
          severity !== rule.effective_severity_int
        )
          onChange(severity);
      }}
    />
  );
}

function RuleDetails({
  rule,
  adapter,
  canManage,
  onClose,
  onSaved,
}: {
  rule: RuleDetail;
  adapter: ApmeApiAdapter;
  canManage: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { saving, error, run } = useMutation(adapter);
  async function save(patch?: RuleOverrideRequest) {
    if (!canManage) return;
    await run(
      () =>
        patch
          ? updateRuleConfig(rule.rule_id, patch, adapter)
          : deleteRuleConfig(rule.rule_id, adapter),
      onSaved,
    );
  }
  return (
    <Dialog
      isOpen
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogHeader>{rule.rule_id}</DialogHeader>
      <DialogBody>
        <Flex direction="column" gap="4">
          <Text>{rule.description}</Text>
          <Text>
            Category: {rule.category} · Source: {rule.source || "Not reported"}
          </Text>
          <Text>
            Default severity: {rule.default_severity || "Not reported"} ·
            Effective severity: {rule.effective_severity}
          </Text>
          <Text>
            {rule.enabled ? "Enabled" : "Disabled"} ·{" "}
            {rule.enforced
              ? "Enforced despite inline ignores"
              : "Inline ignores respected"}
          </Text>
          {rule.scope && <Text>Scope: {rule.scope}</Text>}
          {error && (
            <Alert
              status="danger"
              title="Could not update rule"
              description={error}
            />
          )}
          {canManage && (
            <>
              <Text>
                Changes apply to all projects. Each action saves immediately.
              </Text>
              <Flex gap="2" style={{ flexWrap: "wrap" }}>
                <Button
                  variant="secondary"
                  isDisabled={saving}
                  onPress={() => void save({ enabled_override: !rule.enabled })}
                >
                  {rule.enabled ? "Disable rule" : "Enable rule"}
                </Button>
                <Button
                  variant="secondary"
                  isDisabled={saving}
                  onPress={() => void save({ enforced: !rule.enforced })}
                >
                  {rule.enforced
                    ? "Respect inline ignores"
                    : "Enforce despite inline ignores"}
                </Button>
                <Button
                  variant="secondary"
                  isDisabled={saving || !rule.has_override}
                  onPress={() => void save()}
                >
                  Reset all overrides
                </Button>
              </Flex>
              <Text>Severity override</Text>
              <RuleSeverity
                rule={rule}
                saving={saving}
                onChange={(value) => void save({ severity_override: value })}
              />
            </>
          )}
        </Flex>
      </DialogBody>
      <DialogFooter>
        <Button variant="secondary" onPress={onClose} isDisabled={saving}>
          {saving ? "Saving…" : "Close"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
