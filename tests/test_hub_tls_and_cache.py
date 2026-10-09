"""Regression coverage for Hub TLS settings and cache identity after restart."""

from __future__ import annotations

import argparse
import asyncio
import configparser
import json
import logging
import ssl
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.cli._galaxy_config import parse_galaxy_servers
from galaxy_proxy.collection_downloader import (
    GalaxyServerConfig,
    _inject_galaxy_env,
    download_error_summary,
    write_temp_ansible_cfg,
)
from galaxy_proxy.proxy import server as proxy_server
from galaxy_proxy.proxy.cache import ProxyCache
from galaxy_proxy.proxy.server import _load_servers_from_ansible_cfg, create_app


@pytest.mark.parametrize("verify", [True, False, None])  # type: ignore[untyped-decorator]
def test_server_tls_survives_native_cli_configuration(tmp_path: Path, verify: bool | None) -> None:
    """Both native CLI injection paths preserve explicit TLS policy.

    Args:
        tmp_path: Temporary configuration directory.
        verify: Per-server policy, including inherited default.
    """
    server = GalaxyServerConfig("hub", "https://hub.example.com/api/galaxy/", validate_certs=verify)
    env: dict[str, str] = {"ANSIBLE_GALAXY_IGNORE": "true", "ANSIBLE_GALAXY_SERVER_HUB_VALIDATE_CERTS": "false"}
    _inject_galaxy_env(env, [server])
    assert env.get("ANSIBLE_GALAXY_SERVER_HUB_VALIDATE_CERTS") == (str(verify).lower() if verify is not None else None)
    cfg = configparser.ConfigParser()
    cfg.read(write_temp_ansible_cfg([server], tmp_path))
    assert cfg.get("galaxy_server.hub", "validate_certs", fallback=None) == (
        str(verify).lower() if verify is not None else None
    )
    message = GalaxyServerDef(name="hub", url=server.url, validate_certs=verify)
    restored = GalaxyServerDef()
    restored.ParseFromString(message.SerializeToString())
    assert restored.HasField("validate_certs") == (verify is not None)
    if verify is not None:
        assert restored.validate_certs == verify


def test_restart_preserves_hub_cache_after_initial_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Identical first config pushes retain wheels, and old sources stay gated.

    Args:
        tmp_path: Persistent proxy cache used across app instances.
        monkeypatch: Environment fixture for proxy admin authentication.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("APME_PROXY_REQUIRE_GATEWAY_CONFIG", "1")
    headers = {"x-apme-proxy-token": "test-admin"}
    payload = {
        "servers": [
            {"name": "hub", "url": "https://hub.example.com/", "token": "private-token", "validate_certs": False}
        ]
    }
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    with TestClient(create_app(cache_dir=tmp_path, enable_passthrough=False)) as first:
        assert first.post("/admin/galaxy-config", json=payload, headers=headers).status_code == 200
        ProxyCache(tmp_path).put_wheel(filename, b"cached-artifact")
    with TestClient(create_app(cache_dir=tmp_path, enable_passthrough=False)) as restarted:
        assert restarted.get("/wheels/" + filename).status_code == 503
        assert restarted.get("/simple/Ansible_Collection_Ansible_Posix/").status_code == 503
        assert (
            restarted.post("/admin/prepare-collections", json={"specs": ["ansible.posix"]}, headers=headers).status_code
            == 503
        )
        assert restarted.post("/admin/galaxy-config", json=payload, headers=headers).status_code == 200
        assert restarted.get("/wheels/" + filename).content == b"cached-artifact"
        payload["servers"][0]["validate_certs"] = True
        assert restarted.post("/admin/galaxy-config", json=payload, headers=headers).status_code == 200
        assert ProxyCache(tmp_path).get_wheel(filename) is None
    marker = (tmp_path / ".galaxy-source.sha256").read_text()
    assert len(json.loads(marker)["fingerprint"]) == 64 and "private-token" not in marker


