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

import functools
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from apme.v1 import common_pb2, reporting_pb2
from apme_engine.engine.models import RuleScope
from apme_engine.fingerprint import canonicalize_rule_id
from apme_engine.graph.severity import get_severity, severity_to_proto
from apme_engine.version_defaults import get_version_spec_str

logger = logging.getLogger(__name__)

_APME_ENGINE_ROOT = Path(__file__).resolve().parent
_GRAPH_RULES_DIR = _APME_ENGINE_ROOT / "graph" / "rules"
_OPA_BUNDLE_DIR = _APME_ENGINE_ROOT / "validators" / "opa" / "bundle"
_ANSIBLE_RULES_DIR = _APME_ENGINE_ROOT / "validators" / "ansible" / "rules"

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
    "A": "aap",
    "L": "lint",
    "M": "modernize",
    "R": "risk",
    "P": "policy",
    "SEC": "secrets",
}


def _category_from_rule_id(rule_id: str) -> str:
    """Derive category from rule-ID prefix per ADR-008.

    Args:
        rule_id: Rule identifier (e.g. ``L026``, ``SEC:key``).

    Returns:
        Category string (aap, lint, modernize, risk, policy, secrets, or unknown).
    """
    for prefix, category in _PREFIX_TO_CATEGORY.items():
        if rule_id.startswith(prefix):
            return category
    return "unknown"


