"""Tests for galaxy_proxy.proxy.server (PEP 503 API with ansible-galaxy download)."""

from __future__ import annotations

import unittest.mock
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from galaxy_proxy.proxy.server import _safe_server_label, create_app


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
        mock_versions = AsyncMock(return_value=[])
        whl_data = b"PK\x03\x04converted-wheel"
        whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
            patch(
                "galaxy_proxy.proxy.server.tarball_to_wheel",
                return_value=(whl_name, whl_data),
            ),
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_versions),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 200
        assert whl_name in resp.text
        assert "/wheels/" in resp.text

    def test_collection_no_cached_wheels_download_fails_empty(self, tmp_path: Path) -> None:
        """When on-demand download fails, project page returns empty listing.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        mock_download = AsyncMock(side_effect=RuntimeError("Galaxy unreachable"))
        mock_versions = AsyncMock(return_value=[])

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_versions),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 200
        assert "<a href" not in resp.text

    def test_empty_versions_are_not_cached(self, tmp_path: Path) -> None:
        """Empty version results are retried instead of being cached as failures.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False, metadata_ttl=600.0)

        mock_download = AsyncMock(side_effect=RuntimeError("Galaxy unreachable"))
        mock_versions = AsyncMock(return_value=[])

        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server.download_collections", mock_download),
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_versions),
        ):
            resp1 = client.get("/simple/ansible-collection-ansible-posix/")
            assert resp1.status_code == 200

            resp2 = client.get("/simple/ansible-collection-ansible-posix/")
            assert resp2.status_code == 200

        assert mock_versions.call_count == 2

    def test_collection_with_cached_wheel(self, tmp_path: Path) -> None:
        """Collection with cached wheel lists it in the project page.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        wheels_dir = cache_dir / "wheels"
        wheels_dir.mkdir(parents=True)
        whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
        (wheels_dir / whl_name).write_bytes(b"fake-wheel")

        mock_versions = AsyncMock(return_value=[])
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)
        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_versions),
        ):
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
        (wheels_dir / whl_name).write_bytes(whl_data)

        application = create_app(cache_dir=cache_dir)
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
        """Download failure returns 502.

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
        assert "Galaxy server unreachable" not in resp.text

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


