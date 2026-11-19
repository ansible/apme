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
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 6_000)
    line = b"self._compose(disable_lookups=True); " * 20
    findings = validate_inventory_plugins({"inventory_plugins/custom.py": line})
    # The 2,048-byte notice reserve leaves 3,952 bytes; each finding is
    # charged its 742-byte JSON-escaped excerpt, the 28-byte escaped
    # upload path, and serialization overhead, so three findings fit
    # before the remaining 17 are suppressed.
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
    assert len(json.dumps(findings).encode("utf-8")) <= 6_000


def test_m040_charges_upload_path_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long upload paths consume the budget and the notice stays bounded.

    Each finding is charged its upload path bytes, so a long path admits
    fewer findings, and the suppression notice echoes at most a bounded
    head slice of the path with an explicit truncation marker.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 6_000)
    long_path = "inventory_plugins/" + "p" * 2_000 + ".py"
    content = b"self._compose(disable_lookups=True)\n" * 6
    findings = validate_inventory_plugins({long_path: content})
    # Each finding costs its 38-byte JSON-escaped line, the 2,023-byte
    # escaped upload path, and serialization overhead, so only one of the
    # six calls fits the 3,952 bytes left after the notice reserve.
    assert len(findings) == 2
    admitted, notice = findings
    assert admitted["file"] == long_path
    assert admitted["message"] == m040_module._MESSAGE
    assert str(notice["message"]).endswith("the assessment is incomplete with 5 further findings suppressed.")
    notice_file = str(notice["file"])
    assert m040_module._json_size(notice_file) <= m040_module._MAX_NOTICE_PATH_BYTES
    assert notice_file.endswith(m040_module._PATH_TRUNCATION_MARKER)
    assert notice_file.startswith("inventory_plugins/p")
    assert len(json.dumps(findings).encode("utf-8")) <= 6_000


def test_m040_charges_decoded_excerpt_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bytes gained by decoding with replacement consume the budget.

    A Latin-1 source line parses under its coding cookie, but each invalid
    UTF-8 byte in the excerpt decodes to a three-byte replacement character
    and escapes to six JSON bytes, so the budget charges the decoded,
    escaped size rather than the raw byte length.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    prefix = b"self._compose(disable_lookups=True)  # "
    content = b"# -*- coding: latin-1 -*-\n" + prefix + b"\xe9" * 300
    excerpt = prefix.decode("ascii") + "\ufffd" * 300
    charge = (
        len(json.dumps(excerpt)) + len(json.dumps("inventory_plugins/custom.py")) + m040_module._FINDING_OVERHEAD_BYTES
    )
    # The budget fits the finding under raw-byte accounting but not once
    # decoding expansion is charged, so the call is suppressed.
    budget = m040_module._NOTICE_RESERVE_BYTES + charge - 1
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", budget)
    findings = validate_inventory_plugins({"inventory_plugins/custom.py": content})
    assert len(findings) == 1
    notice = findings[0]
    assert str(notice["message"]).endswith("the assessment is incomplete with 1 further findings suppressed.")
    assert len(json.dumps(findings).encode("utf-8")) <= budget


def test_m040_charges_escaped_path_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON escaping of upload path characters consumes the budget.

    Quotes in an upload path each escape to two JSON bytes, so a path
    whose escaped form exceeds the budget is suppressed even though its
    raw byte length would fit.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    quoted_path = "inventory_plugins/" + '"' * 100 + ".py"
    line = b"self._compose(disable_lookups=True)"
    content = line + b"\n"
    charge = len(json.dumps(line.decode("ascii"))) + len(json.dumps(quoted_path)) + m040_module._FINDING_OVERHEAD_BYTES
    # The budget fits the finding when the path is charged unescaped but
    # not once JSON escaping is charged, so the call is suppressed.
    budget = m040_module._NOTICE_RESERVE_BYTES + charge - 1
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", budget)
    findings = validate_inventory_plugins({quoted_path: content})
    assert len(findings) == 1
    notice = findings[0]
    assert str(notice["message"]).endswith("the assessment is incomplete with 1 further findings suppressed.")
    assert str(notice["file"]) == quoted_path
    assert len(json.dumps(findings).encode("utf-8")) <= budget


def test_m040_bounds_notice_path_by_escaped_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control characters in the notice path are bounded by escaped size.

    Control characters cost one UTF-8 byte but six JSON bytes, so the
    notice path is truncated by its serialized size and the whole notice
    fits its reserve.

    Args:
        monkeypatch: Temporary diagnostic byte budget override.
    """
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 3_000)
    control_path = "inventory_plugins/" + "\x01" * 600 + ".py"
    content = b"self._compose(disable_lookups=True)\n"
    findings = validate_inventory_plugins({control_path: content})
    # The escaped path (3,623 bytes) cannot fit the 952 bytes left after
    # the notice reserve, so the call is suppressed and the notice echoes
    # a bounded escaped head slice of the path.
    assert len(findings) == 1
    notice = findings[0]
    assert str(notice["message"]).endswith("the assessment is incomplete with 1 further findings suppressed.")
    notice_file = str(notice["file"])
    assert m040_module._json_size(notice_file) <= m040_module._MAX_NOTICE_PATH_BYTES
    assert notice_file.endswith(m040_module._PATH_TRUNCATION_MARKER)
    assert notice_file.startswith("inventory_plugins/")
    assert len(json.dumps(findings).encode("utf-8")) <= 3_000


def test_m040_caches_suppressed_line_charges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls on an over-budget line decode it once.

    A line whose charge exceeds the budget is measured once and the
    charge is cached for its remaining calls, so many calls on one large
    line stay linear in the line's size instead of re-decoding per call.

    Args:
        monkeypatch: Counting _json_size wrapper and budget override.
    """
    original_size = m040_module._json_size
    measurements: list[str] = []

    def counting_size(value: str) -> int:
        """Record the measured value and defer to the original helper.

        Args:
            value: Excerpt or path text being measured.

        Returns:
            The original helper's JSON-escaped length.
        """
        measurements.append(value)
        return original_size(value)

    monkeypatch.setattr(m040_module, "_json_size", counting_size)
    monkeypatch.setattr(m040_module, "_DIAGNOSTIC_BUDGET_BYTES", 4_000)
    line = b"self._compose(disable_lookups=True); " * 40 + b"# " + b"x" * 4_000
    findings = validate_inventory_plugins({"inventory_plugins/custom.py": line})
    assert len(findings) == 1
    assert str(findings[0]["message"]).endswith("the assessment is incomplete with 40 further findings suppressed.")
    # One path measurement, one excerpt measurement for the shared line,
    # and one notice-path measurement — not one per suppressed call.
    assert len(measurements) == 3
