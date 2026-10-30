/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import {
  Button,
  Cell,
  Column,
  Flex,
  Row,
  TableBody,
  TableHeader,
  TableRoot,
  Text,
} from "@backstage/ui";
import { listProjectActivity } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { useLoad } from "./useLoad";

const PAGE_SIZE = 25;

export interface ApmeProjectHistoryProps {
  projectId: string;
  adapter: ApmeApiAdapter;
  onOpenActivity: (scanId: string) => void;
}

export function ApmeProjectHistory(props: ApmeProjectHistoryProps) {
  return <ApmeProjectHistoryContent key={props.projectId} {...props} />;
}

function ApmeProjectHistoryContent({
  projectId,
  adapter,
  onOpenActivity,
}: ApmeProjectHistoryProps) {
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    () => listProjectActivity(projectId, PAGE_SIZE, offset, adapter),
    [projectId, adapter, offset],
  );
  const state = useLoad(load);
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Project history
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh history
        </Button>
      </Flex>
      <DataStatus {...state} subject="project history" />
      {!state.loading && !state.error && state.value && (
        <>
          <Text>
            {state.value.total}{" "}
            {state.value.total_is_exact === false ? "available runs" : "runs"}
          </Text>
          <TableRoot aria-label="Project history">
            <TableHeader>
              <Column isRowHeader>Run</Column>
              <Column>Started</Column>
              <Column>Findings</Column>
              <Column>Fixed</Column>
            </TableHeader>
            <TableBody
              items={state.value.items}
              renderEmptyState={() =>
                "No history yet. Check this repository to record a run."
              }
            >
              {(run) => (
                <Row id={run.scan_id}>
                  <Cell>
                    <Button
                      variant="tertiary"
                      onPress={() => onOpenActivity(run.scan_id)}
                    >
                      {run.scan_type === "fix" || run.scan_type === "remediate"
                        ? "Remediate"
                        : "Check"}{" "}
                      · {run.scan_id}
                    </Button>
                  </Cell>
                  <Cell>
                    <time dateTime={run.created_at}>
                      {new Date(run.created_at).toLocaleString()}
                    </time>
                  </Cell>
                  <Cell>{run.total_violations}</Cell>
                  <Cell>{run.remediated_count}</Cell>
                </Row>
              )}
            </TableBody>
          </TableRoot>
        </>
      )}
      <PageControls
        offset={offset}
        size={PAGE_SIZE}
        busy={state.loading}
        hasNext={!!state.value && offset + PAGE_SIZE < state.value.total}
        onChange={setOffset}
        subject="history"
      />
    </Flex>
  );
}
