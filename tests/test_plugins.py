"""Tests for ADR-042 plugin discovery, prefix enforcement, and clients."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import grpc
import pytest

from apme.v1.common_pb2 import File
from apme.v1.plugin_pb2 import DescribeResponse
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.engine_server import _plugin_validate_request
from apme_engine.daemon.plugins import (
    DiscoveredPlugin,
    call_plugin_transform,
    call_plugin_validate,
    describe_plugin,
    discover_plugin_addresses,
    filter_plugin_violations,
    inferred_rule_id_prefix,
    load_plugins,
    normalize_rule_id_prefix,
    plugin_for_rule,
    transform_rule_ids,
    yaml_is_well_formed,
)
from apme_engine.engine.models import ViolationDict


def test_discover_plugin_addresses_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-empty ``APME_PLUGIN_*_ADDRESS`` vars are discovered in name order.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_PLUGIN_ORGPOLICY_ADDRESS", "127.0.0.1:50101")
    monkeypatch.setenv("APME_PLUGIN_SECTEAM_ADDRESS", "127.0.0.1:50100")
    monkeypatch.setenv("APME_PLUGIN_EMPTY_ADDRESS", "  ")
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "127.0.0.1:50055")
    found = discover_plugin_addresses()
    assert found == [
        ("orgpolicy", "127.0.0.1:50101"),
        ("secteam", "127.0.0.1:50100"),
    ]


def test_normalize_rule_id_prefix() -> None:
    """Describe prefixes are coerced to ``EXT-<name>-``."""
    assert inferred_rule_id_prefix("orgpolicy") == "EXT-orgpolicy-"
    assert normalize_rule_id_prefix("orgpolicy", "") == "EXT-orgpolicy-"
    assert normalize_rule_id_prefix("orgpolicy", "EXT-orgpolicy") == "EXT-orgpolicy-"
    assert normalize_rule_id_prefix("orgpolicy", "custom-") == "EXT-custom-"


def test_filter_plugin_violations_drops_wrong_prefix() -> None:
    """Findings that are not EXT- or not this plugin's prefix are dropped."""
    plugin = DiscoveredPlugin(
        name="orgpolicy",
        address="127.0.0.1:50100",
        rule_id_prefix="EXT-orgpolicy-",
    )
    raw: list[ViolationDict] = [
        {"rule_id": "EXT-orgpolicy-001", "message": "ok"},
        {"rule_id": "EXT-secteam-001", "message": "other plugin"},
        {"rule_id": "P001", "message": "built-in"},
    ]
    kept = filter_plugin_violations(plugin, raw)
    assert len(kept) == 1
    assert kept[0]["rule_id"] == "EXT-orgpolicy-001"
    assert kept[0]["source"] == "plugin:orgpolicy"


def test_plugin_for_rule_longest_prefix() -> None:
    """When prefixes nest, the longest match wins."""
    short = DiscoveredPlugin(name="org", address="a", rule_id_prefix="EXT-org-")
    long = DiscoveredPlugin(name="orgpolicy", address="b", rule_id_prefix="EXT-orgpolicy-")
    match = plugin_for_rule([short, long], "EXT-orgpolicy-001")
    assert match is not None
    assert match.name == "orgpolicy"
    assert plugin_for_rule([short], "EXT-other-001") is None


def test_transform_rule_ids_union() -> None:
    """Transform IDs from all plugins are unioned."""
    plugins = [
        DiscoveredPlugin(
            name="a",
            address="a",
            rule_id_prefix="EXT-a-",
            transform_rule_ids=frozenset({"EXT-a-001"}),
        ),
        DiscoveredPlugin(
            name="b",
            address="b",
            rule_id_prefix="EXT-b-",
            transform_rule_ids=frozenset({"EXT-b-002", "EXT-a-001"}),
        ),
    ]
    assert transform_rule_ids(plugins) == frozenset({"EXT-a-001", "EXT-b-002"})


def test_yaml_is_well_formed() -> None:
    """Empty and invalid YAML are rejected."""
    assert yaml_is_well_formed("- name: ok\n") is True
    assert yaml_is_well_formed("   ") is False
    assert yaml_is_well_formed(": not: [yaml") is False


