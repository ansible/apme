"""Unit tests for ADR-041 rule catalog, Primary rule config helpers, and Gateway registration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from apme.v1 import common_pb2, primary_pb2, reporting_pb2
from apme_engine.daemon.primary_server import (
    _apply_rule_configs,
    _validate_rule_configs,
)
from apme_engine.engine.models import ViolationDict
from apme_engine.rule_catalog import (
    _category_from_rule_id,
    _collect_gitleaks_rules,
    collect_all_rules,
)
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db import queries as q
from apme_gateway.grpc_reporting.servicer import ReportingServicer


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("rule_id", "expected_category"),
    [
        ("L001", "lint"),
        ("M002", "modernize"),
        ("R101", "risk"),
        ("P001", "policy"),
        ("SEC:generic-api-key", "secrets"),
        ("INFRA-001", "infrastructure"),
        ("X999-unknown", "unknown"),
    ],
)
def test_category_from_rule_id(rule_id: str, expected_category: str) -> None:
    """``_category_from_rule_id`` maps ADR-008 prefixes to catalog categories.

    Args:
        rule_id: Sample rule identifier under test.
        expected_category: Expected category label from the catalog.

    Returns:
        None: Assert-only test.
    """
    got = _category_from_rule_id(rule_id)
    assert got == expected_category, f"expected category {expected_category!r} for {rule_id!r}, got {got!r}"


def test_collect_all_rules_non_empty_and_fields() -> None:
    """``collect_all_rules`` returns a sorted list with required metadata per entry.

    Returns:
        None: Assert-only test.
    """
    rules = collect_all_rules()
    assert len(rules) > 0, "expected built-in catalog to contain at least one rule"
    for d in rules:
        assert d.rule_id, "each RuleDefinition must have a non-empty rule_id"
        assert d.category, "each RuleDefinition must have a non-empty category"
        assert d.source, "each RuleDefinition must have a non-empty source"
        assert d.default_severity != common_pb2.SEVERITY_UNSPECIFIED, (
            f"rule {d.rule_id!r} must have a resolved default_severity"
        )
    ids = [d.rule_id for d in rules]
    assert ids == sorted(ids), "collect_all_rules must return rules sorted by rule_id"


def test_collect_gitleaks_rules_sec_placeholder_critical() -> None:
    """Gitleaks contributes a single ``SEC:*`` placeholder with critical severity.

    Returns:
        None: Assert-only test.
    """
    defs = _collect_gitleaks_rules()
    assert len(defs) == 1, "gitleaks catalog must expose exactly one placeholder rule"
    r = defs[0]
    assert r.rule_id == "SEC:*", "gitleaks placeholder rule_id must be SEC:*"
    assert r.default_severity == common_pb2.SEVERITY_CRITICAL, "gitleaks placeholder must use CRITICAL default severity"
    assert r.category == "secrets", "gitleaks rules must be categorized as secrets"
    assert r.source == "gitleaks", "gitleaks placeholder source must be gitleaks"


def test_apply_rule_configs_filters_disabled_rule() -> None:
    """Violations for disabled ``RuleConfig`` entries are dropped.

    Returns:
        None: Assert-only test.
    """
    violations: list[ViolationDict] = [
        {"rule_id": "L001", "severity": "error"},
        {"rule_id": "L002", "severity": "medium"},
    ]
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=False, severity=common_pb2.SEVERITY_ERROR),
        primary_pb2.RuleConfig(rule_id="L002", enabled=True),
    ]
    out = _apply_rule_configs(violations, configs)
    assert len(out) == 1, "disabled rule violation should be removed"
    assert out[0]["rule_id"] == "L002", "only the enabled rule's violation should remain"


def test_apply_rule_configs_severity_override() -> None:
    """When ``RuleConfig.severity`` is set, the violation severity label is replaced.

    Returns:
        None: Assert-only test.
    """
    violations: list[ViolationDict] = [{"rule_id": "L001", "severity": "medium"}]
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True, severity=common_pb2.SEVERITY_HIGH),
    ]
    out = _apply_rule_configs(violations, configs)
    assert len(out) == 1, "single violation should remain"
    assert out[0]["severity"] == "high", "severity should be overridden to high"


def test_apply_rule_configs_enforced_flag() -> None:
    """``RuleConfig.enforced`` attaches ``_enforced`` on the violation dict.

    Returns:
        None: Assert-only test.
    """
    violations: list[ViolationDict] = [{"rule_id": "L001", "severity": "error"}]
    configs: list[object] = [primary_pb2.RuleConfig(rule_id="L001", enabled=True, enforced=True)]
    out = _apply_rule_configs(violations, configs)
    assert len(out) == 1, "violation should pass through when enabled"
    assert out[0].get("_enforced") is True, "enforced=True must set _enforced metadata"


def test_apply_rule_configs_empty_configs_returns_same_list() -> None:
    """An empty ``rule_configs`` list must leave violations untouched.

    Returns:
        None: Assert-only test.
    """
    violations: list[ViolationDict] = [{"rule_id": "L001", "severity": "low"}]
    out = _apply_rule_configs(violations, [])
    assert out is violations, "empty rule_configs should return the original list instance"


def test_validate_rule_configs_unknown_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown rule IDs in configs are collected and returned.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr(
        "apme_engine.daemon.primary_server._known_rule_ids",
        {"L001", "M001"},
    )
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True),
        primary_pb2.RuleConfig(rule_id="NOT_A_RULE", enabled=True),
    ]
    unknown, missing = _validate_rule_configs(configs)
    assert unknown == ["NOT_A_RULE"], "unknown rule_id must be reported"
    assert missing == [], "partial mode should not report missing"


def test_validate_rule_configs_known_ids_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configs referencing only known rule IDs produce an empty error list.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr(
        "apme_engine.daemon.primary_server._known_rule_ids",
        {"L001", "P010"},
    )
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True),
        primary_pb2.RuleConfig(rule_id="P010", enabled=False),
    ]
    unknown, missing = _validate_rule_configs(configs)
    assert unknown == [], "all rule IDs are known; no errors expected"
    assert missing == [], "all known IDs covered; no missing expected"


def test_validate_rule_configs_empty_known_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the known-id set is empty, validation is a no-op (no errors).

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr("apme_engine.daemon.primary_server._known_rule_ids", set())
    configs: list[object] = [primary_pb2.RuleConfig(rule_id="ANYTHING", enabled=True)]
    unknown, missing = _validate_rule_configs(configs)
    assert unknown == [], "empty known set must skip validation"
    assert missing == [], "empty known set must skip validation"


def test_validate_rule_configs_complete_detects_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bidirectional audit reports rules the Primary knows but the config omits.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr(
        "apme_engine.daemon.primary_server._known_rule_ids",
        {"L001", "L002", "M001"},
    )
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True),
    ]
    unknown, missing = _validate_rule_configs(configs, complete=True)
    assert unknown == [], "L001 is known; no unknowns"
    assert missing == ["L002", "M001"], "L002 and M001 are known but absent from config"


def test_validate_rule_configs_complete_all_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bidirectional audit passes when config covers all known rules exactly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr(
        "apme_engine.daemon.primary_server._known_rule_ids",
        {"L001", "M001"},
    )
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True),
        primary_pb2.RuleConfig(rule_id="M001", enabled=True),
    ]
    unknown, missing = _validate_rule_configs(configs, complete=True)
    assert unknown == [], "all config IDs are known"
    assert missing == [], "all known IDs are covered"


def test_validate_rule_configs_complete_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bidirectional audit detects both unknown and missing simultaneously.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assert-only test.
    """
    monkeypatch.setattr(
        "apme_engine.daemon.primary_server._known_rule_ids",
        {"L001", "L002"},
    )
    configs: list[object] = [
        primary_pb2.RuleConfig(rule_id="L001", enabled=True),
        primary_pb2.RuleConfig(rule_id="X999", enabled=True),
    ]
    unknown, missing = _validate_rule_configs(configs, complete=True)
    assert unknown == ["X999"], "X999 is not known to the Primary"
    assert missing == ["L002"], "L002 is known but absent from config"


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh SQLite DB for ReportingServicer tests.

    Args:
        tmp_path: Pytest temporary directory.

    Yields:
        None: Test body runs between init and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


