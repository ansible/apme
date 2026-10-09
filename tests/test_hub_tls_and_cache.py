"""Regression coverage for Hub TLS settings and cache identity after restart."""

from __future__ import annotations

import asyncio
import configparser
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.cli._galaxy_config import parse_galaxy_servers
from galaxy_proxy.collection_downloader import (
    GalaxyServerConfig,
    _inject_galaxy_env,
    download_error_summary,
    write_temp_ansible_cfg,
)
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
