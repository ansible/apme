"""Tests for ADR-042 plugin discovery, prefix enforcement, and clients."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import grpc
import pytest

from apme.v1.common_pb2 import File
from apme.v1.plugin_pb2 import DescribeResponse
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.engine_server import (
    EngineServicer,
    _bind_ext_findings_to_graph,
    _plugin_validate_request,
)
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
    probe_plugin_health,
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


def test_plugin_validate_request_public_fields_only() -> None:
    """Plugin ValidateRequest carries request_id, files, and hierarchy only."""
    req = ValidateRequest(
        request_id="r1",
        scandata=b"pickle",
        hierarchy_payload=b"{}",
        venv_path="/sessions/x",
        content_graph_data=b"graph",
        files=[File(path="a.yml", content=b"- hosts: all\n")],
    )
    clone = _plugin_validate_request(req)
    assert clone.request_id == "r1"
    assert clone.scandata == b""
    assert clone.venv_path == ""
    assert clone.content_graph_data == b""
    assert clone.hierarchy_payload == b"{}"
    assert len(clone.files) == 1
    assert req.scandata == b"pickle"
    assert req.venv_path == "/sessions/x"


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
    """Successful Describe keeps the env token and filters Transform IDs."""

    class _Stub:
        async def Describe(self, _req: object, timeout: float = 5) -> DescribeResponse:
            return DescribeResponse(
                name="opa",
                version="1.0.0",
                rule_id_prefix="orgpolicy",
                transform_rule_ids=["EXT-orgpolicy-002", "L001"],
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


async def test_call_plugin_transform_non_rpc_error() -> None:
    """Non-RpcError from Transform must not raise NameError."""

    class _Stub:
        async def Transform(self, _req: object, timeout: float = 60) -> object:
            raise RuntimeError("boom")

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
    assert applied is False
    assert yaml_text is None
    assert error == "transform error"


async def test_probe_plugin_health_requires_exact_ok() -> None:
    """Substring ``ok`` in a status string is not healthy."""

    class _Resp:
        status = "not ok"

    class _Stub:
        async def Health(self, _req: object, timeout: float = 5) -> _Resp:
            return _Resp()

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.plugins._channel", return_value=_Channel()),
        patch("apme_engine.daemon.plugins.plugin_pb2_grpc.PluginStub", return_value=_Stub()),
    ):
        status = await probe_plugin_health("127.0.0.1:50100")
    assert status == "not ok"


def test_bind_ext_findings_from_file_and_line() -> None:
    """File-oriented EXT- findings bind to the covering ContentGraph node."""
    from types import SimpleNamespace

    node = SimpleNamespace(
        file_path="playbooks/site.yml",
        line_start=4,
        line_end=12,
        node_id="playbooks/site.yml/plays[0]/tasks[0]",
    )
    graph = SimpleNamespace(
        get_node=lambda nid: node if nid == node.node_id else None,
        nodes=lambda: iter([node]),
    )
    findings: list[ViolationDict] = [
        {
            "rule_id": "EXT-secscan-hardcoded-secret",
            "file": "playbooks/site.yml",
            "line": 6,
            "path": "",
            "message": "secret",
        }
    ]
    bound = _bind_ext_findings_to_graph(findings, graph)  # type: ignore[arg-type]
    assert bound[0]["path"] == node.node_id


async def test_ensure_plugins_retries_empty_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty first Describe miss must not pin an empty cache forever.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_PLUGIN_ORGPOLICY_ADDRESS", "127.0.0.1:50100")
    plugin = DiscoveredPlugin(
        name="orgpolicy",
        address="127.0.0.1:50100",
        rule_id_prefix="EXT-orgpolicy-",
    )
    servicer = EngineServicer()
    servicer._plugin_cache = []
    with patch(
        "apme_engine.daemon.engine_server.describe_plugin",
        AsyncMock(return_value=plugin),
    ):
        loaded = await servicer._ensure_plugins()
    assert loaded == [plugin]