class TestVersionDiscoveryWithServers:
    """Tests for _fetch_galaxy_versions using configured Galaxy servers."""

    def test_version_discovery_uses_configured_server(self, tmp_path: Path) -> None:
        """Version discovery queries configured servers before public Galaxy.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        with TestClient(application) as client:
            client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": "hub", "url": "https://hub.example.com", "token": "tok"}]},
            )

            mock_fetch = AsyncMock(return_value=["2.0.0", "1.0.0"])
            with patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_fetch):
                resp = client.get("/simple/ansible-collection-ansible-posix/")

            assert resp.status_code == 200
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args
            servers = call_kwargs.kwargs.get("servers") or call_kwargs[1].get("servers")
            assert servers is not None
            assert len(servers) == 1
            assert isinstance(servers[0], GalaxyServerConfig)
            assert servers[0].url == "https://hub.example.com"
            assert servers[0].token == "tok"

    def test_version_discovery_falls_back_to_public_galaxy(self, tmp_path: Path) -> None:
        """Without configured servers, version discovery falls back to public Galaxy.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        mock_fetch = AsyncMock(return_value=["1.5.4"])
        with (
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_fetch),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 200
        call_kwargs = mock_fetch.call_args
        servers = call_kwargs.kwargs.get("servers") or call_kwargs[1].get("servers")
        assert servers is None

    def test_version_discovery_uses_ansible_config_env_when_no_pushed_servers(self, tmp_path: Path) -> None:
        """Version discovery reads Galaxy servers from ``ANSIBLE_CONFIG`` in daemon mode.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig

        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(
            "[galaxy]\n"
            "server_list = certified\n"
            "\n"
            "[galaxy_server.certified]\n"
            "url = https://hub.example.com/api/galaxy/\n"
            "token = secret-token\n"
            "auth_url = https://sso.example.com/token\n"
        )
        application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)

        mock_fetch = AsyncMock(return_value=["1.5.4"])
        with (
            patch.dict("os.environ", {"ANSIBLE_CONFIG": str(cfg)}),
            TestClient(application) as client,
            patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_fetch),
        ):
            resp = client.get("/simple/ansible-collection-ansible-posix/")

        assert resp.status_code == 200
        call_kwargs = mock_fetch.call_args
        servers = call_kwargs.kwargs.get("servers") or call_kwargs[1].get("servers")
        assert servers is not None
        assert len(servers) == 1
        assert isinstance(servers[0], GalaxyServerConfig)
        assert servers[0].name == "certified"
        assert servers[0].url == "https://hub.example.com/api/galaxy/"
        assert servers[0].token == "secret-token"
        assert servers[0].auth_url == "https://sso.example.com/token"

    def test_fetch_versions_tries_servers_in_order(self) -> None:
        """_fetch_galaxy_versions tries configured servers before public Galaxy.

        Verifies that the first successful server short-circuits further attempts.
        """
        import asyncio

        from galaxy_proxy.collection_downloader import GalaxyServerConfig
        from galaxy_proxy.proxy.server import _fetch_galaxy_versions

        servers = [
            GalaxyServerConfig(name="hub", url="https://hub.example.com", token="tok"),
        ]

        with patch(
            "galaxy_proxy.proxy.server._fetch_versions_from",
            new_callable=AsyncMock,
            return_value=["3.0.0", "2.0.0"],
        ) as mock_inner:
            result = asyncio.run(
                _fetch_galaxy_versions("ansible", "posix", servers=servers),
            )

        assert result == ["2.0.0", "3.0.0"]
        mock_inner.assert_called_once_with(
            "ansible",
            "posix",
            "https://hub.example.com",
            token="tok",
        )

    def test_fetch_versions_falls_through_on_failure(self) -> None:
        """When a configured server fails, _fetch_galaxy_versions tries the next.

        Private server returns None (failure), public Galaxy returns versions.
        """
        import asyncio

        from galaxy_proxy.collection_downloader import GalaxyServerConfig
        from galaxy_proxy.proxy.server import _fetch_galaxy_versions

        servers = [
            GalaxyServerConfig(name="hub", url="https://hub.example.com", token="tok"),
        ]

        async def _side_effect(
            ns: str,
            name: str,
            base_url: str,
            *,
            token: str | None = None,
        ) -> list[str] | None:
            if base_url == "https://hub.example.com":
                return None
            return ["1.5.4"]

        with patch(
            "galaxy_proxy.proxy.server._fetch_versions_from",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ) as mock_inner:
            result = asyncio.run(
                _fetch_galaxy_versions("ansible", "posix", servers=servers),
            )

        assert result == ["1.5.4"]
        assert mock_inner.call_count == 2

    def test_fetch_versions_sends_auth_token(self) -> None:
        """_fetch_versions_from passes the auth token in the Authorization header.

        Verifies the httpx client is configured with the token.
        """
        import asyncio

        from galaxy_proxy.proxy.server import _fetch_versions_from

        captured_headers: dict[str, str] = {}

        def _capture_client(**kwargs: object) -> unittest.mock.MagicMock:
            hdrs = kwargs.get("headers")
            if isinstance(hdrs, dict):
                captured_headers.update(hdrs)
            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"version": "1.0.0"}], "links": {}}
            mock_resp.raise_for_status.return_value = None

            client = unittest.mock.MagicMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            return client

        with patch("galaxy_proxy.proxy.server.httpx.AsyncClient", side_effect=_capture_client):
            result = asyncio.run(
                _fetch_versions_from("ansible", "posix", "https://hub.example.com", token="secret"),
            )

        assert result == ["1.0.0"]
        assert captured_headers["Authorization"] == "Token secret"

    def test_fetch_versions_no_token_no_auth_header(self) -> None:
        """_fetch_versions_from omits Authorization header when no token is provided."""
        import asyncio

        from galaxy_proxy.proxy.server import _fetch_versions_from

        captured_headers: dict[str, str] = {}

        def _capture_client(**kwargs: object) -> unittest.mock.MagicMock:
            hdrs = kwargs.get("headers")
            if isinstance(hdrs, dict):
                captured_headers.update(hdrs)
            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"version": "2.0.0"}], "links": {}}
            mock_resp.raise_for_status.return_value = None

            client = unittest.mock.MagicMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            return client

        with patch("galaxy_proxy.proxy.server.httpx.AsyncClient", side_effect=_capture_client):
            result = asyncio.run(
                _fetch_versions_from("ansible", "posix", "https://galaxy.ansible.com"),
            )

        assert result == ["2.0.0"]
        assert "Authorization" not in captured_headers

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("raw_url", "expected"),
        [
            ("https://galaxy.ansible.com", "https://galaxy.ansible.com"),
            ("https://galaxy.ansible.com/", "https://galaxy.ansible.com"),
            ("https://galaxy.ansible.com/api/", "https://galaxy.ansible.com"),
            ("https://hub.example.com/api/galaxy/", "https://hub.example.com"),
            (
                "https://hub.example.com/api/galaxy/content/published/",
                "https://hub.example.com",
            ),
            ("https://console.redhat.com/api/automation-hub", "https://console.redhat.com"),
            ("https://api.example.com", "https://api.example.com"),
            ("https://api.example.com/", "https://api.example.com"),
            ("https://api.galaxy.example.com/api/", "https://api.galaxy.example.com"),
        ],
    )
    def test_normalize_galaxy_url(self, raw_url: str, expected: str) -> None:
        """_normalize_galaxy_url strips /api... suffixes from server URLs.

        Args:
            raw_url: Input URL to normalize.
            expected: Expected normalized URL.
        """
        from galaxy_proxy.proxy.server import _normalize_galaxy_url

        assert _normalize_galaxy_url(raw_url) == expected

    def test_fetch_versions_from_normalizes_api_url(self) -> None:
        """_fetch_versions_from normalizes URLs that include /api/ to avoid /api/api/v3/...."""
        import asyncio

        from galaxy_proxy.proxy.server import _fetch_versions_from

        captured_urls: list[str] = []

        def _capture_client(**kwargs: object) -> unittest.mock.MagicMock:
            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"version": "1.0.0"}], "links": {}}
            mock_resp.raise_for_status.return_value = None

            async def _get(url: str, **kw: object) -> unittest.mock.MagicMock:
                captured_urls.append(url)
                return mock_resp

            client = unittest.mock.MagicMock()
            client.get = _get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            return client

        with patch("galaxy_proxy.proxy.server.httpx.AsyncClient", side_effect=_capture_client):
            result = asyncio.run(
                _fetch_versions_from(
                    "ansible",
                    "posix",
                    "https://hub.example.com/api/",
                    token="tok",
                ),
            )

        assert result == ["1.0.0"]
        assert len(captured_urls) == 1
        assert "/api/api/" not in captured_urls[0]
        assert "/api/v3/plugin/ansible/" in captured_urls[0]

    def test_galaxy_versions_index_path_aap_and_hub_urls(self) -> None:
        """AAP mock and Automation Hub URLs select the /api/galaxy/v3 index path."""
        from galaxy_proxy.proxy.server import (
            _GALAXY_VERSIONS_PATH,
            _GALAXY_VERSIONS_PATH_AAP,
            _galaxy_versions_index_path,
        )

        assert (
            _galaxy_versions_index_path(
                "http://host.containers.internal:8099/api/galaxy/content/community/",
            )
            == _GALAXY_VERSIONS_PATH_AAP
        )
        assert (
            _galaxy_versions_index_path("https://console.redhat.com/api/automation-hub/") == _GALAXY_VERSIONS_PATH_AAP
        )
        assert _galaxy_versions_index_path("https://galaxy.ansible.com") == _GALAXY_VERSIONS_PATH

    def test_fetch_versions_from_uses_api_galaxy_prefix_for_aap_mock(self) -> None:
        """PAH/aap-mock URLs must use /api/galaxy/v3/... not bare /api/v3/...."""
        import asyncio

        from galaxy_proxy.proxy.server import _fetch_versions_from

        captured_urls: list[str] = []

        def _capture_client(**kwargs: object) -> unittest.mock.MagicMock:
            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"version": "1.0.0"}], "links": {}}
            mock_resp.raise_for_status.return_value = None

            async def _get(url: str, **kw: object) -> unittest.mock.MagicMock:
                captured_urls.append(url)
                return mock_resp

            client = unittest.mock.MagicMock()
            client.get = _get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            return client

        with patch("galaxy_proxy.proxy.server.httpx.AsyncClient", side_effect=_capture_client):
            result = asyncio.run(
                _fetch_versions_from(
                    "ansible",
                    "utils",
                    "http://host.containers.internal:8099/api/galaxy/content/community/",
                    token="tok",
                ),
            )

        assert result == ["1.0.0"]
        assert len(captured_urls) == 1
        assert "/api/galaxy/v3/plugin/ansible/" in captured_urls[0]
        assert "/api/v3/plugin/ansible/" not in captured_urls[0]

    def test_version_discovery_with_api_url_through_project_page(self, tmp_path: Path) -> None:
        """Version discovery handles configured server URLs that include /api/.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from galaxy_proxy.collection_downloader import GalaxyServerConfig

        cache_dir = tmp_path / "cache"
        application = create_app(cache_dir=cache_dir, enable_passthrough=False)

        with TestClient(application) as client:
            client.post(
                "/admin/galaxy-config",
                json={"servers": [{"name": "hub", "url": "https://hub.example.com/api/", "token": "tok"}]},
            )

            mock_fetch = AsyncMock(return_value=["2.0.0", "1.0.0"])
            with patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", mock_fetch):
                resp = client.get("/simple/ansible-collection-ansible-posix/")

            assert resp.status_code == 200
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args
            servers = call_kwargs.kwargs.get("servers") or call_kwargs[1].get("servers")
            assert servers is not None
            assert len(servers) == 1
            assert isinstance(servers[0], GalaxyServerConfig)
            assert servers[0].url == "https://hub.example.com/api/"
            assert servers[0].token == "tok"


