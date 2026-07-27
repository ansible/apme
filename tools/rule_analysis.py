"""Shared rule analysis for catalog and remediation reports.

Collects scope, remediation tier, and ADR-043 severity status for every
discovered rule.  Used by ``generate_rule_catalog.py`` and
``generate_remediation_tier_report.py``.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from apme_engine.graph.severity import (  # noqa: E402
    SEVERITY_DEFAULTS,
    Severity,
    get_severity,
    severity_to_label,
)

# Mirror ADR-026 scope values — keep tools importable without engine.models.
_RULE_SCOPE_NAMES: dict[str, str] = {
    "TASK": "task",
    "BLOCK": "block",
    "PLAY": "play",
    "PLAYBOOK": "playbook",
    "ROLE": "role",
    "INVENTORY": "inventory",
    "COLLECTION": "collection",
}
AI_PROPOSABLE_SCOPES = frozenset({"task", "block"})
CROSS_FILE_RULES = frozenset({"R111", "R112"})

NATIVE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "native" / "rules"
OPA_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"
ANSIBLE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "ansible" / "rules"

# ADR-008 / ADR-043 prefix → category label
PREFIX_CATEGORIES: dict[str, str] = {
    "L": "Lint (style, correctness, best practice)",
    "M": "Modernize (ansible-core migration)",
    "R": "Risk / security (annotation-based)",
    "P": "Policy (legacy runtime validation)",
    "A": "AAP-specific (platform compatibility)",
    "SEC": "Secrets (Gitleaks)",
}

# Representative ADR-043 assignments used for alignment audit
ADR043_EXPECTED: dict[str, Severity] = {
    "L003": Severity.LOW,
    "L004": Severity.HIGH,
    "L010": Severity.MEDIUM,
    "L013": Severity.MEDIUM,
    "L020": Severity.HIGH,
    "L031": Severity.HIGH,
    "L037": Severity.MEDIUM,
    "L038": Severity.MEDIUM,
    "L039": Severity.MEDIUM,
    "L042": Severity.INFO,
    "L047": Severity.HIGH,
    "L057": Severity.ERROR,
    "L058": Severity.ERROR,
    "L059": Severity.ERROR,
    "L060": Severity.INFO,
    "L095": Severity.ERROR,
    "L098": Severity.ERROR,
    "L102": Severity.MEDIUM,
    "M001": Severity.HIGH,
    "M004": Severity.ERROR,
    "M009": Severity.HIGH,
    "M010": Severity.HIGH,
    "R101": Severity.MEDIUM,
    "R401": Severity.INFO,
    "R402": Severity.INFO,
    "R404": Severity.INFO,
    "R501": Severity.INFO,
    "P001": Severity.ERROR,
    "SEC:*": Severity.CRITICAL,
}

# Heuristic auto-promotion candidates (mechanical fixes without a transform yet)
AUTO_PROMOTION_CANDIDATES: dict[str, str] = {
    "L003": "Add play name",
    "L024": "Add generated task name",
    "L040": "Replace tabs with spaces",
    "L047": "Add no_log: true",
    "L049": "Rename loop var to item_ prefix",
    "L050": "Rename variable to lowercase_underscore",
    "L051": "Normalize Jinja spacing",
    "L060": "Wrap long lines",
    "L061": "Normalize yes/no/True/False to true/false",
    "L062": "Convert key=value to YAML mapping style",
    "L063": "Add block name",
    "L064": "Replace meta end_play with end_host",
    "L065": "Remove Jinja from play name",
    "L067": "Add verbosity to debug task",
    "L070": "Move Jinja to end of task name",
    "L072": "Add backup: true",
    "L073": "Fix YAML indentation to 2 spaces",
    "L076": "Rewrite ansible_* to ansible_facts[]",
    "L078": "Convert dot to bracket notation in Jinja",
    "L080": "Prefix internal vars with underscore",
    "L091": "Add | bool filter to bare when vars",
    "L092": "Remove loop var from task name",
    "L110": "Add no_log to debug task",
    "M014": "Rewrite ansible_* facts to ansible_facts bracket notation",
    "M015": "Replace play_hosts with ansible_play_batch",
    "M016": "Remove empty when: or add explicit condition",
    "M017": "Convert action mapping to module key",
    "M018": "Replace paramiko connection with ssh",
    "M019": "Remove !!omap/!!pairs YAML tags",
    "M020": "Replace !vault-encrypted with !vault",
    "M021": "Remove empty args: key",
    "M023": "Convert yes/no strings to true/false for follow_redirects",
    "M024": "Wrap ignore_files string in list",
    "M026": "Rename invalid inventory var names",
    "M027": "Merge inline k=v into args mapping",
}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


@dataclass
class RuleMetadata:
    """Enriched metadata for a single rule."""

    rule_id: str
    validator: str
    description: str
    category: str
    scope: str
    severity: str
    severity_enum: Severity
    severity_source: str  # "assigned" | "prefix" | "fallback"
    remediation_tier: str  # "auto" | "ai" | "manual"
    remediation_reason: str
    has_fixer: bool
    auto_candidate: str | None = None
    adr043_mismatch: str | None = None


def _parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    loaded = yaml.safe_load(m.group(1))
    if not isinstance(loaded, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in loaded.items()
        if value is not None and isinstance(value, (str, int, float, bool))
    }


def _find_doc(rule_id: str, doc_dir: Path) -> Path | None:
    exact = doc_dir / f"{rule_id}.md"
    if exact.exists():
        return exact
    for md in doc_dir.glob("*.md"):
        if md.name == "README.md":
            continue
        if md.stem.startswith(rule_id):
            return md
    return None


def rule_category(rule_id: str) -> str:
    """Return the ADR-008 category label for a rule ID."""
    if rule_id.startswith("SEC:"):
        return PREFIX_CATEGORIES["SEC"]
    prefix = rule_id.rstrip("0123456789")
    return PREFIX_CATEGORIES.get(prefix, "Other")


def get_rule_scope(rule_id: str, validator: str) -> str:
    """Look up scope from rule doc frontmatter or native class attribute."""
    dirs = {"OPA": OPA_DIR, "Native": NATIVE_DIR, "Ansible": ANSIBLE_DIR}
    d = dirs.get(validator)
    if d:
        doc = _find_doc(rule_id, d)
        if doc:
            fm = _parse_frontmatter(doc)
            if fm.get("scope"):
                return fm["scope"]
    if validator == "Native":
        for py in NATIVE_DIR.glob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            if f'"{rule_id}"' not in text and f"'{rule_id}'" not in text:
                continue
            m = re.search(r"scope:\s*str\s*=\s*RuleScope\.(\w+)", text)
            if m:
                return _RULE_SCOPE_NAMES.get(m.group(1), "task")
    return "task"


def classify_remediation_tier(
    rule_id: str,
    scope: str,
    severity: Severity,
    has_fixer: bool,
) -> tuple[str, str]:
    """Return (tier_label, reason) for a rule's default remediation routing."""
    if has_fixer:
        return "auto", "Deterministic transform registered (Tier 1)"
    if severity == Severity.INFO:
        return "manual", "Info severity — informational, no auto-fix"
    if rule_id in CROSS_FILE_RULES:
        return "manual", "Cross-file context required (R111/R112)"
    if scope not in AI_PROPOSABLE_SCOPES:
        return "manual", f"Scope '{scope}' — play/role/collection not AI-proposable"
    return "ai", "Task/block scope — AI can propose fix (Tier 2)"


