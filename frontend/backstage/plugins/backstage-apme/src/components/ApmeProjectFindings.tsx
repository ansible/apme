/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import { Button, Checkbox, Flex, Text } from "@backstage/ui";
import { listProjectViolations } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { AssessFindingsPanel, violationToFinding } from "@apme/ui-workflow";
import { DataStatus, PageControls } from "./DataStatus";
import { useLoad } from "./useLoad";

const PAGE_SIZE = 50;

export interface ApmeProjectFindingsProps {
  projectId: string;
  adapter: ApmeApiAdapter;
  ruleId?: string;
  resolveRuleHref?: (ruleId: string) => string | undefined;
}

/** Read-only latest findings, with bounded pages and the existing review UI. */
export function ApmeProjectFindings(props: ApmeProjectFindingsProps) {
  return (
    <ApmeProjectFindingsContent
      key={`${props.projectId}:${props.ruleId ?? ""}`}
      {...props}
    />
  );
}

function ApmeProjectFindingsContent({
  projectId,
  adapter,
  ruleId,
  resolveRuleHref,
}: ApmeProjectFindingsProps) {
  const [offset, setOffset] = useState(0);
  const [includeAcknowledged, setIncludeAcknowledged] = useState(false);
  const load = useCallback(
    () =>
      listProjectViolations(
        projectId,
        PAGE_SIZE + 1,
        offset,
        undefined,
        ruleId,
        adapter,
      ),
    [projectId, adapter, offset, ruleId],
  );
  const state = useLoad(load);
  const rows = state.value?.slice(0, PAGE_SIZE) ?? [];
  const findings = rows
    .filter((row) => includeAcknowledged || !row.suppressed)
    .map(violationToFinding);
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Latest findings
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh findings
        </Button>
      </Flex>
      <DataStatus {...state} subject="findings" />
      {!state.loading && !state.error && (
        <>
          <Checkbox
            isSelected={includeAcknowledged}
            onChange={setIncludeAcknowledged}
          >
            Show acknowledged findings
          </Checkbox>
          <Text>
            {rows.length
              ? `Findings ${offset + 1}–${offset + rows.length}`
              : "No findings on this page."}
          </Text>
          {findings.length > 0 ? (
            <AssessFindingsPanel
              findings={findings}
              resolveRuleHref={resolveRuleHref}
              description="Findings from the latest check."
            />
          ) : (
            rows.length > 0 && (
              <Text>All findings on this page are acknowledged.</Text>
            )
          )}
        </>
      )}
      <PageControls
        offset={offset}
        size={PAGE_SIZE}
        busy={state.loading}
        hasNext={(state.value?.length ?? 0) > PAGE_SIZE}
        onChange={setOffset}
        subject="findings"
      />
    </Flex>
  );
}