def test_native_environment_rotation_rebinds_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone native CLI configuration can rotate without a Gateway.

    Args:
        tmp_path: Persistent cache directory.
        monkeypatch: Environment fixture for native configuration.
    """
    monkeypatch.setenv("ANSIBLE_GALAXY_SERVER_LIST", "hub")
    monkeypatch.setenv("ANSIBLE_GALAXY_SERVER_HUB_URL", "https://hub.example.com/")
    create_app(cache_dir=tmp_path, enable_passthrough=False)
    ProxyCache(tmp_path).put_wheel("old.whl", b"old")
    monkeypatch.setenv("ANSIBLE_GALAXY_SERVER_HUB_TOKEN", "rotated")
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    with TestClient(app) as client:
        assert client.get("/simple/").status_code == 200
    assert app.state.galaxy_config_ready
    assert ProxyCache(tmp_path).get_wheel("old.whl") is None


async def test_periodic_gateway_sync_survives_failed_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Periodic reconciliation retries and restores a restarted proxy.

    Args:
        monkeypatch: Fixture replacing sync scheduling and clock delay.
    """
    from apme_gateway import _galaxy_proxy_sync as sync

    pushed = AsyncMock(side_effect=[False, True, True])
    monkeypatch.setattr(sync, "push_galaxy_config", pushed)
    sleeps = 0

    async def tick(interval: float) -> None:
        nonlocal sleeps
        assert interval == 15
        if sync._pending_push is not None:
            await sync._pending_push
        sleeps += 1
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await sync.reconcile_galaxy_config()
    assert pushed.await_count == 3


def test_native_config_change_invalidates_wheels_during_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local scan cannot reuse another Hub's wheels after config activation.

    Args:
        tmp_path: Configuration and cache directory.
        monkeypatch: Environment fixture selecting a scan configuration.
    """
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://first.example.com/\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    cache_dir = tmp_path / "cache"
    with TestClient(create_app(cache_dir=cache_dir, enable_passthrough=False)) as client:
        ProxyCache(cache_dir).put_wheel(filename, b"first-hub-artifact")
        assert client.get("/wheels/" + filename).content == b"first-hub-artifact"
        cfg.write_text("[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://second.example.com/\n")
        with patch("galaxy_proxy.proxy.server._download_and_convert", AsyncMock(side_effect=RuntimeError("offline"))):
            assert client.get("/wheels/" + filename).status_code == 502
        assert ProxyCache(cache_dir).get_wheel(filename) is None


def test_equivalent_native_session_configs_preserve_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Identical Hub configuration at a new session path keeps its artifacts.

    Args:
        tmp_path: Session and cache root.
        monkeypatch: Native session configuration fixture.
    """
    first = tmp_path / "session-one.cfg"
    second = tmp_path / "session-two.cfg"
    content = "[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://hub.example.com/\n"
    first.write_text(content)
    second.write_text(content)
    monkeypatch.setenv("ANSIBLE_CONFIG", str(first))
    cache_dir = tmp_path / "cache"
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    original = app.state.galaxy_source_fingerprint
    with TestClient(app) as client:
        ProxyCache(cache_dir).put_wheel(filename, b"hub-wheel")
        monkeypatch.setenv("ANSIBLE_CONFIG", str(second))
        read_bytes = Path.read_bytes
        reads: list[Path] = []

        def tracked_read(path: Path) -> bytes:
            reads.append(path)
            return read_bytes(path)

        with patch.object(Path, "read_bytes", tracked_read):
            assert client.get("/wheels/" + filename).content == b"hub-wheel"
        assert second in reads
        assert set(reads) <= {second, cache_dir / "wheels" / filename}
        assert app.state.galaxy_source_fingerprint == original
    with TestClient(create_app(cache_dir=cache_dir, enable_passthrough=False)) as restarted:
        assert restarted.get("/wheels/" + filename).content == b"hub-wheel"


