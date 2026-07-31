"""Tests for Abbenay client factory TLS resolution (issue #400)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_auto_tls_when_default_ca_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Podman shared runtime CA enables TLS without explicit env.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        ca_path = "/tmp/abbenay-run/abbenay/tls/ca.crt"
        trusted = os.stat_result((stat.S_IFREG | 0o644, 0, 0, 0, os.getuid(), 0, 0, 0, 0, 0))
        with (
            patch("apme_engine.remediation.abbenay_client_factory.os.path.isfile", return_value=True),
            patch("apme_engine.remediation.abbenay_client_factory.os.stat", return_value=trusted),
        ):
            cfg = resolve_abbenay_tls_config("127.0.0.1:50057")
        assert cfg.enabled is True
        assert cfg.ca_cert == ca_path

    def test_ignores_untrusted_default_ca(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """World-writable CA files under /tmp are not auto-trusted.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
        monkeypatch.delenv("APME_ABBENAY_CA_CERT", raising=False)
        untrusted = os.stat_result((stat.S_IFREG | 0o666, 0, 0, 0, os.getuid(), 0, 0, 0, 0, 0))
        with (
            patch("apme_engine.remediation.abbenay_client_factory.os.path.isfile", return_value=True),
            patch("apme_engine.remediation.abbenay_client_factory.os.stat", return_value=untrusted),
        ):
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
        ca_path.write_text("pem", encoding="utf-8")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        discovered = _discover_default_ca_cert()
        assert discovered == str(ca_path)


class TestBuildAbbenayClient:
    """Client construction for TCP, Unix, and TLS paths."""

    def test_plaintext_tcp_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TCP without TLS builds a standard AbbenayClient.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ABBENAY_TLS", raising=False)
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
            patch("apme_engine.remediation.abbenay_client_factory.os.path.isfile", return_value=False),
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