class TestDownloadFallback:
    """Tests for download fallback to public Galaxy when configured servers fail."""

    def test_download_falls_back_to_public_galaxy_when_configured_servers_fail(self) -> None:
        """Download retries with public Galaxy when PAH servers fail.

        When portal_hub_* servers are configured but fail to download a collection,
        the download should fall back to galaxy.ansible.com if it wasn't already
        in the server list.
        """
        import asyncio

        from galaxy_proxy.collection_downloader import DownloadResult, GalaxyServerConfig
        from galaxy_proxy.proxy.server import _download_and_convert

        call_count = 0
        captured_servers: list[list[GalaxyServerConfig] | None] = []
        captured_public_defaults: list[bool] = []

        async def mock_download_collections(
            specs: list[str],
            download_dir: Path,
            *,
            ansible_cfg_path: Path | None = None,
            servers: list[GalaxyServerConfig] | None = None,
            ansible_galaxy_bin: str | None = None,
            use_public_galaxy_defaults: bool = False,
        ) -> DownloadResult:
            nonlocal call_count
            call_count += 1
            captured_servers.append(servers)
            captured_public_defaults.append(use_public_galaxy_defaults)

            if call_count == 1:
                # First call (with configured servers) fails
                return DownloadResult(failed_specs=specs)
            # Second call (fallback to public Galaxy) succeeds
            tarball = download_dir / "ansible-posix-1.0.0.tar.gz"
            tarball.write_bytes(b"fake tarball content")
            return DownloadResult(tarball_paths=[tarball])

        def mock_tarball_to_wheel(data: bytes) -> tuple[str, bytes]:
            return ("ansible_collection_ansible_posix-1.0.0-py3-none-any.whl", b"fake wheel")

        pah_servers = [
            GalaxyServerConfig(name="portal_hub_community", url="http://pah.example.com/api/galaxy/content/community/"),
            GalaxyServerConfig(name="portal_hub_validated", url="http://pah.example.com/api/galaxy/content/validated/"),
        ]

        with (
            patch("galaxy_proxy.proxy.server.download_collections", side_effect=mock_download_collections),
            patch("galaxy_proxy.proxy.server.tarball_to_wheel", side_effect=mock_tarball_to_wheel),
        ):
            whl_name, whl_data = asyncio.run(
                _download_and_convert("ansible", "posix", "1.0.0", galaxy_servers=pah_servers)
            )

        assert call_count == 2, "Should have called download twice (initial + fallback)"
        assert captured_servers[0] == pah_servers, "First call should use configured servers"
        assert captured_servers[1] is None, "Second call should use no servers (public Galaxy)"
        assert captured_public_defaults == [False, True]
        assert whl_name == "ansible_collection_ansible_posix-1.0.0-py3-none-any.whl"

    def test_download_no_fallback_when_public_galaxy_already_in_servers(self) -> None:
        """No fallback retry when galaxy.ansible.com is already in the server list.

        If the server list already includes public Galaxy and download still fails,
        there's no point retrying - just fail immediately.
        """
        import asyncio

        from galaxy_proxy.collection_downloader import DownloadResult, GalaxyServerConfig
        from galaxy_proxy.proxy.server import _download_and_convert

        call_count = 0

        async def mock_download_collections(
            specs: list[str],
            download_dir: Path,
            *,
            ansible_cfg_path: Path | None = None,
            servers: list[GalaxyServerConfig] | None = None,
            ansible_galaxy_bin: str | None = None,
            use_public_galaxy_defaults: bool = False,
        ) -> DownloadResult:
            nonlocal call_count
            call_count += 1
            return DownloadResult(failed_specs=specs)

        # Server list includes public Galaxy
        servers_with_public = [
            GalaxyServerConfig(name="portal_hub_community", url="http://pah.example.com/api/galaxy/content/community/"),
            GalaxyServerConfig(name="public_galaxy", url="https://galaxy.ansible.com/"),
        ]

        with (
            patch("galaxy_proxy.proxy.server.download_collections", side_effect=mock_download_collections),
            pytest.raises(RuntimeError, match="Failed to download"),
        ):
            asyncio.run(_download_and_convert("ansible", "posix", "1.0.0", galaxy_servers=servers_with_public))

        assert call_count == 1, "Should only call download once (no fallback when public Galaxy already tried)"

    def test_download_no_fallback_when_no_servers_configured(self) -> None:
        """No fallback when no servers are configured (already using default Galaxy).

        If no servers are configured, the download already uses ansible-galaxy's
        default discovery. No need for fallback logic.
        """
        import asyncio

        from galaxy_proxy.collection_downloader import DownloadResult, GalaxyServerConfig
        from galaxy_proxy.proxy.server import _download_and_convert

        call_count = 0

        async def mock_download_collections(
            specs: list[str],
            download_dir: Path,
            *,
            ansible_cfg_path: Path | None = None,
            servers: list[GalaxyServerConfig] | None = None,
            ansible_galaxy_bin: str | None = None,
            use_public_galaxy_defaults: bool = False,
        ) -> DownloadResult:
            nonlocal call_count
            call_count += 1
            return DownloadResult(failed_specs=specs)

        with (
            patch("galaxy_proxy.proxy.server.download_collections", side_effect=mock_download_collections),
            pytest.raises(RuntimeError, match="Failed to download"),
        ):
            asyncio.run(_download_and_convert("ansible", "posix", "1.0.0", galaxy_servers=None))

        assert call_count == 1, "Should only call download once (no fallback when no servers configured)"


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