def _grpc_context() -> AsyncMock:
    """Build an async mock gRPC servicer context.

    Returns:
        AsyncMock suitable as ``ServicerContext`` for gateway tests.
    """
    return AsyncMock()


def _sample_rule(rule_id: str) -> reporting_pb2.RuleDefinition:
    """Construct a minimal ``RuleDefinition`` for registration tests.

    Args:
        rule_id: Rule identifier.

    Returns:
        Populated ``RuleDefinition`` proto.
    """
    return reporting_pb2.RuleDefinition(
        rule_id=rule_id,
        default_severity=common_pb2.SEVERITY_MEDIUM,
        category="lint",
        source="native",
        description="test rule",
        scope=common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
        enabled=True,
    )


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_register_rules_rejects_non_authority() -> None:
    """``RegisterRules`` with ``is_authority=False`` is rejected without persistence.

    Returns:
        None: Assert-only test.
    """
    servicer = ReportingServicer()
    req = reporting_pb2.RegisterRulesRequest(
        pod_id="pod-secondary",
        is_authority=False,
        rules=[_sample_rule("L001")],
    )
    resp = await servicer.RegisterRules(req, _grpc_context())
    assert resp.accepted is False, "non-authority registration must not be accepted"
    assert "not the rule authority" in resp.message.lower(), "message should explain rejection"

    async with get_session() as db:
        rows = await q.list_rules(db)
    assert len(rows) == 0, "rejected registration must not write rules"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_register_rules_adds_new_rules() -> None:
    """Authority ``RegisterRules`` inserts new catalog rows.

    Returns:
        None: Assert-only test.
    """
    servicer = ReportingServicer()
    req = reporting_pb2.RegisterRulesRequest(
        pod_id="pod-a",
        is_authority=True,
        rules=[_sample_rule("L010"), _sample_rule("L020")],
    )
    resp = await servicer.RegisterRules(req, _grpc_context())
    assert resp.accepted is True, "authority registration should succeed"
    assert resp.rules_added == 2, "both rules should be new"
    assert resp.rules_removed == 0, "first registration removes nothing"
    assert resp.rules_unchanged == 0, "no pre-existing rows to update"

    async with get_session() as db:
        rows = await q.list_rules(db)
    ids = {r.rule_id for r in rows}
    assert ids == {"L010", "L020"}, "database should contain exactly the registered rule IDs"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_register_rules_reconcile_removes_absent_rules() -> None:
    """A second full registration drops rules missing from the incoming set.

    Returns:
        None: Assert-only test.
    """
    servicer = ReportingServicer()
    ctx = _grpc_context()
    first = reporting_pb2.RegisterRulesRequest(
        pod_id="pod-a",
        is_authority=True,
        rules=[_sample_rule("R-KEEP"), _sample_rule("R-DROP")],
    )
    r1 = await servicer.RegisterRules(first, ctx)
    assert r1.accepted is True, "initial registration should succeed"
    assert r1.rules_added == 2, "two new rules on first sync"

    second = reporting_pb2.RegisterRulesRequest(
        pod_id="pod-a",
        is_authority=True,
        rules=[_sample_rule("R-KEEP")],
    )
    r2 = await servicer.RegisterRules(second, ctx)
    assert r2.accepted is True, "reconciliation should succeed"
    assert r2.rules_removed == 1, "one rule absent from payload should be deleted"
    assert r2.rules_unchanged == 1, "remaining rule should count as unchanged/update"
    assert r2.rules_added == 0, "no new IDs in second payload"

    async with get_session() as db:
        rows = await q.list_rules(db)
    assert len(rows) == 1, "catalog should shrink to the single retained rule"
    assert rows[0].rule_id == "R-KEEP", "the retained rule must match the second payload"
