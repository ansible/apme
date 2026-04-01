"""GraphRule L095: basic structural schema validation for plays and galaxy.yml."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_VALID_PLAY_KEYWORDS: frozenset[str] = frozenset(
    {
        # Host & connection
        "hosts",
        "connection",
        "port",
        "remote_user",
        # Privilege escalation
        "become",
        "become_exe",
        "become_flags",
        "become_method",
        "become_user",
        # Fact gathering
        "fact_path",
        "gather_facts",
        "gather_subset",
        "gather_timeout",
        # Execution control
        "any_errors_fatal",
        "check_mode",
        "diff",
        "force_handlers",
        "ignore_errors",
        "ignore_unreachable",
        "max_fail_percentage",
        "order",
        "run_once",
        "serial",
        "strategy",
        "throttle",
        "timeout",
        # Configuration
        "debugger",
        "environment",
        "no_log",
        "tags",
        "when",
        # Play structure (not consumed by load_play, lands in options)
        "vars_prompt",
    }
)

_GALAXY_REQUIRED_KEYS: frozenset[str] = frozenset({"namespace", "name", "version"})


def _unknown_play_keys(options: Mapping[str, object]) -> list[str]:
    """Return play option keys that are not recognized Ansible play keywords.

    Args:
        options: The ``options`` dict from a PLAY ContentNode.

    Returns:
        Sorted list of unrecognized key names.
    """
    return sorted(k for k in options if k not in _VALID_PLAY_KEYWORDS)


def _missing_galaxy_keys(metadata: Mapping[str, object]) -> list[str]:
    """Return required galaxy.yml keys that are missing or empty.

    Handles both flat ``galaxy.yml`` and ``MANIFEST.json`` (where galaxy
    fields are nested under ``collection_info``).

    Args:
        metadata: The ``collection_metadata`` dict from a COLLECTION node.

    Returns:
        Sorted list of missing required key names.
    """
    ci = metadata.get("collection_info")
    source: Mapping[str, object] = ci if isinstance(ci, Mapping) else metadata

    missing: list[str] = []
    for key in sorted(_GALAXY_REQUIRED_KEYS):
        val = source.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(key)
    return missing


@dataclass
class SchemaValidationGraphRule(GraphRule):
    """Flag plays with unknown keys and collections with missing galaxy.yml keys.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L095"
    description: str = "YAML file does not match expected schema structure"
    enabled: bool = True
    name: str = "SchemaValidation"
    version: str = "v0.0.1"
    severity: Severity = Severity.ERROR
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match PLAY nodes with options or COLLECTION nodes with metadata.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for PLAY nodes with non-empty ``options`` or COLLECTION
            nodes with non-empty ``collection_metadata``.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type == NodeType.PLAY:
            return bool(node.options)
        if node.node_type == NodeType.COLLECTION:
            return bool(node.collection_metadata)
        return False

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check schema conformance for plays and collections.

        For PLAY nodes, flags unknown play-level keys in ``options``.
        For COLLECTION nodes, flags missing required galaxy.yml keys.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when schema issues
            are found, or ``verdict`` False when conformant.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        if node.node_type == NodeType.PLAY:
            return self._check_play(node_id, node.options, node.file_path, node.line_start)

        if node.node_type == NodeType.COLLECTION:
            return self._check_collection(node_id, node.collection_metadata, node.file_path, node.line_start)

        return None

    def _check_play(self, node_id: str, options: YAMLDict, file_path: str, line_start: int) -> GraphRuleResult:
        """Validate play-level keys against known Ansible keywords.

        Args:
            node_id: Node identifier.
            options: Play options dict.
            file_path: Source file path.
            line_start: Starting line number.

        Returns:
            GraphRuleResult indicating pass or violation.
        """
        unknown = _unknown_play_keys(options)
        if not unknown:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(file_path, line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "message": f"unknown play keyword(s): {', '.join(unknown)}",
                "unknown_keys": unknown,
            },
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(file_path, line_start),
        )

    def _check_collection(self, node_id: str, metadata: YAMLDict, file_path: str, line_start: int) -> GraphRuleResult:
        """Validate galaxy.yml required keys.

        Args:
            node_id: Node identifier.
            metadata: Collection metadata dict.
            file_path: Source file path.
            line_start: Starting line number.

        Returns:
            GraphRuleResult indicating pass or violation.
        """
        missing = _missing_galaxy_keys(metadata)
        if not missing:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(file_path, line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "message": f"galaxy.yml missing required key(s): {', '.join(missing)}",
                "missing_keys": missing,
            },
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(file_path, line_start),
        )
