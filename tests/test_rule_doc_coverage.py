"""Ensure every rule defined in the codebase has a corresponding .md doc file."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE_RULES_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "native" / "rules"
OPA_BUNDLE_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"
ANSIBLE_RULES_DIR = REPO_ROOT / "src" / "apme_engine" / "validators" / "ansible" / "rules"

_SKIP_NATIVE_FILES = {"__init__", "base_rule", "sample_rule"}


def _discover_native_rule_ids() -> list[tuple[str, str, Path]]:
    """Return (rule_id, source_file, expected_md_dir) for each native Python rule."""
    results = []
    pat = re.compile(r'rule_id:\s*str\s*=\s*"([^"]+)"')
    for py in NATIVE_RULES_DIR.glob("*.py"):
        if py.name.endswith("_test.py") or py.stem in _SKIP_NATIVE_FILES:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        m = pat.search(text)
        if m:
            results.append((m.group(1), py.name, NATIVE_RULES_DIR))
    return results


def _discover_opa_rule_ids() -> list[tuple[str, str, Path]]:
    """Return (rule_id, source_file, expected_md_dir) for each OPA rego rule."""
    results = []
    pat = re.compile(r'"rule_id":\s*"([^"]+)"')
    for rego in OPA_BUNDLE_DIR.glob("*.rego"):
        if rego.name.endswith("_test.rego") or rego.name.startswith("_"):
            continue
        text = rego.read_text(encoding="utf-8", errors="replace")
        m = pat.search(text)
        if m:
            results.append((m.group(1), rego.name, OPA_BUNDLE_DIR))
    return results


def _discover_ansible_rule_ids() -> list[tuple[str, str, Path]]:
    """Return (rule_id, source_file, expected_md_dir) for each ansible validator rule."""
    results = []
    if not ANSIBLE_RULES_DIR.is_dir():
        return results

    const_pat = re.compile(r'RULE_ID\s*=\s*"([^"]+)"')
    inline_pat = re.compile(r'"rule_id":\s*"([^"]+)"')

    for py in ANSIBLE_RULES_DIR.glob("*.py"):
        if py.name.startswith("_") or py.name.endswith("_test.py"):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        found_ids = set()
        for m in const_pat.finditer(text):
            found_ids.add(m.group(1))
        for m in inline_pat.finditer(text):
            found_ids.add(m.group(1))
        for rid in sorted(found_ids):
            results.append((rid, py.name, ANSIBLE_RULES_DIR))
    return results


def _find_md_for_rule(rule_id: str, md_dir: Path) -> Path | None:
    """Find the .md doc file for a rule ID in the given directory."""
    exact = md_dir / f"{rule_id}.md"
    if exact.exists():
        return exact
    for md in md_dir.glob("*.md"):
        if md.stem.startswith(rule_id):
            return md
    return None


def _collect_all_rules():
    rules = []
    rules.extend(("native", *r) for r in _discover_native_rule_ids())
    rules.extend(("opa", *r) for r in _discover_opa_rule_ids())
    rules.extend(("ansible", *r) for r in _discover_ansible_rule_ids())
    return rules


_ALL_RULES = _collect_all_rules()
_PARAM_IDS = [f"{validator}:{rule_id}" for validator, rule_id, _, _ in _ALL_RULES]


@pytest.mark.parametrize(
    "validator,rule_id,source_file,md_dir",
    _ALL_RULES if _ALL_RULES else [("skip", "skip", "skip", Path("."))],
    ids=_PARAM_IDS if _PARAM_IDS else ["no_rules"],
)
def test_rule_has_doc(validator, rule_id, source_file, md_dir):
    """Every rule must have a .md doc file."""
    if rule_id == "skip":
        pytest.skip("No rules discovered")
    md_path = _find_md_for_rule(rule_id, md_dir)
    assert md_path is not None, (
        f"Rule {rule_id} (from {source_file}, validator={validator}) "
        f"has no .md doc file in {md_dir}"
    )


def test_minimum_rule_count():
    """Sanity check: we should discover a reasonable number of rules."""
    native = _discover_native_rule_ids()
    opa = _discover_opa_rule_ids()
    ansible = _discover_ansible_rule_ids()
    assert len(native) >= 20, f"Expected >=20 native rules, got {len(native)}"
    assert len(opa) >= 24, f"Expected >=24 OPA rules, got {len(opa)}"
    assert len(ansible) >= 6, f"Expected >=6 ansible rules, got {len(ansible)}"
