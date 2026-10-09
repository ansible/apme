"""Tests for galaxy_proxy.proxy.server (PEP 503 API with ansible-galaxy download)."""

from __future__ import annotations

import unittest.mock
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from galaxy_proxy.proxy import server as proxy_server
from galaxy_proxy.proxy.server import _safe_server_label, create_app


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _open_admin_for_functional_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt out of fail-closed admin auth for functional (non-auth) tests.

    These tests exercise admin-endpoint behavior, not its auth gate (covered
    in test_review_fixes_proxy_auth.py), so they run with the explicit
    single-host opt-out enabled.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.delenv(proxy_server._ADMIN_TOKEN_ENV, raising=False)
    monkeypatch.setenv(proxy_server._ALLOW_UNAUTH_ADMIN_ENV, "1")


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("raw_url", "expected"),
    [
        ("https://user:secret@hub.example.com:8443/api/?token=secret", "hub.example.com:8443"),
        ("not-a-url-with-secret", "unknown"),
        ("https://hub.example.com:not-a-port/api/", "unknown"),
    ],
)
def test_safe_server_label_redacts_url_details(raw_url: str, expected: str) -> None:
    """Operational labels expose only a parsed hostname or a safe fallback.

    Args:
        raw_url: Untrusted server URL to sanitize.
        expected: Expected safe hostname label.
    """
    assert _safe_server_label(raw_url) == expected


def test_response_middleware_logs_unhandled_failures(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled request failures emit a status and duration event.

    Args:
        tmp_path: Temporary directory for the proxy cache.
        caplog: Captured log records.
    """
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)

    @application.get("/test-unhandled-failure")  # type: ignore[untyped-decorator]
    async def _fail() -> None:
        raise RuntimeError("sensitive failure detail")

    with (
        caplog.at_level("ERROR", logger="galaxy_proxy.proxy.server"),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        response = client.get("/test-unhandled-failure")

    assert response.status_code == 500
    assert "server_response method=GET path=/test-unhandled-failure status=500" in caplog.text
    assert "sensitive failure detail" not in caplog.text


@pytest.fixture()  # type: ignore[untyped-decorator]
def app(tmp_path: Path) -> Iterator[TestClient]:
    """Create a test client for the proxy app with temp cache.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        TestClient: FastAPI TestClient instance.
    """
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    with TestClient(application) as client:
        yield client


class TestHealth:
    """Tests for /health endpoint."""

    def test_health_ok(self, app: TestClient) -> None:
        """Health endpoint returns ok status.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestRootIndex:
    """Tests for /simple/ root endpoint."""

    def test_root_index(self, app: TestClient) -> None:
        """Root index returns HTML page.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/simple/")
        assert resp.status_code == 200
        assert "Ansible Collection Proxy" in resp.text


class TestProjectPage:
    """Tests for /simple/{package_name}/ endpoint."""

    def test_non_collection_no_passthrough(self, app: TestClient) -> None:
        """Non-collection package returns 404 when passthrough disabled.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/simple/requests/")
        assert resp.status_code == 404

    def test_collection_no_cached_wheels_downloads_latest(self, tmp_path: Path) -> None:
        """Collection with no cached wheels triggers on-demand download.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import DownloadResult

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        fake_tarball = tmp_path / "ansible-posix-1.5.4.tar.gz"
        fake_tarball.touch()

        mock_download = AsyncMock(
            return_value=DownloadResult(tarball_paths=[fake_tarball]),
        )
        whl_data = b"PK\x03\x04converted-wheel"
        whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
            patch(
                "galaxy_proxy.proxy.server.tarball_to_wheel",
                return_value=(whl_name, whl_data),
            ),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 200
        assert whl_name in resp.text
        assert "/wheels/" in resp.text

    def test_collection_no_cached_wheels_download_fails_502(self, tmp_path: Path) -> None:
        """When on-demand download fails, project page returns 502.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        mock_download = AsyncMock(side_effect=RuntimeError("Galaxy unreachable"))
        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 502

    def test_failed_latest_download_is_retried(self, tmp_path: Path) -> None:
        """Failed latest downloads are retried instead of cached as failures.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", AsyncMock(side_effect=RuntimeError("fail"))),
        ):
            resp1 = client.get("/simple/ansible-collection-ansible-posix/")
            assert resp1.status_code == 502

            resp2 = client.get("/simple/ansible-collection-ansible-posix/")
            assert resp2.status_code == 502

    def test_collection_with_cached_wheel(self, tmp_path: Path) -> None:
        """Collection with cached wheel lists it in the project page.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        wheels_dir = cache_dir / "wheels"
        wheels_dir.mkdir(parents=True)
        whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)
        (wheels_dir / whl_name).write_bytes(b"fake-wheel")
        with TestClient(application) as client:
            resp = client.get("/simple/ansible-collection-ansible-posix/")
        assert resp.status_code == 200
        assert whl_name in resp.text
        assert "/wheels/" in resp.text


