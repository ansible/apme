/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { GraphData } from "@apme/client";

/** A bounded forest: containment is hierarchical; other relationships stay explicit. */
export function contentTree(graph: GraphData): string {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const children = new Map<string, GraphData["edges"]>();
  const parented = new Set<string>();
  const relationships: GraphData["edges"] = [];
  for (const edge of graph.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) continue;
    if (!edge.edge_type || edge.edge_type === "contains") {
      const siblings = children.get(edge.source) ?? [];
      siblings.push(edge);
      children.set(edge.source, siblings);
      parented.add(edge.target);
    } else relationships.push(edge);
  }
  for (const edges of children.values())
    edges.sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const clean = (value: unknown) =>
    String(value ?? "").replace(/[\r\n\t]/g, " ");
  const label = (id: string) => {
    const data = nodes.get(id)!.data;
    const location = data.file_path
      ? ` — ${clean(data.file_path)}${data.line_start ? `:${clean(data.line_start)}` : ""}`
      : "";
    return `${clean(data.node_type || "node")}: ${clean(data.name || id)}${location}`;
  };
  const lines: string[] = [];
  const expanded = new Set<string>();
  const limit = 5000;
  let truncated = false;
  // Roots first, then disconnected components or cycles with no root.
  const roots = [...nodes.keys()].filter((id) => !parented.has(id));
  for (const root of [...roots, ...nodes.keys()]) {
    if (expanded.has(root)) continue;
    const stack = [{ id: root, prefix: "", branch: "", depth: 0 }];
    while (stack.length) {
      if (lines.length >= limit) {
        truncated = true;
        break;
      }
      const { id, prefix, branch, depth } = stack.pop()!;
      const reference = expanded.has(id);
      lines.push(
        `${prefix}${branch}${label(id)}${reference ? " ↪ reference" : ""}`,
      );
      if (reference) continue;
      expanded.add(id);
      const edges = children.get(id) ?? [];
      if (depth >= 40 && edges.length) {
        lines.push(`${prefix}   … deeper nodes are listed separately`);
        continue;
      }
      for (let index = edges.length - 1; index >= 0; index--) {
        stack.push({
          id: edges[index]!.target,
          prefix:
            prefix + (branch ? (branch === "└── " ? "    " : "│   ") : ""),
          branch: index === edges.length - 1 ? "└── " : "├── ",
          depth: depth + 1,
        });
      }
    }
    if (truncated) break;
  }
  if (relationships.length && !truncated) {
    lines.push("", "Other relationships");
    for (const edge of relationships) {
      if (lines.length >= limit) {
        truncated = true;
        break;
      }
      lines.push(
        `${label(edge.source)} → ${label(edge.target)} (${clean(edge.edge_type)})`,
      );
    }
  }
  if (truncated)
    lines.push(
      "… Tree limited to 5,000 lines. Use Node list for all nodes or Diagram for relationships.",
    );
  return lines.join("\n");
}
