/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { lazy, Suspense, useCallback, useState } from "react";
import {
  Button,
  Cell,
  Column,
  Flex,
  Row,
  SearchField,
  TableBody,
  TableHeader,
  TableRoot,
  Text,
} from "@backstage/ui";
import { getProjectGraph } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { DisclosureSection } from "./DisclosureSection";
import { useLoad } from "./useLoad";

const GraphVisualization = lazy(() =>
  import("@apme/ui-workflow/graph").then((module) => ({
    default: module.GraphVisualization,
  })),
);

export interface ApmeProjectGraphProps {
  projectId: string;
  adapter: ApmeApiAdapter;
}

export function ApmeProjectGraph(props: ApmeProjectGraphProps) {
  return <ProjectGraphContent key={props.projectId} {...props} />;
}

function ProjectGraphContent({ projectId, adapter }: ApmeProjectGraphProps) {
  const load = useCallback(
    () => getProjectGraph(projectId, adapter),
    [projectId, adapter],
  );
  const state = useLoad(load);
  const [diagram, setDiagram] = useState(true);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string>();
  const graph = state.value;
  const nodes =
    graph?.nodes.filter((node) =>
      `${node.id} ${node.data.name ?? ""} ${node.data.node_type ?? ""} ${node.data.file_path ?? ""} ${node.data.module ?? ""}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
    ) ?? [];
  const selected = graph?.nodes.find((node) => node.id === selectedId);
  const page = Math.min(
    offset,
    Math.max(0, Math.ceil(nodes.length / 50) - 1) * 50,
  );
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Content graph
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh graph
        </Button>
      </Flex>
      <DataStatus {...state} subject="content graph" />
      {graph &&
        (graph.nodes.length ? (
          <>
            <Flex gap="2">
              <Button
                variant="secondary"
                aria-pressed={diagram}
                onPress={() => setDiagram(true)}
              >
                Diagram
              </Button>
              <Button
                variant="secondary"
                aria-pressed={!diagram}
                onPress={() => setDiagram(false)}
              >
                Node list
              </Button>
            </Flex>
            {diagram ? (
              <Suspense
                fallback={<Text role="status">Loading graph renderer…</Text>}
              >
                <GraphVisualization data={graph} />
              </Suspense>
            ) : (
              <>
                <SearchField
                  label="Search graph nodes"
                  value={search}
                  onChange={(value) => {
                    setSearch(value);
                    setOffset(0);
                  }}
                />
                <Text>{nodes.length} matching nodes</Text>
                <TableRoot aria-label="Content graph nodes">
                  <TableHeader>
                    <Column isRowHeader>Node</Column>
                    <Column>Type</Column>
                    <Column>File</Column>
                    <Column>Module</Column>
                  </TableHeader>
                  <TableBody
                    items={nodes.slice(page, page + 50)}
                    renderEmptyState={() => "No matching nodes."}
                  >
                    {(node) => (
                      <Row id={node.id}>
                        <Cell>
                          <Button
                            variant="tertiary"
                            onPress={() => setSelectedId(node.id)}
                          >
                            {String(node.data.name || node.id)}
                          </Button>
                        </Cell>
                        <Cell>{String(node.data.node_type ?? "")}</Cell>
                        <Cell>
                          {String(node.data.file_path ?? "")}
                          {node.data.line_start
                            ? `:${node.data.line_start}`
                            : ""}
                        </Cell>
                        <Cell>{String(node.data.module ?? "")}</Cell>
                      </Row>
                    )}
                  </TableBody>
                </TableRoot>
                <PageControls
                  offset={page}
                  size={50}
                  hasNext={page + 50 < nodes.length}
                  busy={false}
                  onChange={setOffset}
                  subject="graph nodes"
                />
                {selected && (
                  <section aria-label="Selected graph node">
                    <Flex direction="column" gap="4">
                      <Text as="h4" variant="title-x-small">
                        {String(selected.data.name || selected.id)}
                      </Text>
                      <Text>{selected.id}</Text>
                      <Text>{String(selected.data.file_path ?? "")}</Text>
                      {typeof selected.data.yaml_lines === "string" && (
                        <DisclosureSection key={selected.id} title="Node YAML">
                          <pre
                            style={{
                              maxHeight: 360,
                              overflow: "auto",
                              whiteSpace: "pre-wrap",
                            }}
                          >
                            {selected.data.yaml_lines}
                          </pre>
                        </DisclosureSection>
                      )}
                      <DisclosureSection
                        key={`${selected.id}-edges`}
                        title="Connected edges"
                      >
                        <ul>
                          {graph.edges
                            .filter(
                              (edge) =>
                                edge.source === selected.id ||
                                edge.target === selected.id,
                            )
                            .map((edge, index) => (
                              <li key={index}>
                                {edge.source} → {edge.target} (
                                {edge.edge_type || "contains"})
                              </li>
                            ))}
                        </ul>
                      </DisclosureSection>
                    </Flex>
                  </section>
                )}
              </>
            )}
          </>
        ) : (
          <Text>
            No content graph nodes recorded. Run a check to generate the graph.
          </Text>
        ))}
    </Flex>
  );
}