class TestServeWheel:
    """Tests for /wheels/{filename} endpoint."""

    def test_cached_wheel(self, tmp_path: Path) -> None:
        """Cached wheel is served directly.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        wheels_dir = cache_dir / "wheels"
        wheels_dir.mkdir(parents=True)
        whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
        whl_data = b"PK\x03\x04fake-wheel-contents"
        application = create_app(cache_dir=cache_dir)
        (wheels_dir / whl_name).write_bytes(whl_data)
        with TestClient(application) as client:
            resp = client.get(f"/wheels/{whl_name}")
        assert resp.status_code == 200
        assert resp.content == whl_data
        assert resp.headers["content-disposition"] == f"attachment; filename={whl_name}"

    def test_invalid_filename_rejected(self, app: TestClient) -> None:
        """Invalid wheel filenames are rejected.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/wheels/not-a-wheel.txt")
        assert resp.status_code == 404

    def test_traversal_rejected(self, app: TestClient) -> None:
        """Path traversal attempts are rejected.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/wheels/../etc/passwd.whl")
        assert resp.status_code == 404

    def test_cache_miss_downloads_and_converts(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Cache miss triggers ansible-galaxy download and conversion.

        Args:
            tmp_path: Pytest-provided temporary directory.
            caplog: Pytest log capture fixture.
        """
        from galaxy_proxy.collection_downloader import DownloadResult

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        fake_tarball = tmp_path / "ansible-posix-1.5.4.tar.gz"
        fake_tarball.touch()

        mock_download = AsyncMock(
            return_value=DownloadResult(tarball_paths=[fake_tarball]),
        )
        whl_data = b"PK\x03\x04converted-wheel"

        with (
            caplog.at_level("INFO", logger="galaxy_proxy.proxy.server"),
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
            patch(
                "galaxy_proxy.proxy.server.tarball_to_wheel",
                return_value=("ansible_collection_ansible_posix-1.5.4-py3-none-any.whl", whl_data),
            ),
        ):
            resp = client.get("/wheels/ansible_collection_ansible_posix-1.5.4-py3-none-any.whl")

        assert resp.status_code == 200
        assert resp.content == whl_data
        completion = ""
        for record in caplog.records:
            if "collection_download_complete" in record.message:
                completion = record.message
                break
        assert completion
        assert "download_duration_ms=" in completion
        assert "conversion_duration_ms=" in completion
        assert "total_duration_ms=" in completion
        assert "tarball_size_bytes=0" in completion
        assert f"wheel_size_bytes={len(whl_data)}" in completion

    def test_cache_miss_download_failure(self, tmp_path: Path) -> None:
        """Download failure returns 502 with collection and server context.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import DownloadResult

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        mock_download = AsyncMock(
            return_value=DownloadResult(
                failed_specs=["ansible.posix:1.5.4"],
                stderr="Galaxy server unreachable",
            ),
        )

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        ):
            resp = client.get("/wheels/ansible_collection_ansible_posix-1.5.4-py3-none-any.whl")

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "ansible.posix" in detail

    def test_unparseable_namespace(self, app: TestClient) -> None:
        """Wheel with unparseable namespace/name returns 404.

        Args:
            app: Test client fixture.
        """
        resp = app.get("/wheels/ansible_collection_bad-1.0.0-py3-none-any.whl")
        assert resp.status_code == 404


class TestAdminGalaxyConfig:
    """Tests for POST /admin/galaxy-config endpoint."""

    def test_push_galaxy_config(self, app: TestClient) -> None:
        """Pushing galaxy server configs updates app state.

        Args:
            app: Test client fixture.
        """
        resp = app.post(
            "/admin/galaxy-config",
            json={
                "servers": [
                    {"name": "hub", "url": "https://hub.example.com", "token": "tok"},
                    {"name": "community", "url": "https://galaxy.ansible.com"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 2
        assert data["servers"] == ["hub", "community"]

    def test_push_clears_ansible_cfg_path(self, tmp_path: Path) -> None:
        """Pushing servers clears any pre-existing ansible_cfg_path.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.touch()
        application = create_app(
            cache_dir=tmp_path / "cache",
            enable_passthrough=False,
            ansible_cfg_path=cfg,
        )
        with TestClient(application) as client:
            assert client.app.state.ansible_cfg_path == cfg
            client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": "hub", "url": "https://hub.example.com"}]},
            )
            assert client.app.state.ansible_cfg_path is None

    def test_push_empty_servers(self, app: TestClient) -> None:
        """Pushing empty server list is accepted.

        Args:
            app: Test client fixture.
        """
        resp = app.post("/admin/galaxy-config", json={"servers": []})
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 0

    def test_push_updates_app_state(self, tmp_path: Path) -> None:
        """Pushing config updates app.state.galaxy_servers with correct types.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig as GSC

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        with TestClient(application) as client:
            client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": "myhub", "url": "https://hub.example.com", "token": "secret"}]},
            )
            servers = client.app.state.galaxy_servers
            assert len(servers) == 1
            assert isinstance(servers[0], GSC)
            assert servers[0].name == "myhub"
            assert servers[0].url == "https://hub.example.com"
            assert servers[0].token == "secret"

    def test_push_rejects_empty_name(self, app: TestClient) -> None:
        """Empty server name returns 422.

        Args:
            app: Test client fixture.
        """
        resp = app.post("/admin/galaxy-config", json={"servers": [{"name": "", "url": "https://x.com"}]})
        assert resp.status_code == 422

    def test_push_rejects_duplicate_name(self, app: TestClient) -> None:
        """Duplicate server names return 422.

        Args:
            app: Test client fixture.
        """
        resp = app.post(
            "/admin/galaxy-config",
            json={"servers": [{"name": "hub", "url": "https://a.com"}, {"name": "HUB", "url": "https://b.com"}]},
        )
        assert resp.status_code == 422

    def test_push_accepts_hyphenated_name(self, app: TestClient) -> None:
        """Hyphenated server names are accepted for proxy sync.

        Args:
            app: Test client fixture.
        """
        resp = app.post(
            "/admin/galaxy-config",
            json={"servers": [{"name": "automation-hub", "url": "https://hub.example.com"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["servers"] == ["automation-hub"]

    def test_push_clears_cache(self, tmp_path: Path) -> None:
        """Pushing new Galaxy config clears cached wheels.

        Prevents stale data from previous server selection being served
        after a configuration change.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        with TestClient(application) as client:
            # Put some data in the cache to verify it gets cleared
            from galaxy_proxy.proxy.cache import ProxyCache

            cache = ProxyCache(cache_dir=cache_dir)
            cache.put_wheel(
                "ansible_collection_ansible_posix-1.0.0-py3-none-any.whl",
                b"fake wheel data",
            )

            # Verify cache has data
            assert cache.get_wheel("ansible_collection_ansible_posix-1.0.0-py3-none-any.whl") is not None

            # Push new Galaxy config
            resp = client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": "newhub", "url": "https://new.example.com"}]},
            )
            assert resp.status_code == 200

            # Verify cache is cleared
            fresh_cache = ProxyCache(cache_dir=cache_dir)
            assert fresh_cache.get_wheel("ansible_collection_ansible_posix-1.0.0-py3-none-any.whl") is None


