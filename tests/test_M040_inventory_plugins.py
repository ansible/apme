"""Tests for the native Python inventory rule M040."""

import json
from unittest.mock import MagicMock

import pytest

from apme.v1 import common_pb2, validate_pb2
from apme_engine.daemon.engine_server import _deduplicate_violations
from apme_engine.daemon.native_validator_server import NativeValidatorServicer
from apme_engine.rule_catalog import _collect_native_rules
from apme_engine.validators.native.rules import M040 as m040_module
from apme_engine.validators.native.rules.M040 import validate_inventory_plugins


async def test_m040_reports_keyword_position_without_a_graph() -> None:
    """Both plugin layouts produce a source-position finding through Native."""
    call = b"self._compose(template, variables,\n    disable_lookups=False,\n)\n"
    response = await NativeValidatorServicer().Validate(
        validate_pb2.ValidateRequest(
            files=[
                common_pb2.File(path="inventory_plugins/custom.py", content=call),
                common_pb2.File(path="collection/plugins/inventory/custom.py", content=call),
            ],
        ),
        MagicMock(),
    )
    findings = response.violations  # type: ignore[attr-defined]
    assert [finding.rule_id for finding in findings] == ["M040", "M040"]
    assert all(finding.path == "$[2][4]" for finding in findings)
    assert all(finding.original_yaml.strip() == "disable_lookups=False," for finding in findings)


def test_m040_ignores_other_calls_and_paths() -> None:
    """Only explicit _compose keywords in valid inventory plugin source match."""
    assert not validate_inventory_plugins(
        {
            "plugins/inventory/clean.py": b"self._compose(template, variables)\n",
            "plugins/inventory/templar.py": b"templar.template(disable_lookups=True)\n",
            "plugins/inventory/broken.py": b"self._compose(disable_lookups=True",
            "plugins/action/custom.py": b"self._compose(disable_lookups=True)\n",
        }
    )


def test_m040_is_registered() -> None:
    """Native exposes M040 to project rule configuration and reporting."""
    rule = next(rule for rule in _collect_native_rules() if rule.rule_id == "M040")
    assert rule.source == "native"
    assert rule.enabled
    assert rule.ansible_core_version == ">=2.23"


def test_m040_keeps_separate_calls_on_one_line() -> None:
    """The engine keeps both source positions when a line has two calls."""
    findings = validate_inventory_plugins(
        {"inventory_plugins/custom.py": b"self._compose(disable_lookups=True); self._compose(disable_lookups=False)"}
    )
    assert len(_deduplicate_violations(findings)) == 2


def test_m040_bounds_total_diagnostic_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Total serialized findings stay within the diagnostic byte budget.

    Once the per-upload budget is exhausted, further positions are counted
    and summarized by one bounded incomplete-assessment notice instead of
    unbounded per-call findings; ordinary findings keep complete excerpts.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 5_000)
    line = b"self._compose(disable_lookups=True); " * 20
    findings = validate_inventory_plugins({"inventory_plugins/custom.py": line})
    # The 1,024-byte notice reserve leaves 3,976 bytes; each finding is
    # charged its 740-byte excerpt plus serialization overhead, so three
    # findings fit before the remaining 17 are suppressed.
    assert len(findings) == 4
    full, notice = findings[:3], findings[3]
    assert all(finding["message"] == m040_module._MESSAGE for finding in full)
    assert all(finding["original_yaml"] == line.decode() for finding in full)
    assert full[0]["original_yaml"] is full[1]["original_yaml"] is full[2]["original_yaml"]
    assert len({finding["path"] for finding in full}) == 3
    unit = "self._compose(disable_lookups=True); "
    assert notice["path"] == f"$[1][{3 * len(unit) + len('self._compose(')}]"
    assert notice["message"] == (
        "M040 diagnostic output exceeded its byte budget for this upload;"
        " the assessment is incomplete with 17 further findings suppressed."
    )
    assert notice["original_yaml"] == m040_module._EXCERPT_OMITTED
    assert len(json.dumps(findings).encode("utf-8")) <= 5_000
