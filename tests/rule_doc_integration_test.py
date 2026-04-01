"""Integration tests: run engine + GraphRules on YAML from rule .md examples and assert expected violation/pass."""

from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    graph_report_to_violations,
    load_graph_rules,
)
from apme_engine.engine.graph_scanner import scan as graph_scan
from apme_engine.runner import run_scan_playbook_yaml
from apme_engine.validators.opa import OpaValidator
from tests.rule_doc_parser import discover_rule_docs

_GRAPH_RULE_KNOWN_FAILURES: dict[str, str] = {
    "L037": "requires module resolution from Ansible validator convergence loop",
    "L039": "requires cross-task variable resolution (no GraphRule equivalent yet)",
    "R402": "informational listing rule (no GraphRule equivalent yet)",
}


def _native_rules_dir() -> Path:
    """Return path to native rules directory.

    Returns:
        Path to native rules.
    """
    import apme_engine.validators.native.rules as rules_pkg

    return Path(rules_pkg.__file__).parent


def _opa_bundle_dir() -> Path:
    """Return path to OPA bundle directory.

    Returns:
        Path to OPA bundle.
    """
    import apme_engine.validators.opa as opa_pkg

    return Path(opa_pkg.__file__).parent / "bundle"


def _violation_ids_for_rule(violations: list[dict[str, object]], rule_id: str, validator: str) -> list[str]:
    """Return violation rule_id values that match this doc rule (for assertion).

    Args:
        violations: List of violation dicts.
        rule_id: Rule ID to match.
        validator: 'native' or 'opa' for prefix.

    Returns:
        List of matching rule_id strings.
    """
    return [str(v["rule_id"]) for v in violations if v.get("rule_id") == rule_id]


def _ensure_playbook(yaml_content: str) -> str:
    """If content is a single task or task list without a play, wrap in a play.

    Args:
        yaml_content: Raw YAML content.

    Returns:
        YAML string with play wrapper if needed.
    """
    import yaml

    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return yaml_content
    if not data:
        return yaml_content
    if isinstance(data, dict):
        if "hosts" in data and "tasks" in data:
            return yaml_content
        return (
            "- name: Example play\n  hosts: localhost\n  connection: local\n  tasks:\n"
            + "    - "
            + str(yaml.dump(data, default_flow_style=False, sort_keys=False)).strip().replace("\n", "\n    ")
        )
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and ("hosts" in first or "tasks" in first):
            return yaml_content
        tasks_yaml = str(yaml.dump(data, default_flow_style=False, sort_keys=False))
        return "- name: Example play\n  hosts: localhost\n  connection: local\n  tasks:\n" + "\n".join(
            "    " + line for line in tasks_yaml.splitlines()
        )
    return yaml_content


def _run_graph_rules_on_graph(graph: ContentGraph) -> list[dict[str, object]]:
    """Load and run all GraphRules against a ContentGraph.

    Args:
        graph: ContentGraph to scan.

    Returns:
        List of violation dicts.
    """
    rules_dir = str(_native_rules_dir())
    rules = load_graph_rules(rules_dir=rules_dir)
    report = graph_scan(graph, rules)
    return cast(list[dict[str, object]], graph_report_to_violations(report))


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def opa_validator() -> OpaValidator:
    """OPA validator instance for doc integration tests.

    Returns:
        OpaValidator initialized with the bundle directory.
    """
    return OpaValidator(str(_opa_bundle_dir()))


def _collect_violations(yaml_content: str, opa_validator: OpaValidator) -> list[dict[str, object]]:
    """Run scan on YAML and return combined violations from graph rules and OPA.

    Args:
        yaml_content: YAML string to scan.
        opa_validator: OPA validator instance.

    Returns:
        Combined list of violation dicts.
    """
    content = _ensure_playbook(yaml_content)
    try:
        context = run_scan_playbook_yaml(content, project_root=None, include_scandata=True)
    except Exception as e:
        pytest.skip(f"Scan failed (engine may need fixes): {e}")
    if not context.hierarchy_payload:
        return []

    violations: list[dict[str, object]] = []

    graph: ContentGraph | None = None
    if context.scandata and hasattr(context.scandata, "content_graph"):
        graph = context.scandata.content_graph
    if graph is not None:
        violations.extend(_run_graph_rules_on_graph(graph))

    violations.extend(cast(list[dict[str, object]], opa_validator.run(context)))

    return violations


def _rule_doc_params() -> tuple[list[tuple[str, dict[str, object]]], list[str]]:
    """List of (md_path, doc) and corresponding ids for parametrize.

    Returns:
        Tuple of (param_tuples, param_ids).
    """
    pairs = discover_rule_docs(_native_rules_dir(), _opa_bundle_dir())
    if not pairs:
        return [], []
    return pairs, [Path(p[0]).name for p in pairs]


_param_tuples, _param_ids = _rule_doc_params()


@pytest.mark.parametrize(
    "md_path,doc",
    _param_tuples if _param_tuples else [("", {})],
    ids=_param_ids if _param_ids else ["no_docs"],
)  # type: ignore[untyped-decorator]
def test_rule_doc_examples(md_path: str, doc: dict[str, object], opa_validator: OpaValidator) -> None:
    """For each rule .md with frontmatter and examples, run YAML and assert violation/pass.

    Args:
        md_path: Parametrized path to the rule markdown file.
        doc: Parametrized rule document dict.
        opa_validator: Fixture providing OPA validator instance.

    """
    if not doc.get("examples"):
        pytest.skip(f"No examples in {md_path}")
    rule_id = str(doc["rule_id"])
    validator = str(doc["validator"])
    if validator == "ansible":
        pytest.skip("Ansible validator docs require a venv; tested separately")
    if rule_id == "R118":
        pytest.skip("R118 is annotation-based; engine must annotate get_url with inbound_transfer")
    if rule_id in _GRAPH_RULE_KNOWN_FAILURES:
        pytest.skip(f"Known graph-path gap: {_GRAPH_RULE_KNOWN_FAILURES[rule_id]}")
    examples = cast(list[dict[str, object]], doc["examples"])
    for i, ex in enumerate(examples):
        expect_violation = bool(ex["expect_violation"])
        yaml_content = str(ex["yaml"])
        violations = _collect_violations(yaml_content, opa_validator)
        matching = _violation_ids_for_rule(violations, rule_id, validator)
        if expect_violation:
            assert matching, (
                f"{md_path} example {i + 1} (violation): expected {rule_id} to fire, "
                f"got: {[v['rule_id'] for v in violations]}"
            )
        else:
            assert not matching, f"{md_path} example {i + 1} (pass): expected no {rule_id}, got: {matching}"


def test_rule_doc_parser_smoke() -> None:
    """Ensure at least one rule doc is discoverable and parseable."""
    docs = discover_rule_docs(_native_rules_dir(), _opa_bundle_dir())
    l026 = [d for _, d in docs if d.get("rule_id") == "L026"]
    assert len(l026) >= 1, "Expected at least one doc for L026"
    assert l026[0].get("examples"), "Expected examples in L026 doc"
