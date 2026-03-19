"""In-memory rule catalog built from validator rule markdown files.

The catalog is loaded once at startup by scanning the rule .md files under
src/apme_engine/validators/.  Each file has YAML frontmatter with rule_id,
validator, and description.  We also check the remediation transform registry
to know which rules have deterministic fixers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)

_RULES_DIRS = [
    "src/apme_engine/validators/native/rules",
    "src/apme_engine/validators/opa/bundle",
    "src/apme_engine/validators/ansible/rules",
]

_FIXER_RULE_IDS = frozenset(
    {
        "L002",
        "L007",
        "L008",
        "L009",
        "L011",
        "L012",
        "L013",
        "L015",
        "L018",
        "L020",
        "L021",
        "L022",
        "L025",
        "L043",
        "L046",
        "M001",
        "M003",
        "M006",
        "M008",
        "M009",
    }
)


@dataclass
class Rule:
    rule_id: str
    validator: str
    description: str
    has_fixer: bool = False
    examples: dict[str, str] = field(default_factory=dict)


_catalog: dict[str, Rule] = {}


def _parse_rule_file(path: Path) -> Rule | None:
    """Extract rule metadata from a .md file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    front = m.group(1)
    fields: dict[str, str] = {}
    for fm in _FIELD_RE.finditer(front):
        fields[fm.group(1).strip()] = fm.group(2).strip()
    rule_id = fields.get("rule_id", "")
    if not rule_id:
        return None

    examples: dict[str, str] = {}
    for label in ("violation", "pass"):
        pattern = rf"###\s*Example:\s*{label}\s*\n```[^\n]*\n(.*?)```"
        em = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if em:
            examples[label] = em.group(1).strip()

    return Rule(
        rule_id=rule_id,
        validator=fields.get("validator", "unknown"),
        description=fields.get("description", ""),
        has_fixer=rule_id in _FIXER_RULE_IDS,
        examples=examples,
    )


def load_catalog(project_root: str | Path | None = None) -> dict[str, Rule]:
    """Scan rule .md files and build the catalog.  Cached after first call."""
    global _catalog
    if _catalog:
        return _catalog

    root = Path(project_root) if project_root else Path.cwd()
    for rules_dir in _RULES_DIRS:
        dirpath = root / rules_dir
        if not dirpath.is_dir():
            continue
        for md_file in sorted(dirpath.glob("*.md")):
            rule = _parse_rule_file(md_file)
            if rule:
                _catalog[rule.rule_id] = rule

    if "SEC:*" not in _catalog:
        _catalog["SEC:*"] = Rule(
            rule_id="SEC:*",
            validator="gitleaks",
            description="Secret/credential detection (delegated to Gitleaks binary).",
        )

    return _catalog


def get_rule(rule_id: str) -> Rule | None:
    if not _catalog:
        load_catalog()
    return _catalog.get(rule_id)


def list_rules() -> list[Rule]:
    if not _catalog:
        load_catalog()
    return sorted(_catalog.values(), key=lambda r: r.rule_id)
