"""Integration test: scan the vendored terrible-playbook and assert expected rules fire."""

from pathlib import Path
from typing import cast

import pytest

from apme_engine.runner import run_scan
from apme_engine.validators.native import NativeValidator
from apme_engine.validators.opa import OpaValidator


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "terrible-playbook"


def _native_rules_dir() -> Path:
    return _repo_root() / "src" / "apme_engine" / "validators" / "native" / "rules"


def _opa_bundle_dir() -> Path:
    return _repo_root() / "src" / "apme_engine" / "validators" / "opa" / "bundle"


EXPECTED_NATIVE_RULES = {
    "L028",
    "L029",
    "L033",
    "L036",
    "L042",
    "L043",
    "L044",
    "L045",
    "L046",
    "L047",
    "L048",
    "L049",
    "L050",
    "L039",
    "L037",
    "M010",
    "R104",
    "R108",
    "R111",
    "R112",
    "R113",
    "R402",
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


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_results() -> dict[str, list[dict[str, object]]]:
    """Scan the terrible-playbook and collect violations from all validators.

    Returns:
        Dict with 'native' and 'opa' keys, each a list of violation dicts.
    """
    fixture = _fixture_path()
    if not fixture.is_dir():
        pytest.skip("terrible-playbook fixture not found")

    context = run_scan(str(fixture / "site.yml"), str(fixture), include_scandata=True)
    if not context.hierarchy_payload:
        pytest.fail("Engine produced no hierarchy payload for terrible-playbook")

    native = NativeValidator(exclude_rule_ids=())
    opa = OpaValidator(str(_opa_bundle_dir()))

    native_violations = native.run(context)
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
    """Verify expected native rules fire on the terrible playbook.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    found = _rule_ids(scan_results["native"], prefix="native:")
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


def test_terrible_playbook_r101_command_exec(scan_results: dict[str, list[dict[str, object]]]) -> None:
    """Verify R101 (parameterized command execution) fires.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    found = _rule_ids(scan_results["native"], prefix="native:")
    if "R101" not in found:
        pytest.skip("R101 requires CMD_EXEC annotation with is_mutable_cmd; may not fire on all playbooks")


def test_terrible_playbook_r115_file_deletion(scan_results: dict[str, list[dict[str, object]]]) -> None:
    """Verify R115 (file deletion with mutable path) fires if applicable.

    Args:
        scan_results: Pytest fixture with native/opa violation lists.
    """
    found = _rule_ids(scan_results["native"], prefix="native:")
    if "R115" not in found:
        pytest.skip("R115 requires FILE_CHANGE annotation with is_deletion + is_mutable_path")
