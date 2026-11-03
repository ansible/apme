import { dependencyGraph } from "../plugins/backstage-apme/src/components/dependencyGraph";
import type { ProjectDependencies } from "@apme/client";
const deps = (dependency_tree: string): ProjectDependencies => ({
  dependency_tree,
  ansible_core_version: "2.19",
  collections: [],
  python_packages: [],
  requirements_files: [],
});
it("parses uv roots, versions and shared dependencies without inventing sibling edges", () => {
  const { graph } = dependencyGraph(
    deps(
      "ansible-core v2.19.3\n├── jinja2 v3.1.6\n│   └── markupsafe v3.0.3\n└── packaging v25.0\nrequests v2.32.0\n└── packaging v25.0 (*)\n\n(*) Package tree already displayed",
    ),
  );
  expect(graph.nodes).toHaveLength(5);
  expect(graph.edges).toHaveLength(4);
  const names = new Map(graph.nodes.map((n) => [n.id, n.data.name]));
  expect(
    graph.edges.map((e) => `${names.get(e.source)}->${names.get(e.target)}`),
  ).toEqual([
    "ansible-core->jinja2",
    "jinja2->markupsafe",
    "ansible-core->packaging",
    "requests->packaging",
  ]);
  expect(
    graph.nodes.find((n) => n.data.name === "requests")?.data.version,
  ).toBe("2.32.0");
});
it("preserves versions, canonical package identities and cyclic references", () => {
  const { graph } = dependencyGraph(
    deps("pkg_a==1.0\n└── child==2.0\n    └── pkg-a==1.0 (*)\npkg_a==2.0"),
  );
  expect(graph.nodes).toHaveLength(3);
  expect(graph.edges).toHaveLength(2);
  expect(graph.edges[1]?.target).toBe(graph.nodes[0]?.id);
});
it("falls back to disconnected inventory when no relationships are recorded", () => {
  const { graph, hasRecordedTree } = dependencyGraph({
    ...deps(""),
    python_packages: [{ name: "requests", version: "2.32" }],
  });
  expect(hasRecordedTree).toBe(false);
  expect(graph.nodes).toHaveLength(2);
  expect(graph.edges).toEqual([]);
});
it("bounds diagram work while retaining access to the original recorded text", () => {
  const result = dependencyGraph(
    deps(Array.from({ length: 600 }, (_, i) => `pkg-${i} v1.0`).join("\n")),
  );
  expect(result.truncated).toBe(true);
  expect(result.graph.nodes).toHaveLength(500);
});
it("does not connect across an unrecognized line or a missing indentation level", () => {
  const { graph } = dependencyGraph(
    deps(
      "root v1\n└── child v2\n[unsupported]\n    └── leaf v3\nother v1\n        └── deep v3",
    ),
  );
  expect(graph.edges).toHaveLength(1);
});
