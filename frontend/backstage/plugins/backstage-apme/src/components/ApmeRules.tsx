/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import {
  Alert,
  Button,
  Cell,
  Column,
  Dialog,
  DialogBody,
  DialogFooter,
  DialogHeader,
  Flex,
  Row,
  SearchField,
  Select,
  TableBody,
  TableHeader,
  TableRoot,
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
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";

export interface ApmeRulesProps {
  adapter: ApmeApiAdapter;
  /** Host permission decision. Backend authorization is still required. */
  canManage?: boolean;
}

export function ApmeRules({ adapter, canManage = false }: ApmeRulesProps) {
  const load = useCallback(() => listRules(undefined, adapter), [adapter]);
  const state = useLoad(load);
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
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h2" variant="title-medium">
          Rule catalog
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh rules
        </Button>
      </Flex>
      <DataStatus {...state} subject="rules" />
      {state.value && (
        <>
          <SearchField
            label="Search rules"
            description="Search ID, description, category, source or severity"
            value={search}
            onChange={(value) => {
              setSearch(value);
              setOffset(0);
            }}
          />
          <Flex gap="4" style={{ flexWrap: "wrap" }}>
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
          </Flex>
          <Text>
            {rules.length} matching rules ·{" "}
            {state.value.filter((rule) => rule.has_override).length} overrides
          </Text>
          <TableRoot aria-label="APME rule catalog">
            <TableHeader>
              <Column isRowHeader>Rule</Column>
              <Column>Description</Column>
              <Column>Category</Column>
              <Column>Source</Column>
              <Column>Severity</Column>
              <Column>Status</Column>
              <Column>Override</Column>
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
                      onPress={() => setSelectedId(rule.rule_id)}
                    >
                      {rule.rule_id}
                    </Button>
                  </Cell>
                  <Cell>{rule.description}</Cell>
                  <Cell>{rule.category}</Cell>
                  <Cell>{rule.source || "Not reported"}</Cell>
                  <Cell>{rule.effective_severity}</Cell>
                  <Cell>
                    {rule.enabled ? "Enabled" : "Disabled"}
                    {rule.enforced ? ", enforced" : ""}
                  </Cell>
                  <Cell>{rule.has_override ? "Yes" : "No"}</Cell>
                </Row>
              )}
            </TableBody>
          </TableRoot>
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

const severities = ["Info", "Low", "Medium", "High", "Error", "Critical"];

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
              <Text>Set severity</Text>
              <Flex gap="2" style={{ flexWrap: "wrap" }}>
                {severities.map((label, index) => (
                  <Button
                    key={label}
                    variant="secondary"
                    isDisabled={saving}
                    onPress={() => void save({ severity_override: index + 1 })}
                  >
                    {label}
                  </Button>
                ))}
              </Flex>
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
