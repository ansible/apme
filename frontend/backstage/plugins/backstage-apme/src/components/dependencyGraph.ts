/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { GraphData, ProjectDependencies } from "@apme/client";

/** Convert uv's recorded tree into real package edges, never guessed inventory edges. */
export function dependencyGraph(dependencies: ProjectDependencies) {
  const graph: GraphData = { version: 1, nodes: [], edges: [] };
  const ids = new Map<string, string>();
  const edgeIds = new Set<string>();
  const parents: string[] = [];
  let truncated = false;
  function node(name: string, version: string) {
    const key = `${name.toLowerCase().replace(/[-_.]+/g, "-")}@${version}`;
    const existing = ids.get(key);
    if (existing) return existing;
    if (graph.nodes.length >= 500) {
      truncated = true;
      return undefined;
    }
    const id = `package-${graph.nodes.length}`;
    ids.set(key, id);
    graph.nodes.push({
      id,
      data: {
        name,
        version,
        module: version ? `v${version}` : "",
        node_type: "python_package",
        scope: "owned",
      },
    });
    return id;
  }
  const lines = dependencies.dependency_tree.split(/\r?\n/);
  for (const raw of lines.slice(0, 10000)) {
    // Strip terminal color sequences from dependency-tree output.
    // eslint-disable-next-line no-control-regex -- ANSI escape codes contain ESC.
    const line = raw.replace(/\u001b\[[0-9;]*m/g, "");
    const connector = /[├└]─+\s/.exec(line);
    const indentation = /^\s*/.exec(line)![0].replace(/\t/g, "    ").length;
    const depth = connector
      ? Math.floor(connector.index / 4) + 1
      : Math.floor(indentation / 2);
    if (depth > 64) {
      truncated = true;
      parents.length = 0;
      continue;
    }
    const label = connector
      ? line.slice(connector.index + connector[0].length)
      : line.trim();
    // uv uses `name v1.2.3`; also accept the recorded `name==1.2.3` format.
    const match =
      /^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:(?:==|\s+v)([^\s()]+))?(?:\s+\((?:\*|cycle)\))?$/.exec(
        label,
      );
    if (!match) {
      parents.length = 0;
      continue;
    }
    const id = node(match[1]!, match[2] ?? "");
    if (!id) {
      parents.length = 0;
      continue;
    }
    const parent = depth > 0 ? parents[depth - 1] : undefined;
    const edgeKey = `${parent}:${id}`;
    if (parent && !edgeIds.has(edgeKey)) {
      if (graph.edges.length < 2000) {
        graph.edges.push({
          source: parent,
          target: id,
          edge_type: "dependency",
        });
        edgeIds.add(edgeKey);
      } else truncated = true;
    }
    // Missing indentation levels do not inherit a previous sibling's parent.
    parents.length = depth;
    parents[depth] = id;
  }
  if (lines.length > 10000) truncated = true;
  const hasRecordedTree = graph.nodes.length > 0;
  if (!hasRecordedTree) {
    if (dependencies.ansible_core_version)
      node("ansible-core", dependencies.ansible_core_version);
    for (const item of dependencies.python_packages)
      node(item.name, item.version);
  }
  return { graph, truncated, hasRecordedTree };
}
