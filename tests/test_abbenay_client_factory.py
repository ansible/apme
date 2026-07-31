"""Tests for Abbenay client factory TLS resolution (issue #400)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from apme_engine.remediation.abbenay_client_factory import (
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
        with patch("apme_engine.remediation.abbenay_client_factory.os.path.isfile", return_value=True):
            cfg = resolve_abbenay_tls_config("127.0.0.1:50057")
        assert cfg.enabled is True
        assert cfg.ca_cert == "/tmp/abbenay-run/abbenay/tls/ca.crt"


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
