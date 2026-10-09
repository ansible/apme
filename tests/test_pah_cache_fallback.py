"""Collection cache reuse and Private Automation Hub fallback regressions."""

from __future__ import annotations

import asyncio
import io
import threading
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from galaxy_proxy.collection_downloader import DownloadResult, GalaxyServerConfig
from galaxy_proxy.proxy.cache import ProxyCache
from galaxy_proxy.proxy.server import create_app

WHEEL = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
HUB_URL = "https://hub.example.com/api/galaxy/content/rh-certified/"
ADMIN_TOKEN = "admin-token"


def _wheel_bytes(
    distribution: str = "ansible_collection_ansible_posix", version: str = "1.5.4", deps: tuple[str, ...] = ()
) -> bytes:
    """Build a minimal valid collection wheel for cache dependency checks.

    Args:
        distribution: Wheel distribution name.
        version: Wheel version.
        deps: Collection dependency requirements to add to METADATA.

    Returns:
        Valid wheel archive bytes.
    """
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as wheel:
        requires = "".join(f"Requires-Dist: {dep}\n" for dep in deps)
        wheel.writestr(
            f"{distribution}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {distribution.replace('_', '-')}\nVersion: {version}\n{requires}\n",
        )
    return output.getvalue()


@pytest.fixture()  # type: ignore[untyped-decorator]
def hub_server() -> GalaxyServerConfig:
    """Return a configured Private Automation Hub source.

    Returns:
        Galaxy server configuration used by tests.
    """
    return GalaxyServerConfig(name="portal_hub_rh_certified", url=HUB_URL, token="hub-token")


def _admin_headers() -> dict[str, str]:
    return {"x-apme-proxy-token": ADMIN_TOKEN}


