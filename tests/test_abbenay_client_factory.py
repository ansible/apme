"""Tests for Abbenay client factory TLS resolution (issue #400)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from apme_engine.remediation.abbenay_client_factory import (
    _discover_default_ca_cert,
    _TlsAbbenayClient,
    build_abbenay_client,
    resolve_abbenay_tls_config,
)


class TestResolveAbbenayTlsConfig:
    """TLS config resolution from environment."""

    def test_unix_addr_never_enables_tls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unix sockets stay plaintext regardless of TLS env vars.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setenv("APME_ABBENAY_TLS", "true")
        monkeypatch.setenv("APME_ABBENAY_CA_CERT", "/etc/ca.crt")
        cfg = resolve_abbenay_tls_config("unix:///run/abbenay.sock")
        assert cfg.enabled is True
        assert cfg.ca_cert == "/etc/ca.crt"

    def test_tcp_plaintext_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TCP without env vars or default CA path stays plaintext.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        with patch("apme_engine.remediation.abbenay_client_factory.os.path.isfile", return_value=False):
            cfg = resolve_abbenay_tls_config("abbenay:50057")
        assert cfg.enabled is False

    def test_auto_tls_when_default_ca_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Podman shared runtime CA enables TLS without explicit env.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            tmp_path: Pytest temporary directory fixture.
        """
        runtime = tmp_path / "runtime"
        ca_path = runtime / "abbenay" / "tls" / "ca.crt"
        ca_path.parent.mkdir(parents=True)
        pem = b"-----BEGIN CERTIFICATE-----\npem\n-----END CERTIFICATE-----\n"
        ca_path.write_bytes(pem)
        os.chmod(ca_path, 0o644)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        cfg = resolve_abbenay_tls_config("127.0.0.1:50057")
        assert cfg.enabled is True
        assert cfg.ca_cert is None
        assert cfg.ca_cert_pem == pem

    def test_ignores_untrusted_default_ca(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """World-writable CA files under /tmp are not auto-trusted.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            tmp_path: Pytest temporary directory fixture.
        """
        runtime = tmp_path / "runtime"
        ca_path = runtime / "abbenay" / "tls" / "ca.crt"
        ca_path.parent.mkdir(parents=True)
        ca_path.write_text("pem", encoding="utf-8")
        os.chmod(ca_path, 0o666)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        cfg = resolve_abbenay_tls_config("127.0.0.1:50057")
        assert cfg.enabled is False

    def test_prefers_xdg_runtime_dir_ca(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """XDG_RUNTIME_DIR CA path is preferred over the legacy /tmp fallback.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            tmp_path: Pytest temporary directory fixture.
        """
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        ca_path = runtime / "abbenay" / "tls" / "ca.crt"
        ca_path.parent.mkdir(parents=True)
        pem = b"pem-bytes"
        ca_path.write_bytes(pem)
        os.chmod(ca_path, 0o644)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        discovered = _discover_default_ca_cert()
        assert discovered == pem


class TestBuildAbbenayClient:
    """Client construction for TCP, Unix, and TLS paths."""

    def test_plaintext_tcp_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TCP without TLS builds a standard AbbenayClient.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        calls: list[dict[str, object]] = []

        class FakeAbbenayClient:
            def __init__(
                self,
                *,
                socket_path: str | None = None,
                host: str | None = None,
                port: int = 50051,
            ) -> None:
                calls.append({"socket_path": socket_path, "host": host, "port": port})

        fake_module = type(sys)("abbenay_grpc")
        fake_module.AbbenayClient = FakeAbbenayClient  # type: ignore[attr-defined]

        with (
            patch.dict(sys.modules, {"abbenay_grpc": fake_module}),
            patch("apme_engine.remediation.abbenay_client_factory._discover_default_ca_cert", return_value=None),
        ):
            client = build_abbenay_client("127.0.0.1:50057")

        assert isinstance(client, FakeAbbenayClient)
        assert calls == [{"socket_path": None, "host": "127.0.0.1", "port": 50057}]

    def test_tls_uses_native_client_when_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Newer abbenay-client wheels receive tls kwargs directly.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setenv("APME_ABBENAY_TLS", "true")
        monkeypatch.setenv("APME_ABBENAY_CA_CERT", "/etc/ca.crt")
        calls: list[dict[str, object]] = []

        class FakeAbbenayClient:
            def __init__(
                self,
                *,
                socket_path: str | None = None,
                host: str | None = None,
                port: int = 50051,
                tls: bool = False,
                ca_cert: str | None = None,
                ssl_target_name: str | None = None,
            ) -> None:
                calls.append(
                    {
                        "socket_path": socket_path,
                        "host": host,
                        "port": port,
                        "tls": tls,
                        "ca_cert": ca_cert,
                        "ssl_target_name": ssl_target_name,
                    }
                )

        fake_module = type(sys)("abbenay_grpc")
        fake_module.AbbenayClient = FakeAbbenayClient  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"abbenay_grpc": fake_module}):
            client = build_abbenay_client("abbenay:50057")

        assert isinstance(client, FakeAbbenayClient)
        assert calls == [
            {
                "socket_path": None,
                "host": "abbenay",
                "port": 50057,
                "tls": True,
                "ca_cert": "/etc/ca.crt",
                "ssl_target_name": "abbenay-grpc",
            }
        ]


