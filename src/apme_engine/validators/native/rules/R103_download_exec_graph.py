"""GraphRule R103: detect download-then-execute pattern across tasks."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules._module_risk_mapping import (
    get_risk_profile,
    resolve_field,
)
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
    is_templated,
)

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class DownloadExecGraphRule(GraphRule):
    """Flag tasks that execute a command when a preceding sibling downloads to a templated source.

    Walks backward through sibling tasks in the same play/block to
    find inbound-transfer modules whose ``src`` is Jinja-templated.
    If the inbound task's ``dest`` path appears in the current task's
    command string, it is flagged as a download-then-execute pattern.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        precedence: Evaluation order (after single-task R-rules).
    """

    rule_id: str = "R103"
    description: str = "A downloaded file from parameterized source is executed"
    enabled: bool = True
    name: str = "Download & Exec"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.NETWORK, Tag.COMMAND)
    precedence: int = 11

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use a command-execution module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has a ``cmd_exec`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.resolved_module_name, node.module)
        return profile is not None and profile.risk_type == "cmd_exec"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for a preceding inbound transfer whose dest is executed.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        cmd_profile = get_risk_profile(node.resolved_module_name, node.module)
        if cmd_profile is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        cmd = resolve_field(mo, cmd_profile, "cmd")
        if not cmd:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        parent_play = _find_enclosing_play(graph, node_id)
        if parent_play is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"command": cmd}

        preceding = _preceding_tasks(graph, parent_play.node_id, node)
        for task_node in preceding:
            sib_profile = get_risk_profile(task_node.resolved_module_name, task_node.module)
            if sib_profile is None or sib_profile.risk_type != "inbound":
                continue

            sib_mo = task_node.module_options
            if not isinstance(sib_mo, dict):
                continue

            sib_src = resolve_field(sib_mo, sib_profile, "src")
            if not sib_src or not is_templated(sib_src):
                continue

            sib_dest = resolve_field(sib_mo, sib_profile, "dest")
            if sib_dest and sib_dest in cmd:
                detail["src"] = sib_src
                detail["executed_file"] = sib_dest
                return GraphRuleResult(
                    verdict=True,
                    detail=detail,
                    node_id=node_id,
                    file=(node.file_path, node.line_start),
                )

        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )


def _preceding_tasks(graph: ContentGraph, play_id: str, current: ContentNode) -> list[ContentNode]:
    """Collect all task/handler descendants of *play_id* that precede *current*.

    Walks the full descendant tree so tasks inside ``block:`` scopes are
    included, not just direct children.

    Args:
        graph: The content graph.
        play_id: Node ID of the enclosing play.
        current: The current command-execution node.

    Returns:
        Task/handler nodes with ``line_start`` < ``current.line_start``.
    """
    result: list[ContentNode] = []
    for desc_id in graph.descendants(play_id):
        desc = graph.get_node(desc_id)
        if desc is None or desc.node_type not in _TASK_TYPES:
            continue
        if desc.line_start >= current.line_start:
            continue
        result.append(desc)
    return result


def _find_enclosing_play(graph: ContentGraph, node_id: str) -> ContentNode | None:
    """Walk ancestors to find the nearest PLAY node.

    Args:
        graph: The content graph.
        node_id: Starting node.

    Returns:
        The enclosing play ``ContentNode``, or ``None``.
    """
    for ancestor in graph.ancestors(node_id):
        if ancestor.node_type == NodeType.PLAY:
            return ancestor
    return None