def test_native_restart_retains_unverified_wheels_until_scan_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daemon startup keeps Hub wheels inaccessible until a scan restores its source.

    Args:
        tmp_path: Session configuration and cache root.
        monkeypatch: Native daemon startup/scan environment fixture.
    """
    first = tmp_path / "session-one.cfg"
    second = tmp_path / "session-two.cfg"
    content = "[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://hub.example.com/\n"
    first.write_text(content)
    second.write_text(content)
    monkeypatch.setenv("ANSIBLE_CONFIG", str(first))
    cache_dir = tmp_path / "cache"
    create_app(cache_dir=cache_dir, enable_passthrough=False)
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    ProxyCache(cache_dir).put_wheel(filename, b"hub-wheel")
    original = ProxyCache(cache_dir).config_identity()
    monkeypatch.delenv("ANSIBLE_CONFIG")
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    assert not app.state.galaxy_config_ready and not app.state.galaxy_config_managed
    assert ProxyCache(cache_dir).config_identity() == original
    assert ProxyCache(cache_dir).get_wheel(filename) == b"hub-wheel"
    monkeypatch.setenv("ANSIBLE_CONFIG", str(second))
    with TestClient(app) as restarted:
        assert restarted.get("/wheels/" + filename).content == b"hub-wheel"
        assert app.state.galaxy_config_ready
    assert ProxyCache(cache_dir).config_identity() == original


def test_bootc_gateway_gate_has_shared_admin_configuration() -> None:
    """All bootc services share the admin token used by the proxy startup gate."""
    root = Path(__file__).resolve().parents[1] / "deploy" / "bootc"
    for name in ("engine", "gateway", "galaxy-proxy"):
        assert "EnvironmentFile=/etc/apme/env/apme.env" in (root / "quadlet" / f"apme-{name}.container").read_text()
    assert (
        "Environment=APME_PROXY_REQUIRE_GATEWAY_CONFIG=1" in (root / "quadlet/apme-galaxy-proxy.container").read_text()
    )
    assert "APME_PROXY_ADMIN_TOKEN=" in (root / "apme.env.example").read_text()


@pytest.mark.parametrize(
    "setting",
    [
        "env_token",
        "ini_token",
        "env_home",
        "ini_home",
        "env_token_cwd",
        "ini_token_cwd",
        "env_home_cwd",
        "ini_home_cwd",
    ],
)  # type: ignore[untyped-decorator]
def test_native_file_token_rotation_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    """File credentials participate in identity under native path precedence.

    Args:
        tmp_path: Credential, configuration, and cache root.
        monkeypatch: Native path configuration fixture.
        setting: Environment or INI token/home selector.
    """
    cfg = tmp_path / "ansible.cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    monkeypatch.delenv("ANSIBLE_GALAXY_TOKEN_PATH", raising=False)
    monkeypatch.delenv("ANSIBLE_HOME", raising=False)
    token = tmp_path / "galaxy_token"
    token_value = "{{CWD}}/galaxy_token" if setting.endswith("_cwd") else str(token)
    home_value = "{{CWD}}" if setting.endswith("_cwd") else str(tmp_path)
    if setting.startswith("env_token"):
        monkeypatch.setenv("ANSIBLE_GALAXY_TOKEN_PATH", token_value)
        cfg.write_text("[galaxy]\nserver=https://hub.example.com/\n")
    elif setting.startswith("ini_token"):
        ini_value = token_value if setting.endswith("_cwd") else "galaxy_token"
        cfg.write_text(f"[galaxy]\nserver=https://hub.example.com/\ntoken_path={ini_value}\n")
    elif setting.startswith("env_home"):
        monkeypatch.setenv("ANSIBLE_HOME", home_value)
        cfg.write_text("[galaxy]\nserver=https://hub.example.com/\n")
    else:
        cfg.write_text(f"[defaults]\nhome={home_value}\n[galaxy]\nserver=https://hub.example.com/\n")
    token.write_text("token: first-credential\n")
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with TestClient(app) as client:
        original = app.state.galaxy_source_fingerprint
        ProxyCache(cache_dir).put_wheel("old.whl", b"old-credential-wheel")
        token.write_text("token: other-credential\n")
        assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel("old.whl") is None
        original = app.state.galaxy_source_fingerprint
        ProxyCache(cache_dir).put_wheel("old.whl", b"rotated-credential-wheel")
        token.unlink()
        assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel("old.whl") is None


def test_default_ca_rotation_invalidates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default trust roots are tracked even without SSL environment overrides.

    Args:
        tmp_path: Trust and cache root.
        monkeypatch: Default SSL trust path fixture.
    """
    cert = tmp_path / "default-roots.pem"
    cert.write_text("first trust root")
    directory = tmp_path / "roots"
    directory.mkdir()
    trust = ssl.get_default_verify_paths()._replace(
        cafile=str(cert), capath=str(directory), openssl_cafile=str(cert), openssl_capath=str(directory)
    )
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: trust)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with TestClient(app) as client:
        original = app.state.galaxy_source_fingerprint
        ProxyCache(cache_dir).put_wheel("old.whl", b"old-trust-wheel")
        cert.write_text("other trust root")
        assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel("old.whl") is None


