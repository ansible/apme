/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback } from "react";
import {
  Button,
  Cell,
  Column,
  Flex,
  Row,
  TableBody,
  TableHeader,
} from "@backstage/ui";
import { getHealth } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus } from "./DataStatus";
import { ApmeTable, SectionHeader } from "./layout";
import { useLoad } from "./useLoad";

export interface ApmeHealthProps {
  adapter: ApmeApiAdapter;
}

/** One request on entry/refresh; no background health polling. */
export function ApmeHealth({ adapter }: ApmeHealthProps) {
  const load = useCallback(() => getHealth(adapter), [adapter]);
  const state = useLoad(load);
  const health = state.value;
  const rows = health
    ? [
        { name: "Gateway", address: "This service", status: health.status },
        {
          name: "Database",
          address: health.database_type || "Not reported",
          status: health.database,
        },
        ...health.components,
      ].map((row, id) => ({ ...row, id }))
    : [];
  return (
    <Flex className="apme-ui" direction="column" gap="4">
      <SectionHeader
        title="System health"
        description="Current status of the services that power APME."
        actions={
          <Button
            variant="secondary"
            onPress={state.reload}
            isDisabled={state.loading}
          >
            Refresh health
          </Button>
        }
      />
      <DataStatus {...state} subject="system health" />
      {health && (
        <ApmeTable aria-label="APME system health">
          <TableHeader>
            <Column isRowHeader>Component</Column>
            <Column>Address</Column>
            <Column>Status</Column>
          </TableHeader>
          <TableBody items={rows}>
            {(row) => (
              <Row id={row.id}>
                <Cell>{row.name}</Cell>
                <Cell>{row.address}</Cell>
                <Cell>{row.status}</Cell>
              </Row>
            )}
          </TableBody>
        </ApmeTable>
      )}
    </Flex>
  );
}
