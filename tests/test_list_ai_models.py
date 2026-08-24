"""Tests for the ListAIModels gRPC RPC on EngineServicer."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from apme.v1.engine_pb2 import ListAIModelsRequest
from apme_engine.daemon.engine_server import EngineServicer


@dataclass
class _FakeModel:
    """Minimal stand-in for abbenay_grpc.Model.

    Attributes:
        id: Model identifier.
        provider: LLM provider name.
        name: Human-readable model name.
        supports_streaming: Whether the model supports streaming.
        supports_tools: Whether the model supports tool calling.
    """

    id: str
    provider: str
    name: str
    supports_streaming: bool = True
    supports_tools: bool = True


class _FakeAbbenayClient:
    """Stand-in for AbbenayClient with a canned model list.

    Args:
        *args: Positional constructor args (must stay empty for unix://).
        **kwargs: Keyword constructor args; ``socket_path`` / ``host`` / ``port``.

    Attributes:
        last_init: Most recent constructor ``args`` / ``kwargs``.
    """

    last_init: dict[str, object] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).last_init = {"args": args, "kwargs": kwargs}
        self._models: list[_FakeModel] = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def list_models(self) -> list[_FakeModel]:
        return self._models


def _make_stub_module(client_cls: type | None = None) -> types.ModuleType:
    """Build a fake ``abbenay_grpc`` module for sys.modules patching.

    Args:
        client_cls: Class to use as AbbenayClient. Defaults to _FakeAbbenayClient.

    Returns:
        A minimal module with an AbbenayClient attribute.
    """
    mod = types.ModuleType("abbenay_grpc")
    mod.AbbenayClient = client_cls or _FakeAbbenayClient  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# Engine.ListAIModels
# ---------------------------------------------------------------------------


class TestEngineListAIModels:
    """Test the ListAIModels gRPC method on EngineServicer."""

    def test_returns_models_from_abbenay(self) -> None:
        """ListAIModels returns models when Abbenay is reachable."""
        models = [
            _FakeModel(id="openai/gpt-4o", provider="openai", name="gpt-4o"),
            _FakeModel(id="anthropic/claude-sonnet-4", provider="anthropic", name="claude-sonnet-4"),
        ]

        class _ClientWithModels(_FakeAbbenayClient):
            def __init__(self, *args: object, **kw: object) -> None:
                super().__init__(*args, **kw)
                self._models = models

        stub_mod = _make_stub_module(_ClientWithModels)

        with (
            patch.dict(os.environ, {"APME_ABBENAY_ADDR": "127.0.0.1:50057"}),
            patch.dict(sys.modules, {"abbenay_grpc": stub_mod}),
        ):
            servicer = EngineServicer()
            ctx = MagicMock()
            resp = asyncio.run(servicer.ListAIModels(ListAIModelsRequest(), ctx))

        assert len(resp.models) == 2
        assert resp.models[0].id == "openai/gpt-4o"
        assert resp.models[1].provider == "anthropic"
        assert _ClientWithModels.last_init["args"] == ()
        assert _ClientWithModels.last_init["kwargs"] == {"host": "127.0.0.1", "port": 50057}

    def test_unix_addr_passes_bare_socket_path(self) -> None:
        """ListAIModels strips unix:// so AbbenayClient does not double-prefix."""
        models = [_FakeModel(id="openai/gpt-4o", provider="openai", name="gpt-4o")]

        class _ClientWithModels(_FakeAbbenayClient):
            def __init__(self, *args: object, **kw: object) -> None:
                super().__init__(*args, **kw)
                self._models = models

        stub_mod = _make_stub_module(_ClientWithModels)
        sock = "/tmp/abbenay-run/abbenay/daemon.sock"

        with (
            patch.dict(os.environ, {"APME_ABBENAY_ADDR": f"unix://{sock}"}),
            patch.dict(sys.modules, {"abbenay_grpc": stub_mod}),
        ):
            servicer = EngineServicer()
            ctx = MagicMock()
            resp = asyncio.run(servicer.ListAIModels(ListAIModelsRequest(), ctx))

        assert len(resp.models) == 1
        assert _ClientWithModels.last_init["args"] == ()
        assert _ClientWithModels.last_init["kwargs"] == {"socket_path": sock}

    def test_returns_empty_when_no_addr(self) -> None:
        """ListAIModels returns empty list when APME_ABBENAY_ADDR is not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APME_ABBENAY_ADDR", None)
            servicer = EngineServicer()
            ctx = MagicMock()
            resp = asyncio.run(servicer.ListAIModels(ListAIModelsRequest(), ctx))

        assert len(resp.models) == 0

    def test_returns_empty_when_abbenay_unreachable(self) -> None:
        """ListAIModels returns empty list when Abbenay connection fails."""

        class _FailingClient(_FakeAbbenayClient):
            async def connect(self) -> None:
                raise ConnectionError("refused")

        stub_mod = _make_stub_module(_FailingClient)

        with (
            patch.dict(os.environ, {"APME_ABBENAY_ADDR": "127.0.0.1:50057"}),
            patch.dict(sys.modules, {"abbenay_grpc": stub_mod}),
        ):
            servicer = EngineServicer()
            ctx = MagicMock()
            resp = asyncio.run(servicer.ListAIModels(ListAIModelsRequest(), ctx))

        assert len(resp.models) == 0

    def test_returns_empty_when_client_not_installed(self) -> None:
        """ListAIModels returns empty list when abbenay_grpc is not installed."""
        with (
            patch.dict(os.environ, {"APME_ABBENAY_ADDR": "127.0.0.1:50057"}),
            patch.dict(sys.modules, {"abbenay_grpc": None}),
        ):
            servicer = EngineServicer()
            ctx = MagicMock()
            resp = asyncio.run(servicer.ListAIModels(ListAIModelsRequest(), ctx))

        assert len(resp.models) == 0
