"""Bound the complete graph and hierarchy before JSON transport."""

import datetime
import json
from pathlib import Path

import pytest

from apme_engine.daemon.engine_server import GraphTransportError, _bounded_transport_json


def test_transport_preserves_shared_literals_and_supported_values() -> None:
    """Accepted data keeps the same JSON values as the existing encoder."""
    shared = {"value": [1, None, "é"]}
    value = {
        "first": shared,
        "second": shared,
        "date": datetime.date(2026, 1, 1),
        "path": Path("a/b"),
        "binary": b"Hello\x00",
    }
    assert json.loads(_bounded_transport_json(value)) == json.loads(json.dumps(value, default=str))


def test_transport_rejects_exponentially_expanded_aliases() -> None:
    """Shared containers must be charged once per serialized occurrence."""
    value: object = "leaf"
    for _ in range(24):
        value = [value, value]
    with pytest.raises(GraphTransportError, match="incomplete"):
        _bounded_transport_json({"module_defaults": value})


def test_transport_rejects_expanded_string_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A low node count cannot bypass the byte budget through repeated strings.

    Args:
        monkeypatch: Reduces the transport limit for a compact regression input.
    """
    monkeypatch.setattr("apme_engine.daemon.engine_server._TRANSPORT_JSON_MAX_BYTES", 1024)
    with pytest.raises(GraphTransportError, match="incomplete"):
        _bounded_transport_json({"module_defaults": ["é" * 300] * 2})


def test_transport_rejects_cycles() -> None:
    """Cyclic YAML aliases fail explicitly rather than creating partial output."""
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(GraphTransportError, match="cyclic"):
        _bounded_transport_json({"module_defaults": cycle})


def test_transport_accepts_large_ordinary_graphs() -> None:
    """Graph transport budgets cover normal graphs with more than 20,000 nodes."""
    nodes = [{"id": f"node-{index}", "data": {"module": "file"}} for index in range(20_001)]
    assert len(json.loads(_bounded_transport_json({"nodes": nodes}))["nodes"]) == len(nodes)