def severity_source(rule_id: str) -> str:
    """Return how severity was resolved for this rule."""
    if rule_id.startswith("SEC:"):
        return "prefix"
    if rule_id in SEVERITY_DEFAULTS:
        return "assigned"
    return "fallback"


def adr043_alignment(rule_id: str, actual: Severity) -> str | None:
    """Return mismatch note if actual severity differs from ADR-043 representative."""
    expected = ADR043_EXPECTED.get("SEC:*") if rule_id.startswith("SEC:") else ADR043_EXPECTED.get(rule_id)
    if expected is None:
        return None
    if actual != expected:
        return f"ADR-043 example: {severity_to_label(expected)}, assigned: {severity_to_label(actual)}"
    return None


def enrich_rule(
    rule_id: str,
    validator: str,
    description: str,
    has_fixer: bool,
) -> RuleMetadata:
    """Build enriched metadata for one rule."""
    scope = get_rule_scope(rule_id, validator)
    sev = get_severity(rule_id)
    tier, reason = classify_remediation_tier(rule_id, scope, sev, has_fixer)
    src = severity_source(rule_id)
    auto_cand = AUTO_PROMOTION_CANDIDATES.get(rule_id) if not has_fixer else None
    mismatch = adr043_alignment(rule_id, sev)
    return RuleMetadata(
        rule_id=rule_id,
        validator=validator,
        description=description,
        category=rule_category(rule_id),
        scope=scope,
        severity=severity_to_label(sev),
        severity_enum=sev,
        severity_source=src,
        remediation_tier=tier,
        remediation_reason=reason,
        has_fixer=has_fixer,
        auto_candidate=auto_cand,
        adr043_mismatch=mismatch,
    )
