/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { lazy, Suspense, useMemo, useState } from "react";
import { Alert, Text } from "@backstage/ui";
import type { ProjectDependencies } from "@apme/client";
import { ApmeButton, TreeOutput } from "./layout";
import { dependencyGraph } from "./dependencyGraph";
const GraphVisualization = lazy(() =>
  import("@apme/ui-workflow/graph").then((module) => ({
    default: module.GraphVisualization,
  })),
);

/** Always expanded; the diagram and raw text share the recorded dependency data. */
export function DependencyDiagram({
  dependencies,
}: {
  dependencies: ProjectDependencies;
}) {
  const [view, setView] = useState("diagram");
  const { graph, truncated, hasRecordedTree } = useMemo(
    () => dependencyGraph(dependencies),
    [dependencies],
  );
  return (
    <section className="apme-stack" aria-label="Dependency tree">
      <Text as="h4" variant="title-x-small">
        Dependency tree
      </Text>
      <div className="apme-controls" aria-label="Dependency tree presentation">
        <ApmeButton
          variant="secondary"
          aria-pressed={view === "diagram"}
          onPress={() => setView("diagram")}
        >
          Diagram
        </ApmeButton>
        {dependencies.dependency_tree && (
          <ApmeButton
            variant="secondary"
            aria-pressed={view === "text"}
            onPress={() => setView("text")}
          >
            Text tree
          </ApmeButton>
        )}
      </div>
      {truncated && (
        <Alert
          status="warning"
          title="Large dependency graph"
          description="The diagram is limited to 500 packages and 2,000 relationships. The recorded text tree and inventory remain available."
        />
      )}
      {!hasRecordedTree && (
        <Text>
          No dependency relationships were recorded. Installed packages are
          shown without connections.
        </Text>
      )}
      {view === "text" ? (
        <TreeOutput
          text={dependencies.dependency_tree}
          label="Recorded dependency tree"
        />
      ) : graph.nodes.length ? (
        <Suspense
          fallback={<Text role="status">Loading dependency graph…</Text>}
        >
          <GraphVisualization data={graph} variant="dependencies" />
        </Suspense>
      ) : (
        <Text>
          No dependency packages recorded. Run a check to collect dependencies.
        </Text>
      )}
    </section>
  );
}