class TestTlsAbbenayClientReconnect:
    """TLS shim reconnect must preserve secure_channel configuration."""

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_reconnect_uses_tls_connect(self) -> None:
        """reconnect() must not delegate to AbbenayClient.reconnect (insecure)."""
        fake_module = type(sys)("abbenay_grpc")
        fake_module.AbbenayClient = MagicMock(return_value=MagicMock(_target="127.0.0.1:50057"))  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"abbenay_grpc": fake_module}):
            client = _TlsAbbenayClient(host="127.0.0.1", port=50057, ca_cert="/etc/ca.crt")
            connect_mock = AsyncMock()
            client.connect = connect_mock  # type: ignore[method-assign]
            client._delegate.reconnect = AsyncMock()  # noqa: SLF001

            await client.reconnect()

        connect_mock.assert_awaited_once()
        client._delegate.reconnect.assert_not_awaited()  # noqa: SLF001


class TestTlsAbbenayClientConnect:
    """TLS shim connect error handling."""

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_connect_wraps_missing_ca_file(self) -> None:
        """Unreadable CA paths raise AbbenayConnectionError, not raw OSError."""
        fake_client_module = type(sys)("abbenay_grpc.client")
        fake_client_module.AbbenayError = Exception  # type: ignore[attr-defined]
        fake_client_module.ConnectionError = type("AbbenayConnectionError", (Exception,), {})  # type: ignore[attr-defined]
        fake_client_module.grpc_service = MagicMock()  # type: ignore[attr-defined]
        fake_client_module.proto = MagicMock()  # type: ignore[attr-defined]

        fake_module = type(sys)("abbenay_grpc")
        fake_module.AbbenayClient = MagicMock(return_value=MagicMock(_target="127.0.0.1:50057"))  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"abbenay_grpc": fake_module, "abbenay_grpc.client": fake_client_module}):
            client = _TlsAbbenayClient(host="127.0.0.1", port=50057, ca_cert="/missing/ca.crt")
            with pytest.raises(fake_client_module.ConnectionError, match="Failed to connect to daemon"):
                await client.connect()

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_connect_serializes_concurrent_calls(self) -> None:
        """Concurrent connect/reconnect calls share one channel registration."""
        fake_client_module = type(sys)("abbenay_grpc.client")
        fake_client_module.AbbenayError = Exception  # type: ignore[attr-defined]
        fake_client_module.ConnectionError = type("AbbenayConnectionError", (Exception,), {})  # type: ignore[attr-defined]
        fake_client_module.grpc_service = MagicMock()  # type: ignore[attr-defined]
        fake_client_module.proto = MagicMock()  # type: ignore[attr-defined]
        fake_client_module.proto.RegisterRequest = MagicMock(return_value="register-request")
        fake_client_module.proto.ClientInfo = MagicMock(return_value="client-info")
        fake_client_module.proto.CLIENT_TYPE_PYTHON = 1

        register_calls = 0
        connect_started = asyncio.Event()
        release_connect = asyncio.Event()

        async def slow_register(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal register_calls
            register_calls += 1
            connect_started.set()
            await release_connect.wait()
            response = MagicMock()
            response.client_id = "client-1"
            return response

        fake_stub = MagicMock()
        fake_stub.Register = slow_register

        fake_client_module.grpc_service.AbbenayStub = MagicMock(return_value=fake_stub)

        fake_channel = MagicMock()
        fake_channel.get_state = MagicMock(return_value=grpc.ChannelConnectivity.READY)
        fake_channel.close = AsyncMock()

        fake_module = type(sys)("abbenay_grpc")
        fake_module.AbbenayClient = MagicMock(return_value=MagicMock(_target="127.0.0.1:50057"))  # type: ignore[attr-defined]

        with (
            patch.dict(sys.modules, {"abbenay_grpc": fake_module, "abbenay_grpc.client": fake_client_module}),
            patch(
                "apme_engine.remediation.abbenay_client_factory.grpc.aio.secure_channel",
                return_value=fake_channel,
            ),
        ):
            client = _TlsAbbenayClient(
                host="127.0.0.1",
                port=50057,
                ca_cert_pem=b"pem-bytes",
            )
            first = asyncio.create_task(client.connect())
            await connect_started.wait()
            second = asyncio.create_task(client.reconnect())
            release_connect.set()
            await asyncio.gather(first, second)

        assert register_calls == 1