class TestConvertTarballs:
    """Tests for POST /convert-tarballs endpoint."""

    def test_convert_valid_tarballs(self, tmp_path: Path) -> None:
        """Converts tarballs in a directory and returns results.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        (tarball_dir / "ansible-posix-1.5.4.tar.gz").write_bytes(b"fake-tarball")

        whl_data = b"PK\x03\x04fake-wheel"
        with (
            TestClient(application) as client,
            patch(
                "galaxy_proxy.proxy.server.tarball_to_wheel",
                return_value=("ansible_collection_ansible_posix-1.5.4-py3-none-any.whl", whl_data),
            ),
        ):
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(tarball_dir)})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["converted"]) == 1
        assert data["failed"] == []

    def test_convert_nonexistent_dir(self, tmp_path: Path) -> None:
        """Nonexistent directory returns 400.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)
        with TestClient(application) as client:
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(tmp_path / "nope")})
        assert resp.status_code == 400

    def test_convert_rejects_path_outside_allowed_roots(self, tmp_path: Path) -> None:
        """Paths outside allowed roots (system tempdir, /sessions) are rejected with 400.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)
        disallowed = tmp_path / "evil"
        disallowed.mkdir()

        with (
            TestClient(application) as client,
            patch("tempfile.gettempdir", return_value=str(tmp_path / "fake-tmp")),
        ):
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(disallowed)})

        assert resp.status_code == 400
        assert "session or temp directory" in resp.json()["detail"]

    def test_convert_rejects_symlink_outside_allowed(self, tmp_path: Path) -> None:
        """A symlink under the allowed root that resolves outside is rejected.

        The symlink is placed inside the allowed temp root but points to a
        directory outside it, verifying that resolve-first validation catches
        the escape.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        allowed_root = tmp_path / "allowed"
        allowed_root.mkdir()
        escape_target = tmp_path / "escaped"
        escape_target.mkdir()

        symlink = allowed_root / "link"
        symlink.symlink_to(escape_target)

        with (
            TestClient(application) as client,
            patch("tempfile.gettempdir", return_value=str(allowed_root)),
        ):
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(symlink)})

        assert resp.status_code == 400
        assert "session or temp directory" in resp.json()["detail"]

    def test_convert_accepts_symlink_within_allowed_root(self, tmp_path: Path) -> None:
        """A symlink resolving to a directory under an allowed root is accepted.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        symlink = tmp_path / "link"
        symlink.symlink_to(real_dir)

        with (
            TestClient(application) as client,
            patch("tempfile.gettempdir", return_value=str(tmp_path)),
        ):
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(symlink)})

        assert resp.status_code == 200
        assert resp.json() == {"converted": [], "failed": []}

    def test_convert_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """Paths using ``..`` to escape allowed roots are rejected.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        traversal = str(tmp_path / "subdir" / ".." / ".." / "etc")
        with (
            TestClient(application) as client,
            patch("tempfile.gettempdir", return_value=str(tmp_path / "fake-tmp")),
        ):
            resp = client.post("/convert-tarballs", params={"tarball_dir": traversal})

        assert resp.status_code == 400

    def test_convert_rejects_symlink_parent_outside_allowed(self, tmp_path: Path) -> None:
        """A symlink parent component that escapes the allowed root is rejected.

        The symlink is placed inside the allowed root as a parent directory
        component, but resolves to a path outside it. Verifies the
        resolve-first pattern catches traversal via symlinked parents.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir)

        allowed_root = tmp_path / "allowed"
        allowed_root.mkdir()
        escape_target = tmp_path / "escaped"
        escape_target.mkdir()
        child = escape_target / "child"
        child.mkdir()

        link_parent = allowed_root / "link_parent"
        link_parent.symlink_to(escape_target)

        with (
            TestClient(application) as client,
            patch("tempfile.gettempdir", return_value=str(allowed_root)),
        ):
            resp = client.post(
                "/convert-tarballs",
                params={"tarball_dir": str(link_parent / "child")},
            )

        assert resp.status_code == 400
        assert "session or temp directory" in resp.json()["detail"]


class TestGalaxyClientDownload:
    """download_tarball and get_version_and_download wrap failures as RuntimeError."""

    def test_download_tarball_success(self) -> None:
        """A 200 response returns the raw tarball bytes."""
        import asyncio

        from galaxy_proxy.galaxy_client import GalaxyClient, GalaxyServer

        client = GalaxyClient(servers=[GalaxyServer(url="https://galaxy.example.com")])
        try:
            mock_resp = unittest.mock.MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.content = b"tarball-bytes"
            with patch.object(
                client._download_client,
                "get",
                AsyncMock(return_value=mock_resp),
            ):
                data = asyncio.run(client.download_tarball("https://cdn.example.com/coll.tar.gz"))
        finally:
            asyncio.run(client.close())

        assert data == b"tarball-bytes"

    @pytest.mark.parametrize("error_kind", ["transport", "status"])  # type: ignore[untyped-decorator]
    def test_download_tarball_wraps_httpx_errors(self, error_kind: str) -> None:
        """Transport and status failures surface as RuntimeError with cause.

        Args:
            error_kind: Which httpx failure the fake transport raises.
        """
        import asyncio

        import httpx

        from galaxy_proxy.galaxy_client import GalaxyClient, GalaxyServer

        url = "https://cdn.example.com/coll.tar.gz"
        request = httpx.Request("GET", url)
        error: Exception
        if error_kind == "transport":
            error = httpx.ConnectError("boom", request=request)
        else:
            error = httpx.HTTPStatusError(
                "server error",
                request=request,
                response=httpx.Response(500, request=request),
            )
        client = GalaxyClient(servers=[GalaxyServer(url="https://galaxy.example.com")])
        try:
            with (
                patch.object(
                    client._download_client,
                    "get",
                    AsyncMock(side_effect=error),
                ),
                pytest.raises(RuntimeError, match="tarball download failed") as excinfo,
            ):
                asyncio.run(client.download_tarball(url))
        finally:
            asyncio.run(client.close())

        assert excinfo.value.__cause__ is error

    def test_get_version_and_download_success(self) -> None:
        """Metadata and tarball are fetched in sequence and returned together."""
        import asyncio

        from galaxy_proxy.galaxy_client import CollectionVersion, GalaxyClient, GalaxyServer

        detail = CollectionVersion(
            namespace="ansible",
            name="posix",
            version="1.0.0",
            download_url="https://cdn.example.com/coll.tar.gz",
        )
        client = GalaxyClient(servers=[GalaxyServer(url="https://galaxy.example.com")])
        try:
            with (
                patch.object(
                    GalaxyClient,
                    "get_version_detail",
                    AsyncMock(return_value=detail),
                ),
                patch.object(
                    GalaxyClient,
                    "download_tarball",
                    AsyncMock(return_value=b"tarball-bytes"),
                ) as mock_download,
            ):
                result = asyncio.run(client.get_version_and_download("ansible", "posix", "1.0.0"))
        finally:
            asyncio.run(client.close())

        assert result == (detail, b"tarball-bytes")
        mock_download.assert_awaited_once_with("https://cdn.example.com/coll.tar.gz")

    def test_get_version_and_download_wraps_raw_errors(self) -> None:
        """Raw payload failures surface as RuntimeError with cause."""
        import asyncio

        from galaxy_proxy.galaxy_client import GalaxyClient, GalaxyServer

        raw = ValueError("bad json")
        client = GalaxyClient(servers=[GalaxyServer(url="https://galaxy.example.com")])
        try:
            with (
                patch.object(
                    GalaxyClient,
                    "get_version_detail",
                    AsyncMock(side_effect=raw),
                ),
                pytest.raises(RuntimeError, match="download failed") as excinfo,
            ):
                asyncio.run(client.get_version_and_download("ansible", "posix", "1.0.0"))
        finally:
            asyncio.run(client.close())

        assert excinfo.value.__cause__ is raw

    def test_get_version_and_download_propagates_wrapped_errors(self) -> None:
        """Already-wrapped RuntimeError failures propagate unchanged."""
        import asyncio

        from galaxy_proxy.galaxy_client import GalaxyClient, GalaxyServer

        wrapped = RuntimeError("Galaxy version detail failed for ansible.posix:1.0.0: boom")
        client = GalaxyClient(servers=[GalaxyServer(url="https://galaxy.example.com")])
        try:
            with (
                patch.object(
                    GalaxyClient,
                    "get_version_detail",
                    AsyncMock(side_effect=wrapped),
                ),
                pytest.raises(RuntimeError, match="version detail failed") as excinfo,
            ):
                asyncio.run(client.get_version_and_download("ansible", "posix", "1.0.0"))
        finally:
            asyncio.run(client.close())

        assert excinfo.value is wrapped


class TestDownloadFailureMessages:
    """Download failures identify the requested collection and source."""

    def test_serve_wheel_download_failure_includes_server_labels(self, tmp_path: Path) -> None:
        """serve_wheel download failure includes configured server labels in 502.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import DownloadResult, GalaxyServerConfig

        cache_dir = tmp_path / "cache"
        application = create_app(
            cache_dir=cache_dir,
            galaxy_servers=[
                GalaxyServerConfig(name="hub", url="https://hub.example.com"),
            ],
        )

        mock_download = AsyncMock(
            return_value=DownloadResult(
                failed_specs=["ansible.posix:1.5.4"],
                stderr="Galaxy server unreachable",
            ),
        )

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        ):
            resp = client.get("/wheels/ansible_collection_ansible_posix-1.5.4-py3-none-any.whl")

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "ansible.posix" in detail
        assert detail.endswith("(servers tried: hub.example.com)")

    def test_serve_wheel_generic_failure_includes_server_labels(self, tmp_path: Path) -> None:
        """serve_wheel generic failure includes server labels in 502 detail.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig

        cache_dir = tmp_path / "cache"
        application = create_app(
            cache_dir=cache_dir,
            galaxy_servers=[
                GalaxyServerConfig(name="hub", url="https://hub.example.com"),
            ],
        )

        mock_download = AsyncMock(side_effect=RuntimeError("network timeout"))

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        ):
            resp = client.get("/wheels/ansible_collection_ansible_posix-1.5.4-py3-none-any.whl")

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "ansible.posix" in detail
        assert detail.endswith("(servers tried: hub.example.com)")

    def test_on_demand_download_failure_returns_502(self, tmp_path: Path) -> None:
        """On-demand download failure returns 502, not empty listing.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig

        cache_dir = tmp_path / "cache"
        application = create_app(
            cache_dir=cache_dir,
            enable_passthrough=False,
            galaxy_servers=[
                GalaxyServerConfig(name="hub", url="https://hub.example.com"),
            ],
        )

        mock_download = AsyncMock(
            side_effect=RuntimeError("Galaxy server unreachable"),
        )

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 502
        assert "ansible.posix" in resp.json()["detail"]


class TestCollectionDependencyFailsScans:
    """Gate 3: Scan aborts when required collections cannot be installed."""

    def test_scan_aborts_on_failed_collections(self) -> None:
        """CollectionDependencyError is raised when collections fail to install."""
        from apme_engine.daemon.engine_server import CollectionDependencyError

        err = CollectionDependencyError(["community.general", "ansible.posix:>=1.5"])
        assert "community.general" in str(err)
        assert "ansible.posix:>=1.5" in str(err)
        assert "2 required collection(s)" in str(err)
        assert "Scan aborted" in str(err)
        assert err.failed_collections == ["community.general", "ansible.posix:>=1.5"]

    def test_single_collection_failure_message(self) -> None:
        """Single failed collection produces correct error message."""
        from apme_engine.daemon.engine_server import CollectionDependencyError

        err = CollectionDependencyError(["ansible.netcommon"])
        assert "1 required collection(s)" in str(err)
        assert "ansible.netcommon" in str(err)
