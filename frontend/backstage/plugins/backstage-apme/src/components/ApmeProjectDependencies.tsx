/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeButton as Button } from "./layout";
import { ApmeTable, TreeOutput } from "./layout";
import { useCallback, useState } from "react";
import {
  Cell,
  Column,
  Flex,
  Row,
  SearchField,
  TableBody,
  TableHeader,
  Text,
} from "@backstage/ui";
import { getProjectDependencies } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { DisclosureSection } from "./DisclosureSection";
import { useLoad } from "./useLoad";
import { DependencyFindings } from "./DependencyFindings";
import { ApmeProjectSbom } from "./ApmeProjectSbom";

export interface ApmeProjectDependenciesProps {
  projectId: string;
  adapter: ApmeApiAdapter;
  canAcknowledge?: boolean;
}

export function ApmeProjectDependencies(props: ApmeProjectDependenciesProps) {
  return <ApmeProjectDependenciesContent key={props.projectId} {...props} />;
}

function ApmeProjectDependenciesContent({
  projectId,
  adapter,
  canAcknowledge = false,
}: ApmeProjectDependenciesProps) {
  const [search, setSearch] = useState("");
  const [collectionOffset, setCollectionOffset] = useState(0);
  const [pythonOffset, setPythonOffset] = useState(0);
  const load = useCallback(
    () => getProjectDependencies(projectId, adapter),
    [projectId, adapter],
  );
  const state = useLoad(load);
  const dependencies = state.value;
  const query = search.trim().toLowerCase();
  const collections =
    dependencies?.collections.filter((item) =>
      `${item.fqcn} ${item.version} ${item.license ?? ""} ${item.supplier ?? ""}`
        .toLowerCase()
        .includes(query),
    ) ?? [];
  const packages =
    dependencies?.python_packages.filter((item) =>
      `${item.name} ${item.version} ${item.license ?? ""} ${item.supplier ?? ""}`
        .toLowerCase()
        .includes(query),
    ) ?? [];
  return (
    <Flex className="apme-ui" direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Project dependencies
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh dependencies
        </Button>
      </Flex>
      <DataStatus {...state} subject="dependencies" />
      <ApmeProjectSbom projectId={projectId} adapter={adapter} />
      {dependencies && !state.error && !state.loading && (
        <>
          <Text>
            Ansible Core: {dependencies.ansible_core_version || "Not recorded"}
          </Text>
          <SearchField
            label="Search dependencies"
            value={search}
            onChange={(value) => {
              setSearch(value);
              setCollectionOffset(0);
              setPythonOffset(0);
            }}
          />
          <DependencyFindings
            projectId={projectId}
            adapter={adapter}
            canAcknowledge={canAcknowledge}
          >
            {(renderIssues) => (
              <>
                <Text as="h4" variant="title-x-small">
                  Collections ({collections.length})
                </Text>
                <ApmeTable aria-label="Project collections">
                  <TableHeader>
                    <Column isRowHeader>Collection</Column>
                    <Column>Version</Column>
                    <Column>Source</Column>
                    <Column>License</Column>
                    <Column>Supplier</Column>
                    <Column>Issues</Column>
                  </TableHeader>
                  <TableBody
                    items={collections
                      .slice(collectionOffset, collectionOffset + 25)
                      .map((item, id) => ({
                        ...item,
                        id,
                      }))}
                    renderEmptyState={() =>
                      search
                        ? "No matching collections."
                        : "No collections recorded. Run a check to discover dependencies."
                    }
                  >
                    {(item) => (
                      <Row id={item.id}>
                        <Cell>{item.fqcn}</Cell>
                        <Cell>{item.version}</Cell>
                        <Cell>{item.source}</Cell>
                        <Cell>{item.license || "Not recorded"}</Cell>
                        <Cell>{item.supplier || "Not recorded"}</Cell>
                        <Cell>{renderIssues("collection", item.fqcn)}</Cell>
                      </Row>
                    )}
                  </TableBody>
                </ApmeTable>
                <PageControls
                  offset={collectionOffset}
                  size={25}
                  busy={false}
                  hasNext={collectionOffset + 25 < collections.length}
                  onChange={setCollectionOffset}
                  subject="collections"
                />
                <Text as="h4" variant="title-x-small">
                  Python packages ({packages.length})
                </Text>
                <ApmeTable aria-label="Project Python packages">
                  <TableHeader>
                    <Column isRowHeader>Package</Column>
                    <Column>Version</Column>
                    <Column>License</Column>
                    <Column>Supplier</Column>
                    <Column>Issues</Column>
                  </TableHeader>
                  <TableBody
                    items={packages
                      .slice(pythonOffset, pythonOffset + 25)
                      .map((item, id) => ({
                        ...item,
                        id,
                      }))}
                    renderEmptyState={() =>
                      search
                        ? "No matching Python packages."
                        : "No Python packages recorded."
                    }
                  >
                    {(item) => (
                      <Row id={item.id}>
                        <Cell>{item.name}</Cell>
                        <Cell>{item.version}</Cell>
                        <Cell>{item.license || "Not recorded"}</Cell>
                        <Cell>{item.supplier || "Not recorded"}</Cell>
                        <Cell>{renderIssues("python", item.name)}</Cell>
                      </Row>
                    )}
                  </TableBody>
                </ApmeTable>
                <PageControls
                  offset={pythonOffset}
                  size={25}
                  busy={false}
                  hasNext={pythonOffset + 25 < packages.length}
                  onChange={setPythonOffset}
                  subject="Python packages"
                />
                {dependencies.requirements_files.length > 0 && (
                  <section aria-label="Requirements files">
                    <Text as="h4" variant="title-x-small">
                      Requirements files
                    </Text>
                    <ul>
                      {dependencies.requirements_files.map((file) => (
                        <li key={file}>{file}</li>
                      ))}
                    </ul>
                  </section>
                )}
                {dependencies.dependency_tree && (
                  <DisclosureSection title="Dependency tree" defaultExpanded>
                    <TreeOutput
                      text={dependencies.dependency_tree}
                      label="Dependency tree"
                    />
                  </DisclosureSection>
                )}
              </>
            )}
          </DependencyFindings>
        </>
      )}
    </Flex>
  );
}
