"""Tests for the native Python inventory rule M040."""

from unittest.mock import MagicMock

from apme.v1 import common_pb2, validate_pb2
from apme_engine.daemon.engine_server import _deduplicate_violations
from apme_engine.daemon.native_validator_server import NativeValidatorServicer
from apme_engine.rule_catalog import _collect_native_rules
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