def _strip_quotes(value: str) -> str:
    """Strip a single matching pair of surrounding quotes from a value.

    ``_parse_frontmatter`` is a lightweight regex-based parser (not a full
    YAML parser), so unlike ``yaml.safe_load`` it does not unquote scalar
    values on its own. Without this, a frontmatter value like
    ``rule_id: "R114"`` would be captured verbatim as ``'"R114"'``,
    diverging from the YAML-parsed (quote-stripped) value used elsewhere
    (e.g. ``_parse_ai_prompt_map``) and causing lookups for the same
    rule to silently miss.

    Args:
        value: Raw captured frontmatter value, already stripped of
            surrounding whitespace by ``_KV_RE``.

    Returns:
        The value with one matching pair of leading/trailing single or
        double quotes removed, or the original value if unquoted.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a markdown file.

    Args:
        path: Path to the ``.md`` file.

    Returns:
        Dict of frontmatter key-value pairs, or empty dict if none found.
        Values are unquoted (see ``_strip_quotes``) so a quoted
        ``rule_id: "R114"`` normalizes the same as the unquoted
        ``rule_id: R114`` form and matches ``yaml.safe_load``-based
        parsers elsewhere in this module.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    return {key: _strip_quotes(value) for key, value in _KV_RE.findall(m.group(1))}


def _collect_native_rules() -> list[reporting_pb2.RuleDefinition]:
    """Collect rules from the Native validator via ``load_graph_rules()``.

    Returns:
        List of RuleDefinition protos for all native rules, including
        disabled-by-default audit rules registered with ``enabled=False``.
    """
    try:
        from apme_engine.graph.scanner import (
            DISABLED_BY_DEFAULT_GRAPH_RULE_IDS,
            load_graph_rules,
        )

        rules_dir = str(_GRAPH_RULES_DIR)
        graph_rules, _ = load_graph_rules(
            rules_dir=rules_dir,
            opt_in_rule_ids=sorted(DISABLED_BY_DEFAULT_GRAPH_RULE_IDS),
            preserve_disabled_defaults=True,
        )
        defs = []
        loaded_ids = {gr.rule_id for gr in graph_rules}
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
                    ansible_core_version=get_version_spec_str(gr.rule_id),
                )
            )
        missing_disabled = DISABLED_BY_DEFAULT_GRAPH_RULE_IDS - loaded_ids
        if missing_disabled:
            for fm_def in _collect_from_frontmatter(_GRAPH_RULES_DIR, "native"):
                if fm_def.rule_id in missing_disabled:
                    defs.append(
                        reporting_pb2.RuleDefinition(
                            rule_id=fm_def.rule_id,
                            default_severity=fm_def.default_severity,
                            category=fm_def.category,
                            source=fm_def.source,
                            description=fm_def.description,
                            scope=fm_def.scope,
                            enabled=False,
                            ansible_core_version=fm_def.ansible_core_version,
                        )
                    )
        logger.info("Collected %d native rules", len(defs))
        return defs
    except Exception:
        logger.warning(
            "Failed to collect native rules via load_graph_rules; falling back to frontmatter",
            exc_info=True,
        )
        return _collect_from_frontmatter(_GRAPH_RULES_DIR, "native")


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
                ansible_core_version=get_version_spec_str(rule_id),
            )
        )
    logger.info("Collected %d %s rules from frontmatter", len(defs), source)
    return defs


def _parse_ai_prompt_map(rule_dirs: Iterable[Path]) -> dict[str, str]:
    """Parse ``ai_prompt`` frontmatter across a set of rule-doc directories.

    This is the single shared implementation for collecting AI-remediation
    guidance from rule markdown frontmatter. Both :func:`_load_rule_guidance_map`
    (this module's public API) and ``AbbenayProvider`` (AI-assisted
    remediation) call this function so the two never drift apart.

    Precedence is **first-write-wins**: if the same ``rule_id`` is declared
    in more than one directory, the entry from the directory earliest in
    ``rule_dirs`` is kept. Callers should pass directories in
    native -> OPA -> Ansible order to match :func:`_index_rule_doc_files`.

    Args:
        rule_dirs: Directories to search, in precedence order (first wins).

    Returns:
        Mapping of rule_id to stripped ``ai_prompt`` text, for rules that
        define one.
    """
    guidance: dict[str, str] = {}
    for rule_dir in rule_dirs:
        rule_dir = Path(rule_dir)
        if not rule_dir.is_dir():
            continue
        for md_path in sorted(rule_dir.glob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            m = _FRONTMATTER_RE.match(text)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1))
            except yaml.YAMLError:
                logger.warning("Failed to parse YAML frontmatter in %s", md_path)
                continue
            if not isinstance(fm, dict):
                continue
            rule_id = fm.get("rule_id", "")
            ai_prompt = fm.get("ai_prompt", "")
            if rule_id and ai_prompt and str(rule_id) not in guidance:
                guidance[str(rule_id)] = str(ai_prompt).strip()
    return guidance


@functools.lru_cache(maxsize=1)
def _load_rule_guidance_map() -> dict[str, str]:
    """Load ``ai_prompt`` remediation guidance from rule doc frontmatter.

    Walks the native, OPA, and Ansible rule-doc directories (in that
    precedence order, first-write-wins — see :func:`_parse_ai_prompt_map`)
    parsing YAML frontmatter with ``yaml.safe_load`` (multiline-safe,
    unlike the lightweight regex used by ``_parse_frontmatter``). The
    result is cached for the process lifetime.

    Returns:
        Mapping of rule_id to stripped ``ai_prompt`` text, for rules that
        define one.
    """
    return _parse_ai_prompt_map((_GRAPH_RULES_DIR, _OPA_BUNDLE_DIR, _ANSIBLE_RULES_DIR))


def get_rule_guidance(rule_id: str) -> str | None:
    """Get AI-remediation guidance text for a rule (public API).

    This is the stable, public way for downstream consumers (e.g. projects
    depending on ``apme-engine``) to retrieve the same ``ai_prompt``
    guidance APME's own AI-assisted remediation uses, without reaching
    into private modules or parsing rule markdown files directly.

    Args:
        rule_id: Rule identifier, e.g. ``"R114"``, ``"L026"``, or
            ``"SEC:generic-api-key"``. A legacy validator-prefixed form
            such as ``"native:R114"`` is also accepted; only the known
            ``native:``/``opa:``/``ansible:``/``gitleaks:`` prefixes are
            stripped (see :func:`apme_engine.fingerprint.canonicalize_rule_id`)
            so IDs whose colon is part of the ID itself (e.g. ``SEC:*``)
            are preserved as-is.

    Returns:
        The rule's ``ai_prompt`` guidance text if the rule defines one,
        else ``None`` (either the rule is unknown or has no AI guidance).

    Example:
        >>> from apme_engine.rule_catalog import get_rule_guidance
        >>> guidance = get_rule_guidance("R114")
        >>> print(guidance)
    """
    bare_id = canonicalize_rule_id(rule_id)
    return _load_rule_guidance_map().get(bare_id)


def list_rules_with_guidance() -> list[str]:
    """List rule IDs that have AI-remediation guidance defined (public API).

    Returns:
        Sorted list of rule IDs whose markdown frontmatter defines an
        ``ai_prompt``.
    """
    return sorted(_load_rule_guidance_map())


@functools.lru_cache(maxsize=1)
def _index_rule_doc_files() -> dict[str, Path]:
    """Build a rule_id -> markdown file path index across all rule-doc dirs.

    Reads each file's frontmatter (not just its filename) so the index is
    correct even when a filename doesn't embed the rule_id verbatim.

    Returns:
        Mapping of rule_id to its ``.md`` doc path. When multiple files
        declare the same rule_id, the first one found wins (directories
        are walked in native -> opa -> ansible order).
    """
    index: dict[str, Path] = {}
    for rule_dir in (_GRAPH_RULES_DIR, _OPA_BUNDLE_DIR, _ANSIBLE_RULES_DIR):
        if not rule_dir.is_dir():
            continue
        for md_path in sorted(rule_dir.glob("*.md")):
            fm = _parse_frontmatter(md_path)
            rule_id = fm.get("rule_id", "")
            if rule_id and rule_id not in index:
                index[rule_id] = md_path
    return index


def get_rule_documentation(rule_id: str) -> str | None:
    """Get the full markdown documentation body for a rule (public API).

    Unlike :func:`get_rule_guidance` (which returns only the short
    ``ai_prompt`` frontmatter field used for AI-assisted remediation),
    this returns the full human-readable docs below the frontmatter:
    description, pass/fail examples, and detailed remediation steps —
    i.e. everything you'd see reading the rule's ``.md`` file directly,
    minus the YAML frontmatter block itself.

    Args:
        rule_id: Rule identifier, e.g. ``"R114"``, ``"L026"``, or
            ``"SEC:generic-api-key"``. A legacy validator-prefixed form
            such as ``"native:R114"`` is also accepted; only the known
            ``native:``/``opa:``/``ansible:``/``gitleaks:`` prefixes are
            stripped (see :func:`apme_engine.fingerprint.canonicalize_rule_id`)
            so IDs whose colon is part of the ID itself (e.g. ``SEC:*``)
            are preserved as-is.

    Returns:
        The markdown body text (frontmatter stripped) if the rule has a
        doc file, else ``None``.

    Example:
        >>> from apme_engine.rule_catalog import get_rule_documentation
        >>> print(get_rule_documentation("R114"))
    """
    bare_id = canonicalize_rule_id(rule_id)
    md_path = _index_rule_doc_files().get(bare_id)
    if md_path is None:
        return None
    text = md_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text.strip()
    return text[m.end() :].strip()


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
