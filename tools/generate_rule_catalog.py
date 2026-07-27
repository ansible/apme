#!/usr/bin/env python3
"""Generate docs/rules/RULE_CATALOG.md and REMEDIATION_TIER_REPORT.md.

Discovers rules from OPA (.rego), Native (GraphRule subclasses), Ansible
(explicit imports), and Gitleaks.  For each rule, reports:

  - validator, description, severity (ADR-043), scope, remediation tier
  - whether a deterministic fixer (transform) exists
  - whether the implementation exists (code, not just a doc stub)
  - whether tests exist (Rego _test.rego or pytest files referencing the ID)

Run from repo root:  python tools/generate_rule_catalog.py
Or via prek hook:    triggered on rule source changes.

Outputs:
  - docs/rules/RULE_CATALOG.md
  - docs/rules/REMEDIATION_TIER_REPORT.md
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

NATIVE_DIR = REPO_ROOT / "src" / "apme_engine" / "graph" / "rules"
OPA_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"
ANSIBLE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "ansible" / "rules"
GITLEAKS_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "gitleaks"
TESTS_DIR = REPO_ROOT / "tests"
INTEGRATION_TEST = TESTS_DIR / "rule_doc_integration_test.py"
TRANSFORMS_INIT = REPO_ROOT / "src" / "apme_engine" / "remediation" / "transforms" / "__init__.py"
OUTPUT = REPO_ROOT / "docs" / "rules" / "RULE_CATALOG.md"
REMEDIATION_OUTPUT = REPO_ROOT / "docs" / "rules" / "REMEDIATION_TIER_REPORT.md"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from rule_analysis import (  # noqa: E402
    RuleMetadata,
    enrich_rule,
)

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_KV = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_NATIVE_RULE_ID = re.compile(r'rule_id:\s*str\s*=\s*"([^"]+)"')
_OPA_RULE_ID = re.compile(r'"rule_id":\s*"([^"]+)"')
_ANSIBLE_CONST = re.compile(r'RULE_ID\s*=\s*"([^"]+)"')
_ANSIBLE_INLINE = re.compile(r'"rule_id":\s*"([^"]+)"')
_REG_CALL = re.compile(r'reg\.register\(\s*"([^"]+)"')

_SKIP_NATIVE_STEMS = {"__init__", "base_rule", "sample_rule", "graph_rule_base", "_module_risk_mapping"}


@dataclass
class Rule:
    """A single rule entry for the catalog."""

    rule_id: str
    validator: str
    description: str
    severity: str = ""
    has_impl: bool = False
    has_test: bool = False
    has_fixer: bool = False
    has_doc: bool = False
    impl_file: str = ""
    test_files: list[str] = field(default_factory=list)


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML-ish frontmatter from a markdown file.

    Args:
        path: Path to the markdown file.

    Returns:
        Dict of frontmatter key-value pairs.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    pairs = _KV.findall(m.group(1))
    return {k: v.strip("\"'") for k, v in pairs}


def _get_severity(rule_id: str) -> str:
    """Look up severity from apme_engine.graph.severity.

    Args:
        rule_id: Rule identifier.

    Returns:
        Severity label string.
    """
    try:
        from apme_engine.graph.severity import get_severity, severity_to_label

        return severity_to_label(get_severity(rule_id))
    except Exception:
        return ""


def _get_fixable_ids() -> set[str]:
    """Return set of rule_ids that have deterministic fixers.

    Returns:
        Set of rule_id strings with registered transforms.
    """
    try:
        from apme_engine.remediation.transforms import build_default_registry

        reg = build_default_registry()
        return set(reg.rule_ids)
    except Exception:
        pass

    if TRANSFORMS_INIT.exists():
        src = TRANSFORMS_INIT.read_text(encoding="utf-8")
        ids = set(_REG_CALL.findall(src))
        if ids:
            print(f"NOTE: loaded {len(ids)} fixer IDs from source (runtime import unavailable)", file=sys.stderr)
            return ids

    print("WARNING: could not determine fixable rule IDs", file=sys.stderr)
    return set()


def _find_pytest_files_for_rule(rule_id: str) -> list[str]:
    """Find pytest files that reference a specific rule ID.

    Searches test file contents for the quoted rule_id string.  Only scans
    Python files under tests/.

    Args:
        rule_id: Rule identifier to search for.

    Returns:
        List of test filenames (relative to tests/) that reference this rule.
    """
    matches: list[str] = []
    if not TESTS_DIR.is_dir():
        return matches
    pat = re.compile(rf'(?:"|\'|rule_id["\s:=]+){re.escape(rule_id)}(?:"|\')')
    for py in sorted(TESTS_DIR.rglob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pat.search(text):
            matches.append(py.relative_to(TESTS_DIR).as_posix())
    return matches


_TEST_CACHE: dict[str, list[str]] | None = None


def _get_test_cache() -> dict[str, list[str]]:
    """Build a cache of rule_id -> list of test files.

    Rules in _GRAPH_RULE_KNOWN_FAILURES whose only test reference is
    rule_doc_integration_test.py are excluded — a skip is not a test.

    Returns:
        Dict mapping rule_id strings to lists of test filenames.
    """
    global _TEST_CACHE  # noqa: PLW0603
    if _TEST_CACHE is not None:
        return _TEST_CACHE

    cache: dict[str, list[str]] = {}
    if not TESTS_DIR.is_dir():
        _TEST_CACHE = cache
        return cache

    all_rule_id_pat = re.compile(r'["\']((?:A|L|M|R|P)\d+|SEC:[a-zA-Z0-9_*-]+)["\']')
    for py in sorted(TESTS_DIR.rglob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = py.relative_to(TESTS_DIR).as_posix()
        for m in all_rule_id_pat.finditer(text):
            rid = m.group(1)
            cache.setdefault(rid, [])
            if rel not in cache[rid]:
                cache[rid].append(rel)

    known_failures = _get_known_failure_ids()
    for rid in known_failures:
        files = cache.get(rid, [])
        real = [f for f in files if f != "rule_doc_integration_test.py"]
        if real:
            cache[rid] = real
        else:
            cache.pop(rid, None)

    _TEST_CACHE = cache
    return cache


def _get_known_failure_ids() -> set[str]:
    """Parse rule IDs from _GRAPH_RULE_KNOWN_FAILURES in the integration test.

    Returns:
        Set of rule_id strings that are known integration-test skips.
    """
    if not INTEGRATION_TEST.exists():
        return set()
    text = INTEGRATION_TEST.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'^\s*"([A-Z]\d+)":', text, re.MULTILINE))


def _find_doc(rule_id: str, doc_dir: Path) -> Path | None:
    """Find the .md doc file for a rule ID.

    Args:
        rule_id: Rule ID to find.
        doc_dir: Directory to search.

    Returns:
        Path to .md file or None.
    """
    exact = doc_dir / f"{rule_id}.md"
    if exact.exists():
        return exact
    for md in doc_dir.glob("*.md"):
        if md.name == "README.md":
            continue
        if md.stem.startswith(rule_id):
            return md
    return None


def _collect_opa_rules() -> list[Rule]:
    """Collect rules from OPA bundle.

    Returns:
        List of Rule objects for OPA rules.
    """
    rules: list[Rule] = []
    for rego in sorted(OPA_DIR.glob("*.rego")):
        if rego.name.endswith("_test.rego") or rego.name.startswith("_"):
            continue
        text = rego.read_text(encoding="utf-8", errors="replace")
        m = _OPA_RULE_ID.search(text)
        if not m:
            continue
        rid = m.group(1)
        doc = _find_doc(rid, OPA_DIR)
        fm = _parse_frontmatter(doc) if doc else {}
        test_rego = OPA_DIR / f"{rego.stem}_test.rego"
        test_cache = _get_test_cache()
        pytest_files = test_cache.get(rid, [])

        rules.append(
            Rule(
                rule_id=rid,
                validator="OPA",
                description=fm.get("description", ""),
                has_impl=True,
                impl_file=rego.name,
                has_doc=doc is not None,
                has_test=test_rego.exists() or bool(pytest_files),
                test_files=([test_rego.name] if test_rego.exists() else []) + pytest_files,
            )
        )
    return rules


def _collect_native_rules() -> list[Rule]:
    """Collect rules from Native validator Python files.

    Returns:
        List of Rule objects for Native rules.
    """
    rules: list[Rule] = []
    doc_only_ids: set[str] = set()

    for py in sorted(NATIVE_DIR.glob("*.py")):
        if py.name.endswith("_test.py") or py.stem in _SKIP_NATIVE_STEMS:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        m = _NATIVE_RULE_ID.search(text)
        if not m:
            continue
        rid = m.group(1)
        doc = _find_doc(rid, NATIVE_DIR)
        fm = _parse_frontmatter(doc) if doc else {}
        test_cache = _get_test_cache()
        pytest_files = test_cache.get(rid, [])

        rules.append(
            Rule(
                rule_id=rid,
                validator="Native",
                description=fm.get("description", ""),
                has_impl=True,
                impl_file=py.name,
                has_doc=doc is not None,
                has_test=bool(pytest_files),
                test_files=pytest_files,
            )
        )
        doc_only_ids.add(rid)

    for md in sorted(NATIVE_DIR.glob("*.md")):
        if md.name == "README.md" or md.name == "sample_rule.md":
            continue
        fm = _parse_frontmatter(md)
        rid = fm.get("rule_id", "")
        if not rid or rid in doc_only_ids:
            continue
        test_cache = _get_test_cache()
        pytest_files = test_cache.get(rid, [])

        rules.append(
            Rule(
                rule_id=rid,
                validator="Native",
                description=fm.get("description", ""),
                has_impl=False,
                impl_file="",
                has_doc=True,
                has_test=bool(pytest_files),
                test_files=pytest_files,
            )
        )

    return rules


def _collect_ansible_rules() -> list[Rule]:
    """Collect rules from Ansible validator.

    Returns:
        List of Rule objects for Ansible rules.
    """
    rules: list[Rule] = []
    if not ANSIBLE_DIR.is_dir():
        return rules

    for py in sorted(ANSIBLE_DIR.glob("*.py")):
        if py.name.startswith("_") or py.name.endswith("_test.py"):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        found_ids: set[str] = set()
        for m in _ANSIBLE_CONST.finditer(text):
            found_ids.add(m.group(1))
        for m in _ANSIBLE_INLINE.finditer(text):
            found_ids.add(m.group(1))

        for rid in sorted(found_ids):
            doc = _find_doc(rid, ANSIBLE_DIR)
            fm = _parse_frontmatter(doc) if doc else {}
            test_cache = _get_test_cache()
            pytest_files = test_cache.get(rid, [])

            rules.append(
                Rule(
                    rule_id=rid,
                    validator="Ansible",
                    description=fm.get("description", ""),
                    has_impl=True,
                    impl_file=py.name,
                    has_doc=doc is not None,
                    has_test=bool(pytest_files),
                    test_files=pytest_files,
                )
            )

    return rules


def _collect_gitleaks_rules() -> list[Rule]:
    """Return a single Gitleaks rule entry.

    Returns:
        List with one Rule for Gitleaks SEC:*.
    """
    return [
        Rule(
            rule_id="SEC:*",
            validator="Gitleaks",
            description="Secret/credential detection (delegated to Gitleaks binary).",
            has_impl=True,
            impl_file="scanner.py",
            has_doc=False,
            has_test=any(k.startswith("SEC:") for k in _get_test_cache()),
        )
    ]


def _sort_key(rule: Rule) -> tuple[str, int]:
    """Return (prefix, number) for sorting rules by rule_id.

    Args:
        rule: Rule object.

    Returns:
        Tuple of (letter prefix, numeric portion) for sort ordering.
    """
    rid = rule.rule_id
    prefix = rid.rstrip("0123456789:*")
    num_str = rid[len(prefix) :].split(":")[0].split("*")[0]
    num = int(num_str) if num_str.isdigit() else 9999
    return (prefix, num)


def _escape_md_cell(text: str) -> str:
    r"""Escape unescaped pipe characters for use inside markdown table cells.

    Idempotent: already-escaped pipes (``\|``) are left unchanged.

    Args:
        text: Raw cell text.

    Returns:
        Text with unescaped ``|`` escaped as ``\|``.
    """
    return re.sub(r"(?<!\\)\|", r"\\|", text)


def _status_icon(ok: bool) -> str:
    """Return a checkmark or X for boolean status.

    Args:
        ok: Status value.

    Returns:
        Status indicator string.
    """
    return "Yes" if ok else "—"


def _collect_all_rules() -> list[Rule]:
    """Discover and sort all rules from every validator.

    Returns:
        Sorted list of Rule objects.
    """
    all_rules: list[Rule] = []
    all_rules.extend(_collect_opa_rules())
    all_rules.extend(_collect_native_rules())
    all_rules.extend(_collect_ansible_rules())
    all_rules.extend(_collect_gitleaks_rules())
    all_rules.sort(key=_sort_key)
    fixable = _get_fixable_ids()
    for r in all_rules:
        r.has_fixer = r.rule_id in fixable
        r.severity = _get_severity(r.rule_id)
    return all_rules


def _enrich_all(rules: list[Rule]) -> list[RuleMetadata]:
    """Attach scope, tier, and severity-source metadata to discovered rules."""
    return [enrich_rule(r.rule_id, r.validator, r.description, r.has_fixer) for r in rules]


def _prefix_section() -> list[str]:
    """Return markdown lines for rule ID prefix conventions."""
    lines = [
        "## Rule ID Conventions",
        "",
        "Rule IDs identify the **type of check**, not the validator that runs it.",
        "The prefix is orthogonal to severity — see [ADR-043](../../.sdlc/adrs/ADR-043-default-severity-assignment.md).",
        "",
        "| Prefix | Category | ID ranges |",
        "|--------|----------|-----------|",
        "| **L** | Lint (style, correctness, best practice) | L001–L199 |",
        "| **M** | Modernize (ansible-core migration) | M001–M099 |",
        "| **R** | Risk / security (annotation-based) | R001–R499 |",
        "| **P** | Policy (legacy runtime validation) | P001–P099 |",
        "| **A** | AAP-specific (platform compatibility) | A001–A099 |",
        "| **SEC:** | Secrets (Gitleaks) | SEC:* |",
        "",
        "For ansible-lint name cross-references and historical renumbering, see",
        "[LINT_RULE_MAPPING.md](LINT_RULE_MAPPING.md).",
        "",
    ]
    return lines


def _severity_section() -> list[str]:
    """Return markdown lines for ADR-043 severity scale."""
    return [
        "## Severity Scale (ADR-043)",
        "",
        "Default severity is assigned from `src/apme_engine/severity_defaults.py` using this decision tree:",
        "",
        "1. Security vulnerability? → **critical**",
        "2. Runtime breakage today? → **error**",
        "3. Imminent breakage or risky behavior? → **high**",
        "4. Probably a bug or anti-pattern? → **medium**",
        "5. Best practice / convention? → **low**",
        "6. Informational / style? → **info**",
        "",
        "Category prefix does **not** determine severity. For example, L057 (syntax) is **error**",
        "while L060 (line length) is **info** — both are L-rules.",
        "",
    ]


def _remediation_section(meta: list[RuleMetadata]) -> list[str]:
    """Return markdown lines summarizing remediation tiers."""
    auto = sum(1 for m in meta if m.remediation_tier == "auto")
    ai = sum(1 for m in meta if m.remediation_tier == "ai")
    manual = sum(1 for m in meta if m.remediation_tier == "manual")
    return [
        "## Remediation Tiers",
        "",
        "Routing follows `src/apme_engine/remediation/partition.py` (ADR-026 scope metadata):",
        "",
        "| Tier | Label | Count | Routing |",
        "|------|-------|-------|---------|",
        f"| 1 | auto | {auto} | Deterministic transform in registry — applied by `apme remediate` |",
        f"| 2 | ai | {ai} | Task/block scope, no fixer — AI proposes patch (Abbenay) |",
        f"| 3 | manual | {manual} | Play/role/collection scope, cross-file, or info severity |",
        "",
        "Full analysis and promotion candidates: [REMEDIATION_TIER_REPORT.md](REMEDIATION_TIER_REPORT.md).",
        "",
    ]


def generate() -> str:
    """Collect all rules and return RULE_CATALOG.md content.

    Returns:
        Full markdown content for docs/rules/RULE_CATALOG.md.
    """
    all_rules = _collect_all_rules()
    meta = _enrich_all(all_rules)
    meta_by_id = {m.rule_id: m for m in meta}

    total = len(all_rules)
    impl_count = sum(1 for r in all_rules if r.has_impl)
    test_count = sum(1 for r in all_rules if r.has_test)
    doc_count = sum(1 for r in all_rules if r.has_doc)
    fixer_count = sum(1 for r in all_rules if r.has_fixer)
    validators = len({r.validator for r in all_rules})
    severity_resolved = sum(1 for m in meta if m.severity_source != "fallback")
    severity_fallback = sum(1 for m in meta if m.severity_source == "fallback")

    lines = [
        "# Rule Catalog",
        "",
        "<!-- AUTO-GENERATED by tools/generate_rule_catalog.py — do not edit by hand -->",
        "",
        f"**{total} rules** across {validators} validators — the single reference for every rule.",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Implemented | {impl_count}/{total} |",
        f"| Tested | {test_count}/{total} |",
        f"| Documented | {doc_count}/{total} |",
        f"| Deterministic fixer (Tier 1) | {fixer_count}/{total} |",
        f"| Severity resolved (ADR-043 table or SEC: prefix) | {severity_resolved}/{total} |",
        f"| Severity fallback (medium default) | {severity_fallback}/{total} |",
        "",
    ]
    lines.extend(_prefix_section())
    lines.extend(_severity_section())
    lines.extend(_remediation_section(meta))
    lines.extend(
        [
            "## All Rules",
            "",
            "| Rule ID | Cat | Validator | Severity | Scope | Tier | Description | Impl | Test | Doc | Fix |",
            "|---------|-----|-----------|----------|-------|------|-------------|------|------|-----|-----|",
        ]
    )

    for r in all_rules:
        m = meta_by_id[r.rule_id]
        cat = r.rule_id.rstrip("0123456789:*").replace("SEC:", "SEC")
        lines.append(
            f"| {r.rule_id} | {cat} | {r.validator} | {r.severity} | {m.scope} | {m.remediation_tier} "
            f"| {_escape_md_cell(r.description)} "
            f"| {_status_icon(r.has_impl)} | {_status_icon(r.has_test)} "
            f"| {_status_icon(r.has_doc)} | {_status_icon(r.has_fixer)} |"
        )

    lines.append("")
    lines.append("## By Validator")
    lines.append("")

    validators_map: dict[str, list[Rule]] = {}
    for r in all_rules:
        validators_map.setdefault(r.validator, []).append(r)

    for vname in ("OPA", "Native", "Ansible", "Gitleaks"):
        vrules = validators_map.get(vname, [])
        if not vrules:
            continue
        v_impl = sum(1 for r in vrules if r.has_impl)
        v_test = sum(1 for r in vrules if r.has_test)
        v_fix = sum(1 for r in vrules if r.has_fixer)
        lines.append(f"### {vname} ({len(vrules)} rules, {v_impl} impl, {v_test} tested, {v_fix} fixers)")
        lines.append("")
        lines.append("| Rule ID | Severity | Scope | Tier | Description | Impl | Test | Doc | Fix |")
        lines.append("|---------|----------|-------|------|-------------|------|------|-----|-----|")
        for r in vrules:
            m = meta_by_id[r.rule_id]
            lines.append(
                f"| {r.rule_id} | {r.severity} | {m.scope} | {m.remediation_tier} "
                f"| {_escape_md_cell(r.description)} "
                f"| {_status_icon(r.has_impl)} | {_status_icon(r.has_test)} "
                f"| {_status_icon(r.has_doc)} | {_status_icon(r.has_fixer)} |"
            )
        lines.append("")

    lines.append("## Coverage Gaps")
    lines.append("")

    no_impl = [r for r in all_rules if not r.has_impl]
    no_test = [r for r in all_rules if not r.has_test and r.has_impl]
    no_doc = [r for r in all_rules if not r.has_doc and r.has_impl]
    no_severity = [m for m in meta if m.severity_source == "fallback"]
    adr_mismatch = [m for m in meta if m.adr043_mismatch]

    if no_impl:
        lines.append(f"### Doc-only rules (no implementation) — {len(no_impl)}")
        lines.append("")
        for r in no_impl:
            lines.append(f"- **{r.rule_id}** ({r.validator}): {r.description}")
        lines.append("")

    if no_test:
        lines.append(f"### Implemented but untested — {len(no_test)}")
        lines.append("")
        for r in no_test:
            lines.append(f"- **{r.rule_id}** ({r.validator}): {r.description}")
        lines.append("")

    if no_doc:
        lines.append(f"### Implemented but undocumented — {len(no_doc)}")
        lines.append("")
        for r in no_doc:
            lines.append(f"- **{r.rule_id}** ({r.validator}): {r.description}")
        lines.append("")

    if no_severity:
        lines.append(f"### Missing from severity_defaults.py (fallback to medium) — {len(no_severity)}")
        lines.append("")
        for m in no_severity:
            lines.append(f"- **{m.rule_id}** ({m.validator}): add to `SEVERITY_DEFAULTS` per ADR-043")
        lines.append("")

    if adr_mismatch:
        lines.append(f"### ADR-043 representative examples differ from assignment — {len(adr_mismatch)}")
        lines.append("")
        for m in adr_mismatch:
            lines.append(f"- **{m.rule_id}**: {m.adr043_mismatch}")
        lines.append("")

    if not no_impl and not no_test and not no_doc and not no_severity and not adr_mismatch:
        lines.append("All rules are implemented, tested, documented, and severity-assigned.")
        lines.append("")

    lines.append("## Fixer Summary (Tier 1)")
    lines.append("")
    lines.append("Deterministic fixers are auto-applied by `apme remediate`.")
    lines.append("")
    lines.append("| Rule ID | Severity | Scope | Description |")
    lines.append("|---------|----------|-------|-------------|")

    for r in all_rules:
        if r.has_fixer:
            m = meta_by_id[r.rule_id]
            lines.append(f"| {r.rule_id} | {r.severity} | {m.scope} | {_escape_md_cell(r.description)} |")

    lines.append("")
    return "\n".join(lines)


def generate_remediation_report() -> str:
    """Return REMEDIATION_TIER_REPORT.md content.

    Returns:
        Full markdown for the remediation tier analysis report.
    """
    all_rules = _collect_all_rules()
    meta = _enrich_all(all_rules)

    auto = [m for m in meta if m.remediation_tier == "auto"]
    ai = [m for m in meta if m.remediation_tier == "ai"]
    manual = [m for m in meta if m.remediation_tier == "manual"]
    auto_candidates = [m for m in meta if m.auto_candidate and not m.has_fixer]
    ai_promote = [m for m in auto_candidates if m.remediation_tier == "ai"]
    manual_promote = [m for m in auto_candidates if m.remediation_tier == "manual"]

    lines = [
        "# Remediation Tier Report",
        "",
        "<!-- AUTO-GENERATED by tools/generate_rule_catalog.py — do not edit by hand -->",
        "",
        "Analysis of default remediation routing for all rules. Regenerate with:",
        "`python tools/generate_rule_catalog.py`",
        "",
        "## Summary",
        "",
        f"| Tier | Count | % of {len(meta)} |",
        "|------|-------|--------|",
        f"| Tier 1 — auto (has transform) | {len(auto)} | {len(auto) * 100 // len(meta)}% |",
        f"| Tier 2 — AI (task/block, no fixer) | {len(ai)} | {len(ai) * 100 // len(meta)}% |",
        f"| Tier 3 — manual | {len(manual)} | {len(manual) * 100 // len(meta)}% |",
        "",
        "### Promotion potential",
        "",
        f"- **{len(ai_promote)}** Tier 2 rules could likely move to Tier 1 (mechanical transforms)",
        f"- **{len(manual_promote)}** Tier 3 rules have mechanical fixes blocked by scope",
        f"- **{len(ai)} - {len(ai_promote)} = {len(ai) - len(ai_promote)}** Tier 2 rules should stay AI (judgment/security)",
        "",
        "### Tier 3 breakdown",
        "",
        "| Reason | Count |",
        "|--------|-------|",
        f"| info severity | {sum(1 for m in manual if m.severity == 'info')} |",
        f"| playbook scope | {sum(1 for m in manual if m.scope == 'playbook')} |",
        f"| collection scope | {sum(1 for m in manual if m.scope == 'collection')} |",
        f"| role scope | {sum(1 for m in manual if m.scope == 'role')} |",
        f"| play scope | {sum(1 for m in manual if m.scope == 'play')} |",
        f"| inventory scope | {sum(1 for m in manual if m.scope == 'inventory')} |",
        f"| cross-file (R111/R112) | {sum(1 for m in manual if m.rule_id in {'R111', 'R112'})} |",
        "",
        f"## Tier 1 — Auto ({len(auto)} rules)",
        "",
        "| Rule ID | Validator | Severity | Scope | Description |",
        "|---------|-----------|----------|-------|-------------|",
    ]
    rule_by_id = {r.rule_id: r for r in all_rules}
    for m in sorted(auto, key=lambda x: x.rule_id):
        r = rule_by_id[m.rule_id]
        lines.append(f"| {m.rule_id} | {r.validator} | {m.severity} | {m.scope} | {_escape_md_cell(m.description)} |")

    lines.extend(
        [
            "",
            f"## Tier 2 — AI ({len(ai)} rules)",
            "",
            "### Could move to Tier 1 auto",
            "",
            "| Rule ID | Severity | Scope | Proposed transform |",
            "|---------|----------|-------|-------------------|",
        ]
    )
    for m in sorted(ai_promote, key=lambda x: x.rule_id):
        lines.append(f"| {m.rule_id} | {m.severity} | {m.scope} | {m.auto_candidate} |")

    lines.extend(
        [
            "",
            "### Should stay AI",
            "",
            "| Rule ID | Severity | Scope | Reason |",
            "|---------|----------|-------|--------|",
        ]
    )
    for m in sorted([x for x in ai if not x.auto_candidate], key=lambda x: x.rule_id):
        lines.append(f"| {m.rule_id} | {m.severity} | {m.scope} | {_escape_md_cell(m.remediation_reason)} |")

    lines.extend(
        [
            "",
            f"## Tier 3 — Manual ({len(manual)} rules)",
            "",
            "| Rule ID | Validator | Severity | Scope | Auto candidate? | Reason |",
            "|---------|-----------|----------|-------|-----------------|--------|",
        ]
    )
    for m in sorted(manual, key=lambda x: x.rule_id):
        r = rule_by_id[m.rule_id]
        cand = m.auto_candidate or "—"
        lines.append(
            f"| {m.rule_id} | {r.validator} | {m.severity} | {m.scope} | {cand} "
            f"| {_escape_md_cell(m.remediation_reason)} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Write RULE_CATALOG.md and REMEDIATION_TIER_REPORT.md to docs/rules/."""
    catalog = generate()
    OUTPUT.write_text(catalog, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({catalog.count(chr(10))} lines)")

    report = generate_remediation_report()
    REMEDIATION_OUTPUT.write_text(report, encoding="utf-8")
    print(f"Wrote {REMEDIATION_OUTPUT} ({report.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