def test_plugin_validate_request_clears_scandata() -> None:
    """Plugin ValidateRequest must not carry native scandata."""
    req = ValidateRequest(scandata=b"pickle", hierarchy_payload=b"{}")
    clone = _plugin_validate_request(req)
    assert clone.scandata == b""
    assert clone.hierarchy_payload == b"{}"
    assert req.scandata == b"pickle"


async def test_describe_unimplemented_uses_inferred_prefix() -> None:
    """UNIMPLEMENTED Describe falls back to ``EXT-<name>-``."""

    class _Unimplemented(grpc.RpcError):
        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.UNIMPLEMENTED

    class _Stub:
        async def Describe(self, _req: object, timeout: float = 5) -> DescribeResponse:
            raise _Unimplemented()

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.plugins._channel", return_value=_Channel()),
        patch("apme_engine.daemon.plugins.plugin_pb2_grpc.PluginStub", return_value=_Stub()),
    ):
        plugin = await describe_plugin("orgpolicy", "127.0.0.1:50100")
    assert plugin.rule_id_prefix == "EXT-orgpolicy-"
    assert plugin.transform_rule_ids == frozenset()


async def test_describe_normalizes_reported_prefix() -> None:
    """Successful Describe uses the plugin-reported name and prefix."""

    class _Stub:
        async def Describe(self, _req: object, timeout: float = 5) -> DescribeResponse:
            return DescribeResponse(
                name="OrgPolicy",
                version="1.0.0",
                rule_id_prefix="orgpolicy",
                transform_rule_ids=["EXT-orgpolicy-002"],
            )

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.plugins._channel", return_value=_Channel()),
        patch("apme_engine.daemon.plugins.plugin_pb2_grpc.PluginStub", return_value=_Stub()),
    ):
        plugin = await describe_plugin("orgpolicy", "127.0.0.1:50100")
    assert plugin.name == "orgpolicy"
    assert plugin.rule_id_prefix == "EXT-orgpolicy-"
    assert plugin.transform_rule_ids == frozenset({"EXT-orgpolicy-002"})
    assert plugin.version == "1.0.0"


async def test_load_plugins_skips_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Describe failures skip that plugin rather than aborting discovery.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_PLUGIN_BAD_ADDRESS", "127.0.0.1:50199")
    with patch(
        "apme_engine.daemon.plugins.describe_plugin",
        AsyncMock(side_effect=RuntimeError("down")),
    ):
        loaded = await load_plugins()
    assert loaded == []


async def test_call_plugin_validate_maps_violations() -> None:
    """Validate RPC results are converted to violation dicts."""
    from apme.v1.common_pb2 import Violation

    class _Stub:
        async def Validate(self, _req: object, timeout: float = 300) -> ValidateResponse:
            return ValidateResponse(
                violations=[Violation(rule_id="EXT-orgpolicy-001", message="banned")],
            )

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.plugins._channel", return_value=_Channel()),
        patch("apme_engine.daemon.plugins.plugin_pb2_grpc.PluginStub", return_value=_Stub()),
    ):
        violations, error = await call_plugin_validate(
            "127.0.0.1:50100",
            ValidateRequest(request_id="r1"),
        )
    assert error is None
    assert len(violations) == 1
    assert violations[0]["rule_id"] == "EXT-orgpolicy-001"


async def test_call_plugin_transform_returns_yaml() -> None:
    """Applied Transform returns decoded node YAML."""
    from apme.v1.plugin_pb2 import TransformResponse

    class _Stub:
        async def Transform(self, _req: object, timeout: float = 60) -> TransformResponse:
            return TransformResponse(
                applied=True,
                file=File(path="site.yml", content=b"- name: fixed\n"),
            )

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.plugins._channel", return_value=_Channel()),
        patch("apme_engine.daemon.plugins.plugin_pb2_grpc.PluginStub", return_value=_Stub()),
    ):
        applied, yaml_text, error = await call_plugin_transform(
            "127.0.0.1:50100",
            request_id="r1",
            file_path="site.yml",
            yaml_content="- name: old\n",
            violation={"rule_id": "EXT-orgpolicy-002", "message": "fix me"},
        )
    assert applied is True
    assert yaml_text == "- name: fixed\n"
    assert error == ""
