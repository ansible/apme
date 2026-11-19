"""Tests for the native Python inventory rule M040."""

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


def test_m040_bounds_repeated_excerpt_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Findings past the diagnostic byte budget omit excerpts and report it.

    Repeated findings on one long line must not retain or serialize the full
    source line once the per-upload byte budget is exhausted; positions stay
    complete and affected findings state the incomplete assessment.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 4_000)
    line = b"self._compose(disable_lookups=True); " * 20
    findings = validate_inventory_plugins({"inventory_plugins/custom.py": line})
    assert len(findings) == 20
    assert len({finding["path"] for finding in findings}) == 20
    full = [finding for finding in findings if finding["message"] == m040_module._MESSAGE]
    omitted = [finding for finding in findings if finding["message"] != m040_module._MESSAGE]
    # Each in-budget finding is charged its full excerpt plus serialization
    # overhead, so only the first three findings of this 740-byte line fit.
    assert len(full) == 3
    assert all(finding["original_yaml"] == line.decode() for finding in full)
    assert full[0]["original_yaml"] is full[1]["original_yaml"] is full[2]["original_yaml"]
    assert len(omitted) == 17
    assert all(finding["original_yaml"] == m040_module._EXCERPT_OMITTED for finding in omitted)
    assert all(finding["message"] == m040_module._MESSAGE + m040_module._INCOMPLETE_SUFFIX for finding in omitted)
