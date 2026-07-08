"""Graph-derived AI context for node-level remediation prompts.

Builds an ``AINodeContext`` from the ``ContentGraph`` that bundles
everything the LLM needs to fix violations on a single node: the node's
YAML, its violations, parent context (play vars, become, tags), and
surrounding sibling snippets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apme_engine.engine.models import ViolationDict

if TYPE_CHECKING:
    from apme_engine.graph.content_graph import ContentGraph


@dataclass(frozen=True, slots=True)
class AINodeContext:
    """Graph-derived context for a single node's AI remediation prompt.

    Attributes:
        node_id: Graph node identifier.
        node_type: Node type (task, block, handler, etc.).
        yaml_lines: Current YAML text for this node.
        violations: Violations on this node to be fixed.
        file_path: Source file path (display only).
        parent_context: Summarized ancestor chain (play vars, become, tags).
        sibling_snippets: YAML of surrounding siblings for awareness.
        feedback: Validation feedback from a prior failed AI attempt.
    """

    node_id: str
    node_type: str
    yaml_lines: str
    violations: list[ViolationDict]
    file_path: str = ""
    parent_context: str = ""
    sibling_snippets: list[str] = field(default_factory=list)
    feedback: str = ""


def build_ai_node_context(
    graph: ContentGraph,
    node_id: str,
    violations: list[ViolationDict],
    *,
    feedback: str = "",
    max_siblings: int = 2,
) -> AINodeContext | None:
    """Build an ``AINodeContext`` from the graph for a single node.

    Args:
        graph: ContentGraph containing the node.
        node_id: ID of the node to build context for.
        violations: Violations scoped to this node.
        feedback: Optional validation feedback from a prior AI attempt.
        max_siblings: Number of preceding/following siblings to include.

    Returns:
        Populated ``AINodeContext``, or ``None`` if the node doesn't exist
        or has no YAML content.
    """
    node = graph.get_node(node_id)
    if node is None or not node.yaml_lines:
        return None

    parent_context = _build_parent_context(graph, node_id)
    sibling_snippets = _build_sibling_snippets(graph, node_id, max_siblings)

    return AINodeContext(
        node_id=node_id,
        node_type=node.node_type.value,
        yaml_lines=node.yaml_lines,
        violations=violations,
        file_path=node.file_path,
        parent_context=parent_context,
        sibling_snippets=sibling_snippets,
        feedback=feedback,
    )


def _build_parent_context(graph: ContentGraph, node_id: str) -> str:
    """Summarize ancestor chain as structured context for the LLM.

    Walks ``graph.ancestors()`` and extracts play-level variables,
    become settings, and tags — the inherited context that affects
    how a task executes.

    Args:
        graph: ContentGraph for ancestor lookup.
        node_id: Starting node to walk upward from.

    Returns:
        Multi-line summary string, or empty string if no relevant context.
    """
    ancestors = graph.ancestors(node_id)
    if not ancestors:
        return ""

    sections: list[str] = []
    for anc in ancestors:
        parts: list[str] = []
        label = f"{anc.node_type.value}"
        if anc.name:
            label += f" ({anc.name})"

        if anc.variables:
            parts.append(f"  vars: {json.dumps(_simplify_dict(anc.variables), indent=2)}")
        if anc.become:
            parts.append(f"  become: {json.dumps(_simplify_dict(anc.become))}")
        if anc.tags:
            parts.append(f"  tags: {anc.tags}")
        if anc.when_expr:
            parts.append(f"  when: {anc.when_expr}")
        if anc.environment:
            parts.append(f"  environment: {json.dumps(_simplify_dict(anc.environment))}")

        if parts:
            sections.append(f"{label}:\n" + "\n".join(parts))

    return "\n".join(sections)


def _build_sibling_snippets(
    graph: ContentGraph,
    node_id: str,
    max_siblings: int,
) -> list[str]:
    """Get YAML snippets of surrounding sibling nodes for context.

    Finds the node's parent, gets all children, locates this node's
    position, and returns up to ``max_siblings`` snippets before and
    after (truncated to 20 lines each).

    Args:
        graph: ContentGraph for traversal.
        node_id: Node whose siblings to find.
        max_siblings: Number of siblings before/after to include.

    Returns:
        List of sibling YAML snippets (may be empty).
    """
    ancestors = graph.ancestors(node_id)
    if not ancestors:
        return []

    parent = ancestors[0]
    children = graph.children(parent.node_id)
    if len(children) <= 1:
        return []

    idx = next((i for i, c in enumerate(children) if c.node_id == node_id), -1)
    if idx < 0:
        return []

    snippets: list[str] = []
    start = max(0, idx - max_siblings)
    end = min(len(children), idx + max_siblings + 1)

    for i in range(start, end):
        if i == idx:
            continue
        child = children[i]
        if child.yaml_lines:
            snippet = _truncate_snippet(child.yaml_lines, max_lines=20)
            snippets.append(snippet)

    return snippets


def _simplify_dict(d: object) -> dict[str, object]:
    """Simplify a nested dict for prompt display by truncating long values.

    Args:
        d: Dictionary (or mapping) to simplify.

    Returns:
        Simplified copy with long values truncated.
    """
    if not isinstance(d, dict):
        return {}
    result: dict[str, object] = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 200:
            result[k] = v[:200] + "..."
        elif isinstance(v, dict):
            result[k] = _simplify_dict(v)
        else:
            result[k] = v
    return result


def _truncate_snippet(text: str, *, max_lines: int = 20) -> str:
    """Truncate a YAML snippet to a maximum number of lines.

    Args:
        text: YAML text to truncate.
        max_lines: Maximum number of lines to keep.

    Returns:
        Truncated text with ``# ... (truncated)`` marker if needed.
    """
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return text
    return "".join(lines[:max_lines]) + "# ... (truncated)\n"