@pytest.mark.parametrize("override", ["directory", "missing"])  # type: ignore[untyped-decorator]
def test_native_config_directory_and_fallback_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    """Native directory overrides and fallback files invalidate changed sources.

    Args:
        tmp_path: Native configuration and cache root.
        monkeypatch: Config discovery fixture.
        override: Directory selection or a missing override requiring fallback.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANSIBLE_CONFIG", str(tmp_path if override == "directory" else tmp_path / "missing.cfg"))
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy]\nserver=https://first.example.com/\n")
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with TestClient(app) as client:
        original = app.state.galaxy_source_fingerprint
        ProxyCache(cache_dir).put_wheel("old.whl", b"first-source-wheel")
        cfg.write_text("[galaxy]\nserver=https://other.example.com/\n")
        assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel("old.whl") is None


def test_explicit_config_identity_ignores_inactive_environment_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ignored environment file cannot overwrite the explicit config digest.

    Args:
        tmp_path: Active/inactive config and cache root.
        monkeypatch: Inactive native environment selection fixture.
    """
    active = tmp_path / "a-active.cfg"
    inactive = tmp_path / "z-inactive.cfg"
    active.write_text("[galaxy]\nserver=https://first.example.com/\n")
    inactive.write_text("[galaxy]\nserver=https://ignored.example.com/\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(inactive))
    app = create_app(ansible_cfg_path=active, cache_dir=tmp_path / "cache", enable_passthrough=False)
    with TestClient(app) as client:
        original = app.state.galaxy_source_fingerprint
        active.write_text("[galaxy]\nserver=https://other.example.com/\n")
        assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original


def test_gateway_identity_tracks_inherited_config_tls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nullable Gateway TLS overrides retain native INI policy in cache identity.

    Args:
        tmp_path: Native config and cache root.
        monkeypatch: Native config and admin token fixture.
    """
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy_server.hub]\nvalidate_certs=false\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with TestClient(app) as client:
        payload = {"servers": [{"name": "hub", "url": "https://hub.example.com/"}]}
        headers = {"x-apme-proxy-token": "test-admin"}
        assert client.post("/admin/galaxy-config", json=payload, headers=headers).status_code == 200
        original = app.state.galaxy_source_fingerprint
        ProxyCache(cache_dir).put_wheel("old.whl", b"unverified-wheel")
        cfg.write_text("[galaxy_server.hub]\nvalidate_certs=true\n")
        assert client.post("/admin/galaxy-config", json=payload, headers=headers).status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel("old.whl") is None


@pytest.mark.parametrize("marker", [None, "invalid"])  # type: ignore[untyped-decorator]
def test_gateway_mode_waits_even_without_cache_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str | None
) -> None:
    """Missing or corrupt identity cannot trigger public Galaxy discovery.

    Args:
        tmp_path: Persistent cache directory.
        monkeypatch: Proxy deployment environment fixture.
        marker: Optional damaged identity marker.
    """
    monkeypatch.setenv("APME_PROXY_REQUIRE_GATEWAY_CONFIG", "1")
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    if marker is not None:
        (tmp_path / ".galaxy-source.sha256").write_text(marker)
    with TestClient(create_app(cache_dir=tmp_path, enable_passthrough=False)) as client:
        assert client.get("/simple/ansible-collection-ansible-posix/").status_code == 503
        response = client.post(
            "/admin/galaxy-config", json={"servers": []}, headers={"x-apme-proxy-token": "test-admin"}
        )
        assert response.status_code == 200
        assert client.get("/simple/").status_code == 200


@pytest.mark.parametrize("value", ["y", "t", "true", "yes", "on", "1"])  # type: ignore[untyped-decorator]
def test_native_tls_boolean_and_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Both parsers honor native booleans and environment-over-INI defaults.

    Args:
        tmp_path: Configuration directory.
        monkeypatch: Native CLI environment fixture.
        value: A valid Ansible true spelling.
    """
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text(
        f"[galaxy]\nserver_list=hub\nignore_certs={value}\n[galaxy_server.hub]\nurl=https://hub.example.com/\n"
    )
    monkeypatch.setenv("ANSIBLE_GALAXY_IGNORE", "false")
    assert parse_galaxy_servers(cfg)[0].validate_certs
    servers = _load_servers_from_ansible_cfg(cfg)
    assert servers and servers[0].validate_certs
    cfg.write_text(cfg.read_text() + f"validate_certs={value}\n")
    monkeypatch.setenv("ANSIBLE_GALAXY_IGNORE", "true")
    assert parse_galaxy_servers(cfg)[0].validate_certs
    cfg.write_text(cfg.read_text().replace(f"validate_certs={value}", "validate_certs=invalid"))
    with pytest.raises(ValueError, match="Invalid Galaxy TLS boolean"):
        parse_galaxy_servers(cfg)
    assert _load_servers_from_ansible_cfg(cfg) == []


def test_native_startup_does_not_inherit_managed_mode(tmp_path: Path) -> None:
    """A standalone daemon can share a cache previously used by Gateway.

    Args:
        tmp_path: Persistent cache directory.
    """
    ProxyCache(tmp_path).bind_config("b" * 64, managed=True)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    assert app.state.galaxy_config_ready
    assert not app.state.galaxy_config_managed
    assert ProxyCache(tmp_path).config_identity() == (app.state.galaxy_source_fingerprint, False)


def test_native_reconciliation_recovers_interrupted_binding(tmp_path: Path) -> None:
    """A cancelled source transition can recover after the native source resets.

    Args:
        tmp_path: Persistent cache directory.
    """
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    original_fingerprint = app.state.galaxy_source_fingerprint
    ProxyCache(tmp_path).bind_config("b" * 64)
    app.state.galaxy_config_ready = False
    with TestClient(app) as client:
        assert client.get("/simple/").status_code == 200
    assert app.state.galaxy_config_ready
    assert ProxyCache(tmp_path).config_identity() == (original_fingerprint, False)


def test_cache_binding_failure_disables_stale_reads(tmp_path: Path) -> None:
    """A failed source transition cannot reuse artifacts from the old source.

    Args:
        tmp_path: Proxy cache directory.
    """
    cache = ProxyCache(tmp_path)
    cache.put_wheel("old.whl", b"old")
    with patch.object(ProxyCache, "clear", side_effect=PermissionError("readonly")):
        cache.bind_config("a" * 64)
    assert not cache.enabled
    assert cache.get_wheel("old.whl") is None
    assert cache.wheel_path("old.whl") is None
    with pytest.raises(OSError):
        cache.put_wheel("new.whl", b"new")


@pytest.mark.parametrize(
    "diagnostic",
    [
        "CERTIFICATE_VERIFY_FAILED private-token",
        "HTTP Error 403 private-token",
        "Could not satisfy the following requirements: private-token",
    ],
)  # type: ignore[untyped-decorator]
def test_cli_error_classification_never_exposes_upstream_text(diagnostic: str) -> None:
    """Failure descriptions remain fixed messages with no upstream secrets.

    Args:
        diagnostic: CLI output containing a token-like secret.
    """
    summary = download_error_summary(diagnostic)
    assert "private-token" not in summary
    assert summary


@pytest.mark.parametrize(
    ("diagnostic", "auth_failure"),
    [
        ("HTTP Error 401: denied", True),
        ("HTTP 403", True),
        ("HTTP status code: 403", True),
        ("unauthorized", True),
        ("forbidden", True),
        ("version 1.401.0 is unavailable", False),
        ("artifact 4030 missing", False),
        ("HTTP Error 4030", False),
    ],
)  # type: ignore[untyped-decorator]
def test_cli_numeric_auth_errors_require_http_context(diagnostic: str, auth_failure: bool) -> None:
    """Status classification ignores unrelated numbers and partial status codes.

    Args:
        diagnostic: CLI diagnostic to classify.
        auth_failure: Whether authentication failed according to the diagnostic.
    """
    assert ("authentication" in download_error_summary(diagnostic)) is auth_failure


@pytest.mark.parametrize("managed", [False, True])  # type: ignore[untyped-decorator]
def test_ca_directory_rotation_invalidates_cached_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    """Rotating certificate contents at the same path invalidates either mode.

    Args:
        tmp_path: Certificate and cache root.
        monkeypatch: Trust and admin environment fixture.
        managed: Whether Gateway owns the proxy configuration.
    """
    ca_dir = tmp_path / "certs"
    ca_dir.mkdir()
    cert = ca_dir / "root.pem"
    cert.write_text("first certificate")
    (ca_dir / "12345678.0").symlink_to(cert)
    monkeypatch.setenv("SSL_CERT_DIR", str(ca_dir))
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with TestClient(app) as client:
        if managed:
            assert (
                client.post(
                    "/admin/galaxy-config", json={"servers": []}, headers={"x-apme-proxy-token": "test-admin"}
                ).status_code
                == 200
            )
        ProxyCache(cache_dir).put_wheel(filename, b"old-trust-wheel")
        original = app.state.galaxy_source_fingerprint
        cert.write_text("other certificate")
        if managed:
            assert (
                client.post(
                    "/admin/galaxy-config", json={"servers": []}, headers={"x-apme-proxy-token": "test-admin"}
                ).status_code
                == 200
            )
        else:
            assert client.get("/simple/").status_code == 200
        assert app.state.galaxy_source_fingerprint != original
        assert ProxyCache(cache_dir).get_wheel(filename) is None


def test_cached_requests_do_not_read_configuration_or_rehash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged cached wheel requests skip parsing and full fingerprinting.

    Args:
        tmp_path: Configuration and cache root.
        monkeypatch: Native configuration fixture.
    """
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://hub.example.com/\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    cache_dir = tmp_path / "cache"
    app = create_app(cache_dir=cache_dir, enable_passthrough=False)
    filename = "ansible_collection_ansible_posix-2.2.2-py3-none-any.whl"
    ProxyCache(cache_dir).put_wheel(filename, b"cached-wheel")
    with (
        TestClient(app) as client,
        patch.object(proxy_server, "_source_fingerprint") as fingerprint,
        patch.object(proxy_server, "_load_servers_from_ansible_cfg") as parse,
    ):
        for _ in range(3):
            assert client.get("/wheels/" + filename).content == b"cached-wheel"
        fingerprint.assert_not_called()
        parse.assert_not_called()


async def test_slow_native_probe_does_not_block_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata access on slow storage runs outside the HTTP event loop.

    Args:
        tmp_path: Cache root.
        monkeypatch: Fixture delaying configuration metadata access.
    """
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    original = proxy_server._source_probe
    entered = threading.Event()
    release = threading.Event()

    def slow_probe(servers: list[GalaxyServerConfig] | None, cfg_path: Path | None) -> str:
        entered.set()
        assert release.wait(5)
        return original(servers, cfg_path)

    monkeypatch.setattr(proxy_server, "_source_probe", slow_probe)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://proxy") as client:
        request = asyncio.create_task(client.get("/simple/"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await asyncio.wait_for(client.get("/health"), 2)).status_code == 200
        finally:
            release.set()
            await request


def test_native_download_parses_config_in_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncached collection requests also parse configuration off the event loop.

    Args:
        tmp_path: Configuration and cache root.
        monkeypatch: Fixture selecting native configuration.
    """
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://hub.example.com/\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    original = proxy_server._load_servers_from_ansible_cfg

    def parse(path: Path) -> list[GalaxyServerConfig] | None:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return original(path)

    with (
        TestClient(create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)) as client,
        patch.object(proxy_server, "_load_servers_from_ansible_cfg", side_effect=parse) as parser,
        patch.object(proxy_server, "_download_and_convert", AsyncMock(side_effect=RuntimeError("offline"))),
    ):
        assert client.get("/wheels/ansible_collection_ansible_posix-2.2.2-py3-none-any.whl").status_code == 502
        parser.assert_called_once()


def test_conversion_config_conflict_is_not_an_item_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source change during conversion aborts the request with a retry signal.

    Args:
        tmp_path: Tarball and cache root.
        monkeypatch: Admin authentication fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    tarballs = tmp_path / "tarballs"
    tarballs.mkdir()
    for name in ("a.tar.gz", "b.tar.gz"):
        (tarballs / name).write_bytes(b"tarball")
    app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)

    def convert(data: bytes) -> tuple[str, bytes]:
        app.state.galaxy_config_generation += 1
        return "old.whl", data

    with TestClient(app) as client, patch.object(proxy_server, "tarball_to_wheel", side_effect=convert) as converter:
        response = client.post(
            "/convert-tarballs", params={"tarball_dir": str(tarballs)}, headers={"x-apme-proxy-token": "test-admin"}
        )
        assert response.status_code == 409
        assert "retry" in response.json()["detail"]
        converter.assert_called_once()
        assert ProxyCache(tmp_path / "cache").get_wheel("old.whl") is None


@pytest.mark.parametrize("command", ["check", "remediate"])  # type: ignore[untyped-decorator]
def test_cli_invalid_tls_exits_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """Bad user TLS settings identify the file/server/setting without values.

    Args:
        tmp_path: Scan and configuration root.
        monkeypatch: Configuration fixture.
        capsys: CLI output capture.
        command: Assessment or remediation entry point.
    """
    from apme_engine.cli import check, remediate
    from apme_engine.cli._exit_codes import EXIT_ERROR

    cfg = tmp_path / "ansible.cfg"
    cfg.write_text(
        "[galaxy]\nserver_list=hub\n[galaxy_server.hub]\nurl=https://hub.example.com/\n"
        "validate_certs=secret-invalid-value\n"
    )
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    module = check if command == "check" else remediate
    run = check.run_check if command == "check" else remediate.run_remediate
    with (
        patch.object(module, "resolve_scan_context", return_value=([tmp_path], tmp_path, tmp_path)),
        patch.object(module, "yield_scan_chunks") as upload,
        pytest.raises(SystemExit) as exit_info,
    ):
        run(argparse.Namespace(target=str(tmp_path)))
    assert exit_info.value.code == EXIT_ERROR
    error = capsys.readouterr().err
    assert str(cfg) in error and "hub" in error and "validate_certs" in error
    assert "Traceback" not in error and "secret-invalid-value" not in error
    upload.assert_not_called()


def test_proxy_refresh_logs_only_changes_at_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Identical reconciliation payloads emit no proxy INFO records.

    Args:
        tmp_path: Cache root.
        monkeypatch: Admin authentication fixture.
        caplog: Log capture fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "test-admin")
    with TestClient(create_app(cache_dir=tmp_path, enable_passthrough=False)) as client:
        caplog.set_level(logging.DEBUG, logger="galaxy_proxy.proxy.server")
        headers = {"x-apme-proxy-token": "test-admin"}
        assert client.post("/admin/galaxy-config", json={"servers": []}, headers=headers).status_code == 200
        assert any("Galaxy config updated" in r.message and r.levelno == logging.INFO for r in caplog.records)
        caplog.clear()
        assert client.post("/admin/galaxy-config", json={"servers": []}, headers=headers).status_code == 200
        assert not any(r.levelno >= logging.INFO and r.name == "galaxy_proxy.proxy.server" for r in caplog.records)


async def test_sync_logs_outcome_transitions(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Repeated periodic outcomes are quiet, while failure/recovery are visible.

    Args:
        monkeypatch: Stubbed DB, HTTP, and sync state fixture.
        caplog: Log capture fixture.
    """
    from apme_gateway import _galaxy_proxy_sync as sync
    from tests.test_review_fixes_proxy_auth import _install_push_stubs

    _install_push_stubs(monkeypatch)
    monkeypatch.setattr(sync, "_last_push_ok", None)
    caplog.set_level(logging.DEBUG, logger=sync.__name__)
    assert await sync.push_galaxy_config(periodic=True)
    caplog.clear()
    assert await sync.push_galaxy_config(periodic=True)
    assert not any(r.name == sync.__name__ and r.levelno >= logging.INFO for r in caplog.records)
    with patch("apme_gateway.db.queries.list_galaxy_servers", AsyncMock(side_effect=RuntimeError("DB offline"))):
        caplog.clear()
        assert not await sync.push_galaxy_config(periodic=True)
        assert any(r.levelno == logging.WARNING and r.exc_info for r in caplog.records)
        caplog.clear()
        assert not await sync.push_galaxy_config(periodic=True)
        assert not any(r.name == sync.__name__ and (r.levelno >= logging.INFO or r.exc_info) for r in caplog.records)
    caplog.clear()
    assert await sync.push_galaxy_config(periodic=True)
    assert any(r.name == sync.__name__ and r.levelno == logging.INFO for r in caplog.records)
