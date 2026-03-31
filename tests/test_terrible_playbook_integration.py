"""Integration test: scan the vendored terrible-playbook and assert expected rules fire."""

from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    graph_report_to_violations,
    load_graph_rules,
)
from apme_engine.engine.graph_scanner import scan as graph_scan
from apme_engine.engine.models import ViolationDict
from apme_engine.runner import run_scan
from apme_engine.validators.opa import OpaValidator


def _fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "terrible-playbook"


def _native_rules_dir() -> Path:
    import apme_engine.validators.native.rules as rules_pkg

    return Path(rules_pkg.__file__).parent


def _opa_bundle_dir() -> Path:
    import apme_engine.validators.opa as opa_pkg

    return Path(opa_pkg.__file__).parent / "bundle"


EXPECTED_NATIVE_RULES = {
    "L027",
    "L036",
    "L037",
    "L043",
    "L044",
    "L045",
    "L047",
    "L048",
    "L049",
    "L051",
    "L074",
    "L077",
    "L078",
    "L079",
    "M010",
    "R104",
    "R111",
    "R113",
    "R114",
}

EXPECTED_OPA_RULES = {
    "L003",
    "L006",
    "L007",
    "L008",
    "L009",
    "L010",
    "L011",
    "L012",
    "L013",
    "L014",
    "L015",
    "L016",
    "L020",
    "L021",
    "L022",
    "L025",
    "M006",
    "M009",
}


def _run_graph_rules(graph: ContentGraph) -> list[ViolationDict]:
    """Run all GraphRules against a ContentGraph.

    Args:
        graph: ContentGraph to scan.

    Returns:
        List of violation dicts.
    """
    rules = load_graph_rules(rules_dir=str(_native_rules_dir()))
    report = graph_scan(graph, rules)
    return graph_report_to_violations(report)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_results() -> dict[str, list[dict[str, object]]]:
    """Scan the terrible-playbook and collect violations from graph rules + OPA.

    Returns:
        Dict with 'native' and 'opa' keys, each a list of violation dicts.
    """
    fixture = _fixture_path()
    if not fixture.is_dir():
        pytest.skip("terrible-playbook fixture not found")

    context = run_scan(str(fixture / "site.yml"), str(fixture), include_scandata=True)
    if not context.hierarchy_payload:
        pytest.fail("Engine produced no hierarchy payload for terrible-playbook")

    native_violations: list[dict[str, object]] = []
    if context.scandata and hasattr(context.scandata, "content_graph"):
        graph: ContentGraph | None = context.scandata.content_graph
        if graph is not None:
            native_violations = cast(list[dict[str, object]], _run_graph_rules(graph))

    opa = OpaValidator(str(_opa_bundle_dir()))
    opa_violations = cast(list[dict[str, object]], opa.run(context))

    return {
        "native": native_violations,
        "opa": opa_violations,
    }


def _rule_ids(violations: list[dict[str, object]], prefix: str = "") -> set[str]:
    ids = set()
    for v in violations:
        rid = str(v.get("rule_id", ""))
        if prefix and rid.startswith(prefix):
            rid = rid[len(prefix) :]
        ids.add(rid)
    return ids


def test_terrible_playbook_native_rules(scan_results: dict[str, list[dict[str, object]]]) -> None:
    """Verify expected native graph rules fire on the terrible playbook.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    found = _rule_ids(scan_results["native"])
    missing = EXPECTED_NATIVE_RULES - found
    assert not missing, f"Expected native rules did not fire: {sorted(missing)}. Found: {sorted(found)}"


def test_terrible_playbook_opa_rules(scan_results: dict[str, list[dict[str, object]]]) -> None:
    """Verify expected OPA rules fire on the terrible playbook.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    found = _rule_ids(scan_results["opa"])
    missing = EXPECTED_OPA_RULES - found
    assert not missing, f"Expected OPA rules did not fire: {sorted(missing)}. Found: {sorted(found)}"


def test_terrible_playbook_has_violations(scan_results: dict[str, list[dict[str, object]]]) -> None:
    """Verify the scan produces a meaningful number of violations.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    total = len(scan_results["native"]) + len(scan_results["opa"])
    assert total >= 50, f"Expected at least 50 violations, got {total}"
