#!/usr/bin/env python3
"""Generate docs/RULE_CATALOG.md — a single-file reference of every rule.

Validator, description, severity/level, and whether a deterministic fixer exists.
Run from repo root: python scripts/generate_rule_catalog.py
The output is written to docs/RULE_CATALOG.md.  Re-run whenever rules or
transforms change.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

NATIVE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "native" / "rules"
OPA_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"
ANSIBLE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "ansible" / "rules"
GITLEAKS_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "gitleaks"
OUTPUT = REPO_ROOT / "docs" / "RULE_CATALOG.md"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_KV = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a markdown file; return key-value dict.

    Args:
        path: Path to the markdown file.

    Returns:
        Dict of frontmatter key-value pairs.

    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    return dict(_KV.findall(m.group(1)))


def _extract_opa_level(rego_path: Path) -> str:
    """Extract the first "level" value from a .rego file.

    Args:
        rego_path: Path to the .rego source file.

    Returns:
        Level string (e.g. "warning", "error") or empty string.

    """
    if not rego_path.is_file():
        return ""
    for line in rego_path.read_text(encoding="utf-8").splitlines():
        m = re.search(r'"level":\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return ""


def _extract_native_level(py_path: Path) -> str:
    """Extract Severity.XXX from a native rule .py file.

    Args:
        py_path: Path to the Python rule file.

    Returns:
        Normalised level string (e.g. "high", "very_low") or empty string.

    """
    if not py_path.is_file():
        return ""
    for line in py_path.read_text(encoding="utf-8").splitlines():
        m = re.search(r"severity:\s*str\s*=\s*Severity\.(\w+)", line)
        if m:
            return m.group(1).lower()
    return ""


def _extract_ansible_level(py_path: Path) -> str:
    """Extract the first "level" value from an Ansible validator .py file.

    Args:
        py_path: Path to the Python rule file.

    Returns:
        Level string or empty string.

    """
    if not py_path.is_file():
        return ""
    for line in py_path.read_text(encoding="utf-8").splitlines():
        m = re.search(r'"level":\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return ""


def _collect_opa_rules() -> list[dict[str, str]]:
    """Collect rule_id, validator, description, source, level from OPA bundle .md files.

    Returns:
        List of rule dicts with rule_id, validator, description, source, level.

    """
    rules = []
    for md in sorted(OPA_DIR.glob("*.md")):
        fm = _parse_frontmatter(md)
        if not fm.get("rule_id"):
            continue
        rego = md.with_suffix(".rego")
        level = _extract_opa_level(rego)
        rules.append(
            {
                "rule_id": fm["rule_id"],
                "validator": "OPA",
                "description": fm.get("description", ""),
                "source": md.name,
                "level": level,
            }
        )
    return rules


def _collect_native_rules() -> list[dict[str, str]]:
    """Collect rule_id, validator, description, source, level from native rules .md files.

    Returns:
        List of rule dicts with rule_id, validator, description, source, level.

    """
    rules = []
    for md in sorted(NATIVE_DIR.glob("*.md")):
        fm = _parse_frontmatter(md)
        if not fm.get("rule_id"):
            continue
        py_stem = md.stem
        py_candidates = list(NATIVE_DIR.glob(f"{py_stem}*.py"))
        level = _extract_native_level(py_candidates[0]) if py_candidates else ""
        rules.append(
            {
                "rule_id": fm["rule_id"],
                "validator": "Native",
                "description": fm.get("description", ""),
                "source": md.name,
                "level": level,
            }
        )
    return rules


def _collect_ansible_rules() -> list[dict[str, str]]:
    """Collect rule_id, validator, description, source, level from Ansible rules .md files.

    Returns:
        List of rule dicts with rule_id, validator, description, source, level.

    """
    rules = []
    for md in sorted(ANSIBLE_DIR.glob("*.md")):
        fm = _parse_frontmatter(md)
        if not fm.get("rule_id"):
            continue
        py_stem = md.stem
        py_candidates = list(ANSIBLE_DIR.glob(f"{py_stem}*.py"))
        level = _extract_ansible_level(py_candidates[0]) if py_candidates else ""
        rules.append(
            {
                "rule_id": fm["rule_id"],
                "validator": "Ansible",
                "description": fm.get("description", ""),
                "source": md.name,
                "level": level,
            }
        )
    return rules


def _collect_gitleaks_rules() -> list[dict[str, str]]:
    """Return a single Gitleaks rule entry.

    Returns:
        List with one rule dict for Gitleaks SEC:*.

    """
    return [
        {
            "rule_id": "SEC:*",
            "validator": "Gitleaks",
            "description": "Secret/credential detection (delegated to Gitleaks binary).",
            "source": "scanner.py",
            "level": "critical",
        }
    ]


def _get_fixable_ids() -> set[str]:
    """Load remediation registry and return set of rule_ids that have fixers.

    Returns:
        Set of rule_id strings that have deterministic fixers.

    """
    try:
        from apme_engine.remediation.transforms import build_default_registry

        reg = build_default_registry()
        return set(reg.rule_ids)
    except Exception as e:
        print(f"WARNING: could not load registry: {e}", file=sys.stderr)
        return set()


def _sort_key(rule: dict[str, str]) -> tuple[str, int]:
    """Return (prefix, number) for sorting rules by rule_id.

    Args:
        rule: Rule dict with at least a rule_id key.

    Returns:
        Tuple of (letter prefix, numeric portion) for sort ordering.

    """
    rid = rule["rule_id"]
    prefix = rid.rstrip("0123456789:*")
    num_str = rid[len(prefix) :].split(":")[0].split("*")[0]
    num = int(num_str) if num_str.isdigit() else 9999
    return (prefix, num)


def generate() -> str:
    """Collect all rules from OPA/native/Ansible/Gitleaks and return RULE_CATALOG.md content.

    Returns:
        Full markdown content for docs/RULE_CATALOG.md.

    """
    all_rules: list[dict[str, str]] = []
    all_rules.extend(_collect_opa_rules())
    all_rules.extend(_collect_native_rules())
    all_rules.extend(_collect_ansible_rules())
    all_rules.extend(_collect_gitleaks_rules())
    all_rules.sort(key=_sort_key)

    fixable = _get_fixable_ids()

    lines = [
        "# Rule Catalog",
        "",
        "<!-- AUTO-GENERATED by scripts/generate_rule_catalog.py — do not edit by hand -->",
        "",
        f"**{len(all_rules)} rules** across {len({r['validator'] for r in all_rules})} validators "
        f"| **{len(fixable)} deterministic fixers** registered",
        "",
        "## All Rules",
        "",
        "| Rule ID | Validator | Description | Level | Fixer |",
        "|---------|-----------|-------------|-------|-------|",
    ]

    for r in all_rules:
        rid = r["rule_id"]
        fixer = "Yes" if rid in fixable else ""
        level = r.get("level", "")
        lines.append(f"| {rid} | {r['validator']} | {r['description']} | {level} | {fixer} |")

    lines.append("")
    lines.append("## By Validator")
    lines.append("")

    validators: dict[str, list[dict[str, str]]] = {}
    for r in all_rules:
        validators.setdefault(r["validator"], []).append(r)

    for vname in ("OPA", "Native", "Ansible", "Gitleaks"):
        vrules = validators.get(vname, [])
        if not vrules:
            continue
        fix_count = sum(1 for r in vrules if r["rule_id"] in fixable)
        lines.append(f"### {vname} ({len(vrules)} rules, {fix_count} fixers)")
        lines.append("")
        lines.append("| Rule ID | Description | Level | Fixer |")
        lines.append("|---------|-------------|-------|-------|")
        for r in vrules:
            fixer = "Yes" if r["rule_id"] in fixable else ""
            level = r.get("level", "")
            lines.append(f"| {r['rule_id']} | {r['description']} | {level} | {fixer} |")
        lines.append("")

    lines.append("## Fixer Summary")
    lines.append("")
    lines.append("Deterministic fixers (Tier 1) are auto-applied by `apme-scan fix --apply`.")
    lines.append("Rules without fixers fall to Tier 2 (AI-proposable) or Tier 3 (manual review).")
    lines.append("")
    lines.append("| Rule ID | Transform |")
    lines.append("|---------|-----------|")

    rule_map: dict[str, str] = {r["rule_id"]: r["description"] for r in all_rules}
    for rid in sorted(fixable):
        desc = rule_map.get(rid, "")
        lines.append(f"| {rid} | {desc} |")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Write RULE_CATALOG.md to docs/."""
    content = generate()
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({content.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
