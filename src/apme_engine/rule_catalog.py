"""Rule catalog collector for ADR-041 registration.

Enumerates all built-in rules across validators and returns a list of
``RuleDefinition`` protos suitable for ``RegisterRules``.  Each validator
is collected via code-level interfaces (no gRPC):

- **Native**: ``load_graph_rules()`` returns live ``GraphRule`` instances.
- **OPA / Ansible**: YAML frontmatter in sidecar ``.md`` files.
- **Gitleaks**: Single ``SEC:*`` placeholder (dynamic external binary).

Severity comes from ``severity_defaults.py``; category is derived from the
rule-ID prefix per ADR-008.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from apme.v1 import common_pb2, reporting_pb2
from apme_engine.engine.models import RuleScope
from apme_engine.severity_defaults import get_severity, severity_to_proto

logger = logging.getLogger(__name__)

_VALIDATORS_ROOT = Path(__file__).resolve().parent / "validators"
_NATIVE_RULES_DIR = _VALIDATORS_ROOT / "native" / "rules"
_OPA_BUNDLE_DIR = _VALIDATORS_ROOT / "opa" / "bundle"
_ANSIBLE_RULES_DIR = _VALIDATORS_ROOT / "ansible" / "rules"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_KV_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)

_SCOPE_TO_PROTO: dict[str, int] = {
    RuleScope.TASK.value: common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
    RuleScope.BLOCK.value: common_pb2.RULE_SCOPE_BLOCK,  # type: ignore[attr-defined]
    RuleScope.PLAY.value: common_pb2.RULE_SCOPE_PLAY,  # type: ignore[attr-defined]
    RuleScope.PLAYBOOK.value: common_pb2.RULE_SCOPE_PLAYBOOK,  # type: ignore[attr-defined]
    RuleScope.ROLE.value: common_pb2.RULE_SCOPE_ROLE,  # type: ignore[attr-defined]
    RuleScope.INVENTORY.value: common_pb2.RULE_SCOPE_INVENTORY,  # type: ignore[attr-defined]
    RuleScope.COLLECTION.value: common_pb2.RULE_SCOPE_COLLECTION,  # type: ignore[attr-defined]
}

_PREFIX_TO_CATEGORY: dict[str, str] = {
    "L": "lint",
    "M": "modernize",
    "R": "risk",
    "P": "policy",
    "SEC": "secrets",
    "INFRA": "infrastructure",
}


def _category_from_rule_id(rule_id: str) -> str:
    """Derive category from rule-ID prefix per ADR-008.

    Args:
        rule_id: Rule identifier (e.g. ``L026``, ``SEC:key``).

    Returns:
        Category string (lint, modernize, risk, policy, secrets, or unknown).
    """
    for prefix, category in _PREFIX_TO_CATEGORY.items():
        if rule_id.startswith(prefix):
            return category
    return "unknown"


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a markdown file.

    Args:
        path: Path to the ``.md`` file.

    Returns:
        Dict of frontmatter key-value pairs, or empty dict if none found.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    return dict(_KV_RE.findall(m.group(1)))


def _collect_native_rules() -> list[reporting_pb2.RuleDefinition]:
    """Collect rules from the Native validator via ``load_graph_rules()``.

    Returns:
        List of RuleDefinition protos for all enabled native rules.
    """
    try:
        from apme_engine.engine.graph_scanner import load_graph_rules

        rules_dir = str(_NATIVE_RULES_DIR)
        graph_rules = load_graph_rules(rules_dir=rules_dir)
        defs = []
        for gr in graph_rules:
            defs.append(
                reporting_pb2.RuleDefinition(
                    rule_id=gr.rule_id,
                    default_severity=severity_to_proto(get_severity(gr.rule_id)),
                    category=_category_from_rule_id(gr.rule_id),
                    source="native",
                    description=gr.description or "",
                    scope=_SCOPE_TO_PROTO.get(
                        gr.scope,
                        common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
                    )
                    or 0,
                    enabled=gr.enabled,
                )
            )
        logger.info("Collected %d native rules", len(defs))
        return defs
    except Exception:
        logger.warning(
            "Failed to collect native rules via load_graph_rules; falling back to frontmatter",
            exc_info=True,
        )
        return _collect_from_frontmatter(_NATIVE_RULES_DIR, "native")


def _collect_from_frontmatter(
    directory: Path,
    source: str,
) -> list[reporting_pb2.RuleDefinition]:
    """Collect rules by parsing ``.md`` sidecar frontmatter.

    Args:
        directory: Directory containing ``.md`` rule documentation files.
        source: Validator name (e.g. ``opa``, ``ansible``).

    Returns:
        List of RuleDefinition protos parsed from frontmatter.
    """
    defs: list[reporting_pb2.RuleDefinition] = []
    if not directory.is_dir():
        return defs
    for md in sorted(directory.glob("*.md")):
        fm = _parse_frontmatter(md)
        rule_id = fm.get("rule_id", "")
        if not rule_id:
            continue
        scope_str = fm.get("scope", "task")
        defs.append(
            reporting_pb2.RuleDefinition(
                rule_id=rule_id,
                default_severity=severity_to_proto(get_severity(rule_id)),
                category=_category_from_rule_id(rule_id),
                source=source,
                description=fm.get("description", ""),
                scope=_SCOPE_TO_PROTO.get(
                    scope_str,
                    common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
                )
                or 0,
                enabled=True,
            )
        )
    logger.info("Collected %d %s rules from frontmatter", len(defs), source)
    return defs


def _collect_gitleaks_rules() -> list[reporting_pb2.RuleDefinition]:
    """Return a single placeholder entry for Gitleaks (dynamic external binary).

    Returns:
        List with one ``SEC:*`` RuleDefinition.
    """
    return [
        reporting_pb2.RuleDefinition(
            rule_id="SEC:*",
            default_severity=severity_to_proto(get_severity("SEC:any")),
            category="secrets",
            source="gitleaks",
            description="Secret/credential detection (delegated to Gitleaks binary).",
            scope=common_pb2.RULE_SCOPE_PLAYBOOK,  # type: ignore[attr-defined]
            enabled=True,
        )
    ]


def collect_all_rules() -> list[reporting_pb2.RuleDefinition]:
    """Aggregate rules from all built-in validators.

    Returns:
        Deterministic list of RuleDefinition protos sorted by rule_id.
    """
    all_defs: list[reporting_pb2.RuleDefinition] = []
    all_defs.extend(_collect_native_rules())
    all_defs.extend(_collect_from_frontmatter(_OPA_BUNDLE_DIR, "opa"))
    all_defs.extend(_collect_from_frontmatter(_ANSIBLE_RULES_DIR, "ansible"))
    all_defs.extend(_collect_gitleaks_rules())

    all_defs.sort(key=lambda d: d.rule_id)
    logger.info("Total rules collected: %d", len(all_defs))
    return all_defs
