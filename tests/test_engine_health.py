"""Tests for EngineServicer.Health required-validator gating."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme.v1 import common_pb2
from apme_engine.daemon.engine_server import EngineServicer


async def test_health_unhealthy_when_required_validator_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing NATIVE/OPA/ANSIBLE addresses make Health return unhealthy.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for env in (
        "NATIVE_GRPC_ADDRESS",
        "OPA_GRPC_ADDRESS",
        "ANSIBLE_GRPC_ADDRESS",
        "GITLEAKS_GRPC_ADDRESS",
        "COLLECTION_HEALTH_GRPC_ADDRESS",
        "DEP_AUDIT_GRPC_ADDRESS",
        "APME_GALAXY_PROXY_URL",
    ):
        monkeypatch.delenv(env, raising=False)

    servicer = EngineServicer()
    resp = await servicer.Health(common_pb2.HealthRequest(), MagicMock())
    assert resp.status == "unhealthy"
    names = {d.name for d in resp.downstream}
    assert {"native", "opa", "ansible", "galaxy_proxy"} <= names


async def test_health_ok_when_required_validators_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured required validators that report ok yield aggregate ok.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "127.0.0.1:50055")
    monkeypatch.setenv("OPA_GRPC_ADDRESS", "127.0.0.1:50054")
    monkeypatch.setenv("ANSIBLE_GRPC_ADDRESS", "127.0.0.1:50053")
    monkeypatch.setenv("APME_GALAXY_PROXY_URL", "http://127.0.0.1:8765")
    for env in (
        "GITLEAKS_GRPC_ADDRESS",
        "COLLECTION_HEALTH_GRPC_ADDRESS",
        "DEP_AUDIT_GRPC_ADDRESS",
    ):
        monkeypatch.delenv(env, raising=False)

    class _Resp:
        status = "ok"

    class _Stub:
        async def Health(self, _req: object, timeout: float = 5) -> _Resp:
            return _Resp()

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    class _HttpResp:
        status_code = 200
        text = '{"status":"ok"}'

        def json(self) -> dict[str, str]:
            return {"status": "ok"}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=_HttpResp())

    with (
        patch("apme_engine.daemon.engine_server.grpc.aio.insecure_channel", return_value=_Channel()),
        patch(
            "apme_engine.daemon.engine_server.validate_pb2_grpc.ValidatorStub",
            return_value=_Stub(),
        ),
        patch("apme_engine.daemon.engine_server.httpx.AsyncClient", return_value=mock_client),
    ):
        servicer = EngineServicer()
        resp = await servicer.Health(common_pb2.HealthRequest(), MagicMock())
    assert resp.status == "ok"
    assert len(resp.downstream) == 4
    assert {d.name for d in resp.downstream} == {"native", "opa", "ansible", "galaxy_proxy"}


async def test_health_unhealthy_when_galaxy_proxy_status_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Galaxy Proxy body ``{"status":"not ok"}`` must not count as healthy.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "127.0.0.1:50055")
    monkeypatch.setenv("OPA_GRPC_ADDRESS", "127.0.0.1:50054")
    monkeypatch.setenv("ANSIBLE_GRPC_ADDRESS", "127.0.0.1:50053")
    monkeypatch.setenv("APME_GALAXY_PROXY_URL", "http://127.0.0.1:8765")
    for env in (
        "GITLEAKS_GRPC_ADDRESS",
        "COLLECTION_HEALTH_GRPC_ADDRESS",
        "DEP_AUDIT_GRPC_ADDRESS",
    ):
        monkeypatch.delenv(env, raising=False)

    class _Resp:
        status = "ok"

    class _Stub:
        async def Health(self, _req: object, timeout: float = 5) -> _Resp:
            return _Resp()

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    class _HttpResp:
        status_code = 200
        text = '{"status":"not ok"}'

        def json(self) -> dict[str, str]:
            return {"status": "not ok"}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=_HttpResp())

    with (
        patch("apme_engine.daemon.engine_server.grpc.aio.insecure_channel", return_value=_Channel()),
        patch(
            "apme_engine.daemon.engine_server.validate_pb2_grpc.ValidatorStub",
            return_value=_Stub(),
        ),
        patch("apme_engine.daemon.engine_server.httpx.AsyncClient", return_value=mock_client),
    ):
        servicer = EngineServicer()
        resp = await servicer.Health(common_pb2.HealthRequest(), MagicMock())
    assert resp.status == "unhealthy"
    galaxy = next(d for d in resp.downstream if d.name == "galaxy_proxy")
    assert galaxy.status == "error: status='not ok'"


async def test_health_unhealthy_when_galaxy_proxy_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing APME_GALAXY_PROXY_URL makes Health return unhealthy.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "127.0.0.1:50055")
    monkeypatch.setenv("OPA_GRPC_ADDRESS", "127.0.0.1:50054")
    monkeypatch.setenv("ANSIBLE_GRPC_ADDRESS", "127.0.0.1:50053")
    monkeypatch.delenv("APME_GALAXY_PROXY_URL", raising=False)

    class _Resp:
        status = "ok"

    class _Stub:
        async def Health(self, _req: object, timeout: float = 5) -> _Resp:
            return _Resp()

    class _Channel:
        async def close(self, grace: object = None) -> None:
            return None

    with (
        patch("apme_engine.daemon.engine_server.grpc.aio.insecure_channel", return_value=_Channel()),
        patch(
            "apme_engine.daemon.engine_server.validate_pb2_grpc.ValidatorStub",
            return_value=_Stub(),
        ),
    ):
        servicer = EngineServicer()
        resp = await servicer.Health(common_pb2.HealthRequest(), MagicMock())
    assert resp.status == "unhealthy"
    assert any(d.name == "galaxy_proxy" for d in resp.downstream)