def test_identical_authenticated_sync_preserves_cached_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub_server: GalaxyServerConfig
) -> None:
    """An identical Portal credential refresh does not clear cached artifacts.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
        hub_server: Configured Private Automation Hub source.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    cache = ProxyCache(tmp_path)
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    cache.put_wheel(WHEEL, b"cached-wheel")
    payload = {"servers": [{"name": hub_server.name, "url": hub_server.url, "token": hub_server.token}]}
    with TestClient(app) as client:
        response = client.post("/admin/galaxy-config", json=payload, headers=_admin_headers())
    assert response.status_code == 200
    assert cache.get_wheel(WHEEL) == b"cached-wheel"


@pytest.mark.parametrize("fault", ["read", "write"])  # type: ignore[untyped-decorator]
def test_cache_io_failure_still_serves_hub_download(tmp_path: Path, hub_server: GalaxyServerConfig, fault: str) -> None:
    """Cache storage failure cannot prevent serving a successful CLI download.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Configured Private Automation Hub source.
        fault: Cache operation to fail, either read or write.
    """
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    download = AsyncMock(return_value=(WHEEL, b"downloaded-wheel"))
    method = "get_wheel" if fault == "read" else "put_wheel"
    with (
        TestClient(app) as client,
        patch.object(ProxyCache, method, side_effect=PermissionError("cache unavailable")),
        patch("galaxy_proxy.proxy.server._download_and_convert", download),
    ):
        response = client.get(f"/wheels/{WHEEL}")
    assert response.status_code == 200
    assert response.content == b"downloaded-wheel"
    assert download.call_args.kwargs["galaxy_servers"] == [hub_server]


def test_project_page_wheel_survives_cache_write_failure(tmp_path: Path, hub_server: GalaxyServerConfig) -> None:
    """A project page link remains installable after a cache write failure.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Configured Private Automation Hub source.
    """
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    download = AsyncMock(return_value=(WHEEL, b"downloaded-wheel"))
    with (
        TestClient(app) as client,
        patch("galaxy_proxy.proxy.server._download_and_convert", download),
        patch.object(ProxyCache, "put_wheel", side_effect=PermissionError("cache unavailable")),
    ):
        page = client.get("/simple/ansible-collection-ansible-posix/")
        assert page.status_code == 200
        assert WHEEL in page.text
        wheel = client.get(f"/wheels/{WHEEL}")
    assert wheel.status_code == 200
    assert wheel.content == b"downloaded-wheel"
    download.assert_awaited_once()


def test_hub_config_change_discards_in_memory_wheel_handoff(
    tmp_path: Path, hub_server: GalaxyServerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wheel from old Hub credentials is not served after config changes.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Initial configured Hub source.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    download = AsyncMock(side_effect=[(WHEEL, b"old-hub-wheel"), (WHEEL, b"new-hub-wheel")])
    with (
        TestClient(app) as client,
        patch("galaxy_proxy.proxy.server._download_and_convert", download),
        patch.object(ProxyCache, "put_wheel", side_effect=PermissionError("cache unavailable")),
    ):
        page = client.get("/simple/ansible-collection-ansible-posix/")
        assert page.status_code == 200
        update = client.post(
            "/admin/galaxy-config",
            json={"servers": [{"name": hub_server.name, "url": hub_server.url, "token": "rotated-token"}]},
            headers=_admin_headers(),
        )
        assert update.status_code == 200
        wheel = client.get(f"/wheels/{WHEEL}")
    assert wheel.status_code == 200
    assert wheel.content == b"new-hub-wheel"
    assert download.await_count == 2


def test_prepare_collections_uses_cli_for_requested_pin_and_dependencies(
    tmp_path: Path, hub_server: GalaxyServerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact requested specs are handed to the configured CLI and cached.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Configured Private Automation Hub source.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)

    async def fake_download(specs: list[str], download_dir: Path, **kwargs: object) -> DownloadResult:
        tarball = download_dir / "ansible-posix-1.5.4.tar.gz"
        tarball.write_bytes(b"tarball")
        assert specs == ["ansible.posix:1.5.4"]
        assert kwargs["include_dependencies"] is True
        return DownloadResult(tarball_paths=[tarball])

    with (
        TestClient(app) as client,
        patch("galaxy_proxy.proxy.server.download_collections", side_effect=fake_download) as download,
        patch("galaxy_proxy.proxy.server.tarball_to_wheel", return_value=(WHEEL, _wheel_bytes())),
    ):
        response = client.post(
            "/admin/prepare-collections",
            json={"specs": ["ansible.posix:1.5.4"]},
            headers=_admin_headers(),
        )
    assert response.status_code == 200
    assert response.json() == {"prepared": [WHEEL], "failed_specs": []}
    assert download.await_count == 1
    assert ProxyCache(tmp_path).get_wheel(WHEEL) == _wheel_bytes()


def test_prepare_collections_serves_cached_pin_without_hub_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching cached wheel satisfies a pin without contacting the Hub.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    ProxyCache(tmp_path).put_wheel(WHEEL, _wheel_bytes())
    with TestClient(app) as client, patch("galaxy_proxy.proxy.server.download_collections") as download:
        response = client.post(
            "/admin/prepare-collections",
            json={"specs": ["ansible.posix:1.5.4"]},
            headers=_admin_headers(),
        )
    assert response.status_code == 200
    assert response.json()["failed_specs"] == []
    download.assert_not_called()


def test_failed_pin_remains_discoverable_when_another_version_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed exact pin links its versioned retry despite other cached versions.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    cache = ProxyCache(tmp_path)
    older_wheel = "ansible_collection_ansible_posix-1.4.0-py3-none-any.whl"
    pinned_wheel = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    cache.put_wheel(older_wheel, b"older-wheel")

    async def failed_download(*_args: object, **_kwargs: object) -> DownloadResult:
        return DownloadResult(failed_specs=["ansible.posix:1.5.4"])

    exact_download = AsyncMock(return_value=(pinned_wheel, b"pinned-wheel"))
    with (
        TestClient(app) as client,
        patch("galaxy_proxy.proxy.server.download_collections", side_effect=failed_download),
        patch("galaxy_proxy.proxy.server._download_and_convert", exact_download),
    ):
        prepared = client.post(
            "/admin/prepare-collections",
            json={"specs": ["ansible.posix:1.5.4"]},
            headers=_admin_headers(),
        )
        page = client.get("/simple/ansible-collection-ansible-posix/")
        pinned = client.get(f"/wheels/{pinned_wheel}")

    assert prepared.status_code == 200
    assert prepared.json()["failed_specs"] == ["ansible.posix:1.5.4"]
    assert older_wheel in page.text
    assert pinned_wheel in page.text
    assert pinned.status_code == 200
    assert pinned.content == b"pinned-wheel"
    assert exact_download.call_args.args[:3] == ("ansible", "posix", "1.5.4")


def test_prepare_collections_refreshes_cache_when_dependency_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached root wheel is refreshed when its collection dependency is absent.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    cache = ProxyCache(tmp_path)
    cache.put_wheel(WHEEL, _wheel_bytes(deps=("ansible-collection-community-general>=9",)))
    with (
        TestClient(app) as client,
        patch("galaxy_proxy.proxy.server.download_collections", AsyncMock(return_value=DownloadResult())) as download,
    ):
        response = client.post(
            "/admin/prepare-collections",
            json={"specs": ["ansible.posix:1.5.4"]},
            headers=_admin_headers(),
        )
    assert response.status_code == 200
    download.assert_awaited_once()


def test_prepare_collections_cache_hit_includes_dependency_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching cached root and dependency closure avoid a Hub request.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    cache = ProxyCache(tmp_path)
    cache.put_wheel(WHEEL, _wheel_bytes(deps=("ansible-collection-community-general>=9",)))
    dep_wheel = "ansible_collection_community_general-9.1.0-py3-none-any.whl"
    cache.put_wheel(
        dep_wheel,
        _wheel_bytes("ansible_collection_community_general", "9.1.0"),
    )
    with TestClient(app) as client, patch("galaxy_proxy.proxy.server.download_collections") as download:
        response = client.post(
            "/admin/prepare-collections",
            json={"specs": ["ansible.posix:1.5.4"]},
            headers=_admin_headers(),
        )
    assert response.status_code == 200
    assert response.json()["failed_specs"] == []
    download.assert_not_called()


def test_dependency_cycle_does_not_memoize_provisional_satisfaction(tmp_path: Path) -> None:
    """A cycle guard cannot hide a missing dependency from another root spec.

    Args:
        tmp_path: Temporary cache directory.
    """
    from galaxy_proxy.proxy.server import _cache_satisfies_spec

    cache = ProxyCache(tmp_path)
    cache.put_wheel(
        WHEEL,
        _wheel_bytes(
            deps=(
                "ansible-collection-community-general>=9",
                "ansible-collection-something-missing>=1",
            )
        ),
    )
    cache.put_wheel(
        "ansible_collection_community_general-9.1.0-py3-none-any.whl",
        _wheel_bytes(
            "ansible_collection_community_general",
            "9.1.0",
            deps=("ansible-collection-ansible-posix>=1",),
        ),
    )
    memo: dict[str, bool] = {}

    assert not _cache_satisfies_spec(cache, {}, "ansible.posix:1.5.4", _memo=memo)
    assert not _cache_satisfies_spec(cache, {}, "community.general:9.1.0", _memo=memo)


def test_prepare_collections_requires_admin_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI-backed prepare endpoint is protected by the proxy admin token.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    with TestClient(app) as client:
        response = client.post("/admin/prepare-collections", json={"specs": ["ansible.posix"]})
    assert response.status_code == 403


@pytest.mark.parametrize("bad_spec", ["--help", "ansible.posix;echo", "missing-dot", "ansible.posix:>=1, <2"])  # type: ignore[untyped-decorator]
def test_prepare_collections_rejects_unsafe_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_spec: str
) -> None:
    """User controlled collection arguments cannot become arbitrary CLI args.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
        bad_spec: Invalid collection spec sent to the endpoint.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    with TestClient(app) as client:
        response = client.post("/admin/prepare-collections", json={"specs": [bad_spec]}, headers=_admin_headers())
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["wheel", "latest"])  # type: ignore[untyped-decorator]
async def test_config_change_discards_inflight_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hub_server: GalaxyServerConfig,
    endpoint: str,
) -> None:
    """Downloads from replaced credentials cannot repopulate or serve cache.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
        hub_server: Original configured Hub source.
        endpoint: Download route held during the config change.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    started = asyncio.Event()
    release = asyncio.Event()

    async def paused(*_args: object, **_kwargs: object) -> tuple[str, bytes]:
        started.set()
        await release.wait()
        return WHEEL, b"old-source-wheel"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        path = f"/wheels/{WHEEL}" if endpoint == "wheel" else "/simple/ansible-collection-ansible-posix/"
        with patch("galaxy_proxy.proxy.server._download_and_convert", paused):
            request = asyncio.create_task(client.get(path))
            await asyncio.wait_for(started.wait(), timeout=1)
            response = await client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": hub_server.name, "url": hub_server.url, "token": "replacement-token"}]},
                headers=_admin_headers(),
            )
            assert response.status_code == 200
            release.set()
            assert (await request).status_code == 409
    assert ProxyCache(tmp_path).get_wheel(WHEEL) is None


async def test_prepare_requests_for_different_collections_do_not_serialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow preparation does not block an unrelated cached collection.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    cache = ProxyCache(tmp_path)
    other_wheel = "ansible_collection_community_general-9.1.0-py3-none-any.whl"
    cache.put_wheel(other_wheel, _wheel_bytes("ansible_collection_community_general", "9.1.0"))
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_download(*_args: object, **_kwargs: object) -> DownloadResult:
        started.set()
        await release.wait()
        return DownloadResult()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        with patch("galaxy_proxy.proxy.server.download_collections", side_effect=slow_download):
            slow = asyncio.create_task(
                client.post(
                    "/admin/prepare-collections",
                    json={"specs": ["ansible.posix:1.5.4"]},
                    headers=_admin_headers(),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            cache_hit = await asyncio.wait_for(
                client.post(
                    "/admin/prepare-collections",
                    json={"specs": ["community.general:9.1.0"]},
                    headers=_admin_headers(),
                ),
                timeout=1,
            )
            release.set()
            await slow
    assert cache_hit.status_code == 200
    assert cache_hit.json()["failed_specs"] == []


async def test_prepare_checks_cached_wheels_off_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disk and wheel metadata checks run in a worker thread.

    Args:
        tmp_path: Temporary cache directory.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, enable_passthrough=False)
    ProxyCache(tmp_path).put_wheel(WHEEL, _wheel_bytes())
    event_loop_thread = threading.get_ident()
    check_threads: list[int] = []
    from galaxy_proxy.proxy import server as proxy_server

    original_check = proxy_server._cache_satisfies_spec

    def record_check_thread(
        cache: ProxyCache,
        handoffs: dict[str, tuple[float, bytes]],
        spec: str,
        _visiting: set[str] | None = None,
        _memo: dict[str, bool] | None = None,
    ) -> bool:
        check_threads.append(threading.get_ident())
        return original_check(cache, handoffs, spec, _visiting, _memo)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        with patch("galaxy_proxy.proxy.server._cache_satisfies_spec", side_effect=record_check_thread):
            response = await client.post(
                "/admin/prepare-collections",
                json={"specs": ["ansible.posix:1.5.4"]},
                headers=_admin_headers(),
            )
    assert response.status_code == 200
    assert check_threads
    assert all(thread_id != event_loop_thread for thread_id in check_threads)


async def test_prepare_does_not_publish_wheel_after_config_change(
    tmp_path: Path, hub_server: GalaxyServerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale preparation response cannot publish old-Hub wheels.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Initially configured Hub source.
        monkeypatch: Pytest environment fixture.
    """
    monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", ADMIN_TOKEN)
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    cache = ProxyCache(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def paused_download(_specs: list[str], download_dir: Path, **_kwargs: object) -> DownloadResult:
        tarball = download_dir / "ansible-posix-1.5.4.tar.gz"
        tarball.write_bytes(b"old-hub-tarball")
        started.set()
        await release.wait()
        return DownloadResult(tarball_paths=[tarball])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        with (
            patch("galaxy_proxy.proxy.server.download_collections", side_effect=paused_download),
            patch("galaxy_proxy.proxy.server.tarball_to_wheel", return_value=(WHEEL, b"old-hub-wheel")),
        ):
            prepare = asyncio.create_task(
                client.post(
                    "/admin/prepare-collections",
                    json={"specs": ["ansible.posix:1.5.4"]},
                    headers=_admin_headers(),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            updated = await client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": hub_server.name, "url": hub_server.url, "token": "rotated"}]},
                headers=_admin_headers(),
            )
            assert updated.status_code == 200
            release.set()
            stale_response = await prepare
    assert stale_response.status_code == 409
    assert cache.get_wheel(WHEEL) is None


def test_cached_wheels_remain_visible_without_hub_access(tmp_path: Path, hub_server: GalaxyServerConfig) -> None:
    """An unavailable Hub does not hide an already cached collection wheel.

    Args:
        tmp_path: Temporary cache directory.
        hub_server: Configured Private Automation Hub source.
    """
    app = create_app(cache_dir=tmp_path, galaxy_servers=[hub_server], enable_passthrough=False)
    ProxyCache(tmp_path).put_wheel(WHEEL, b"cached-wheel")
    with TestClient(app) as client, patch("galaxy_proxy.proxy.server.download_collections") as download:
        response = client.get("/simple/ansible-collection-ansible-posix/")
    assert response.status_code == 200
    assert WHEEL in response.text
    download.assert_not_called()
