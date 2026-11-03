import { render, screen, fireEvent } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter, type GraphData } from "@apme/client";
import { contentTree } from "../plugins/backstage-apme/src/components/contentTree";
import { ApmeProjectGraph } from "../plugins/backstage-apme/src/components/ApmeProjectGraph";
import { ApmeProjectDependencies } from "../plugins/backstage-apme/src/components/ApmeProjectDependencies";

const graph: GraphData = {
  version: 1,
  nodes: [
    { id: "p", data: { name: "site.yml", node_type: "playbook" } },
    {
      id: "t",
      data: {
        name: "Install <httpd>",
        node_type: "task",
        file_path: "site.yml",
        line_start: 8,
      },
    },
    { id: "r", data: { name: "Disconnected role", node_type: "role" } },
  ],
  edges: [
    { source: "p", target: "t", edge_type: "contains" },
    { source: "t", target: "p", edge_type: "dependency" },
  ],
};

it("renders ordered containment and preserves disconnected nodes and non-tree relationships", () => {
  const text = contentTree(graph);
  expect(text).toContain(
    "playbook: site.yml\n└── task: Install <httpd> — site.yml:8",
  );
  expect(text).toContain("role: Disconnected role");
  expect(text).toContain("Other relationships");
  expect(text).toContain("(dependency)");
});
it("terminates cycles and marks shared nodes as references", () => {
  const text = contentTree({
    ...graph,
    edges: [
      { source: "p", target: "t" },
      { source: "t", target: "p" },
      { source: "r", target: "t" },
    ],
  });
  expect(text).toContain("↪ reference");
  expect(text.length).toBeLessThan(1000);
});
it("bounds deep and large graphs without recursion failure", () => {
  const nodes = Array.from({ length: 6000 }, (_, id) => ({
    id: String(id),
    data: { name: `task-${id}` },
  }));
  const edges = nodes
    .slice(1)
    .map((node, i) => ({ source: String(i), target: node.id }));
  expect(contentTree({ version: 1, nodes, edges })).toContain(
    "Tree limited to 5,000 lines",
  );
});
it("uses the shared tree surface by default and keeps node inspection available without fetching again", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(graph)));
  render(
    <BUIProvider>
      <ApmeProjectGraph
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      />
    </BUIProvider>,
  );
  const tree = await screen.findByRole("region", {
    name: "Content graph tree",
  });
  expect(tree.textContent).toContain("Install <httpd>");
  expect(tree.querySelector("httpd")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Node list" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Install <httpd>" }),
  );
  await screen.findByRole("region", { name: "Selected graph node" });
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("expands the dependency tree on entry and lets users collapse it", async () => {
  const fetch = vi.fn(
    async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify(
          String(input).includes("/dependencies")
            ? {
                ansible_core_version: "2.19",
                collections: [],
                python_packages: [],
                requirements_files: [],
                dependency_tree: "ansible-core\n└── requests",
              }
            : [],
        ),
      ),
  );
  render(
    <BUIProvider>
      <ApmeProjectDependencies
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      />
    </BUIProvider>,
  );
  await screen.findByRole("region", { name: "Dependency tree" });
  fireEvent.click(screen.getByRole("button", { name: "Dependency tree" }));
  expect(screen.queryByRole("region", { name: "Dependency tree" })).toBeNull();
});
