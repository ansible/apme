"""Tests for Galaxy Proxy admin-token auth and gateway forwarding parity.

Covers findings #6 (open-with-warning default when the token is unset),
#7 (``/convert-tarballs`` is operator-only: no in-repo production HTTP
caller, so rollout order is proxy-first then gateway), and #30 (header and
env literals must match across the proxy and gateway services).

Deterministic: ``TestClient`` with monkeypatched env, stubbed httpx/DB —
no network.
"""

from __future__ import annotations

import hmac
import tempfile
from pathlib import Path
from types import TracebackType

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from galaxy_proxy.proxy import server as proxy_server
from galaxy_proxy.proxy.server import create_app

_TEST_TOKEN = "s3cret-proxy-token"

_ADMIN_ENDPOINTS: list[str] = ["/admin/galaxy-config", "/convert-tarballs"]

REPO_ROOT = Path(__file__).resolve().parents[1]


def _post_admin(
    client: TestClient,
    endpoint: str,
    tarball_dir: Path,
    headers: dict[str, str] | None,
) -> Response:
    """Post to a proxy admin endpoint with the body each endpoint expects.

    Args:
        client: Test client bound to the proxy app.
        endpoint: Admin path under test.
        tarball_dir: Directory advertised to ``/convert-tarballs``.
        headers: Optional request headers (the auth token under test).

    Returns:
        The HTTP response.
    """
    if endpoint == "/admin/galaxy-config":
        return client.post(endpoint, json={"servers": []}, headers=headers)
    return client.post(endpoint, params={"tarball_dir": str(tarball_dir)}, headers=headers)


def _files_containing(root: Path, needle: str) -> list[str]:
    """List repo-relative ``*.py`` files under root containing a substring.

    Args:
        root: Directory to search recursively.
        needle: Literal substring to look for.

    Returns:
        Sorted list of repo-relative path strings.
    """
    return sorted(
        str(path.relative_to(REPO_ROOT)) for path in root.rglob("*.py") if needle in path.read_text(encoding="utf-8")
    )


def _install_push_stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the gateway DB layer and httpx client for ``push_galaxy_config``.

    Stubs at the narrowest seam that still proves the header reaches the
    wire: the ``httpx.AsyncClient`` used for the POST, plus the DB helpers
    the push reads servers from.

    Args:
        monkeypatch: Pytest fixture for patching.

    Returns:
        Capture dict populated with the outgoing POST url and kwargs.
    """
    import httpx

    import apme_gateway.db as gateway_db
    from apme_gateway.db import queries as gateway_queries

    posted: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            return False

        async def post(self, url: str, **kwargs: object) -> _FakeResponse:
            posted["url"] = url
            posted["kwargs"] = kwargs
            return _FakeResponse()

    class _FakeServer:
        def __init__(self) -> None:
            self.name = "hub"
            self.url = "https://hub.example.com"
            self.token = "secret"
            self.auth_url = ""

    class _FakeSession:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            return None

    async def _fake_list_servers(db: object) -> list[_FakeServer]:
        return [_FakeServer()]

    def _fake_get_session() -> _FakeSession:
        return _FakeSession()

    monkeypatch.setattr(gateway_db, "get_session", _fake_get_session)
    monkeypatch.setattr(gateway_queries, "list_galaxy_servers", _fake_list_servers)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return posted


class TestUnsetTokenOpenAdmin:
    """Without a configured token the admin surface stays open (finding #6)."""

    def test_galaxy_config_open_without_token(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Unset token leaves ``/admin/galaxy-config`` unprotected.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
        """
        monkeypatch.delenv(proxy_server._ADMIN_TOKEN_ENV, raising=False)
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        with TestClient(app) as client:
            resp = client.post("/admin/galaxy-config", json={"servers": []})
        assert resp.status_code == 200
        assert resp.json() == {"accepted": 0, "servers": []}

    def test_convert_tarballs_open_without_token(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Unset token leaves ``/convert-tarballs`` unprotected.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
        """
        monkeypatch.delenv(proxy_server._ADMIN_TOKEN_ENV, raising=False)
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        with TestClient(app) as client:
            resp = client.post("/convert-tarballs", params={"tarball_dir": str(tarball_dir)})
        assert resp.status_code == 200
        assert resp.json() == {"converted": [], "failed": []}

    def test_blank_token_counts_as_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A whitespace-only token is stripped to empty, so the surface stays open.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
        """
        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, "   ")
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        with TestClient(app) as client:
            resp = client.post("/admin/galaxy-config", json={"servers": []})
        assert resp.status_code == 200


class TestSetTokenEnforcement:
    """A configured token locks both admin endpoints until the exact header arrives."""

    @pytest.mark.parametrize("endpoint", _ADMIN_ENDPOINTS)  # type: ignore[untyped-decorator]
    def test_exact_header_allowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, endpoint: str) -> None:
        """The exact header value is accepted on both admin endpoints.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
            endpoint: Admin path under test.
        """
        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, _TEST_TOKEN)
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        headers = {proxy_server._ADMIN_TOKEN_HEADER: _TEST_TOKEN}
        with TestClient(app) as client:
            resp = _post_admin(client, endpoint, tarball_dir, headers)
        assert resp.status_code == 200

    @pytest.mark.parametrize("endpoint", _ADMIN_ENDPOINTS)  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize(
        "presented",
        [
            pytest.param(None, id="missing"),
            pytest.param("wrong-token", id="wrong"),
            pytest.param(_TEST_TOKEN + " ", id="extra-space"),
        ],
    )  # type: ignore[untyped-decorator]
    def test_bad_header_forbidden(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        endpoint: str,
        presented: str | None,
    ) -> None:
        """Missing, wrong, and padded tokens are all rejected with 403.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
            endpoint: Admin path under test.
            presented: Token value sent in the header (None sends no header).
        """
        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, _TEST_TOKEN)
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        headers = None if presented is None else {proxy_server._ADMIN_TOKEN_HEADER: presented}
        with TestClient(app) as client:
            resp = _post_admin(client, endpoint, tarball_dir, headers)
        assert resp.status_code == 403


class TestAdminTokenComparison:
    """Token comparison rejects near-misses without leaking an oracle."""

    def test_wrong_length_and_wrong_content_share_one_forbidden_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Short and same-length wrong tokens get identical 403 responses.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
        """
        token = "s3cret-token"
        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, token)
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        header = proxy_server._ADMIN_TOKEN_HEADER
        with TestClient(app) as client:
            short = client.post(
                "/admin/galaxy-config",
                json={"servers": []},
                headers={header: "short"},
            )
            same_length = client.post(
                "/admin/galaxy-config",
                json={"servers": []},
                headers={header: "X3cret-token"},
            )
        assert short.status_code == 403
        assert same_length.status_code == 403
        assert short.json() == same_length.json()
        assert token not in short.text
        assert token not in same_length.text

    def test_rejection_goes_through_constant_time_compare(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Admin auth delegates to ``hmac.compare_digest`` with the configured token.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
            tmp_path: Pytest-provided temporary directory.
        """
        real_compare = hmac.compare_digest
        calls: list[tuple[str, str]] = []

        def _spy(provided: str, expected: str) -> bool:
            calls.append((provided, expected))
            return bool(real_compare(provided, expected))

        monkeypatch.setattr(hmac, "compare_digest", _spy)
        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, _TEST_TOKEN)
        app = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
        with TestClient(app) as client:
            resp = client.post(
                "/admin/galaxy-config",
                json={"servers": []},
                headers={proxy_server._ADMIN_TOKEN_HEADER: "wrong-token"},
            )
        assert resp.status_code == 403
        assert calls == [("wrong-token", _TEST_TOKEN)]


class TestGatewayAdminTokenHeaders:
    """Gateway forwards the proxy admin token only when configured."""

    def test_set_token_yields_stripped_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A set token is stripped and sent under the proxy header name.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        from apme_gateway import _galaxy_proxy_sync as sync

        monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "  push-token  ")
        assert sync._admin_token_headers() == {sync._PROXY_ADMIN_TOKEN_HEADER: "push-token"}

    def test_unset_token_yields_no_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset token produces no auth header (open proxy default).

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        from apme_gateway import _galaxy_proxy_sync as sync

        monkeypatch.delenv("APME_PROXY_ADMIN_TOKEN", raising=False)
        assert sync._admin_token_headers() == {}

    def test_blank_token_yields_no_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A whitespace-only token is stripped to empty and produces no header.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        from apme_gateway import _galaxy_proxy_sync as sync

        monkeypatch.setenv("APME_PROXY_ADMIN_TOKEN", "   ")
        assert sync._admin_token_headers() == {}


class TestPushGalaxyConfigForwardsToken:
    """``push_galaxy_config`` carries the admin token to the proxy wire."""

    async def test_push_sends_admin_token_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The config push POSTs the token header to ``/admin/galaxy-config``.

        Args:
            monkeypatch: Pytest fixture for patching.
        """
        from apme_gateway import _galaxy_proxy_sync as sync

        monkeypatch.setenv(proxy_server._ADMIN_TOKEN_ENV, "push-token")
        monkeypatch.delenv("APME_GALAXY_PROXY_URL", raising=False)
        posted = _install_push_stubs(monkeypatch)

        assert await sync.push_galaxy_config() is True
        assert posted["url"] == "http://127.0.0.1:8765/admin/galaxy-config"
        raw_kwargs = posted["kwargs"]
        assert isinstance(raw_kwargs, dict)
        assert raw_kwargs["headers"] == {sync._PROXY_ADMIN_TOKEN_HEADER: "push-token"}
        assert raw_kwargs["json"] == {
            "servers": [
                {
                    "name": "hub",
                    "url": "https://hub.example.com",
                    "token": "secret",
                    "auth_url": "",
                },
            ]
        }

    async def test_push_without_token_sends_no_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a token the push still succeeds, headerless (open proxy).

        Args:
            monkeypatch: Pytest fixture for patching.
        """
        from apme_gateway import _galaxy_proxy_sync as sync

        monkeypatch.delenv(proxy_server._ADMIN_TOKEN_ENV, raising=False)
        monkeypatch.delenv("APME_GALAXY_PROXY_URL", raising=False)
        posted = _install_push_stubs(monkeypatch)

        assert await sync.push_galaxy_config() is True
        raw_kwargs = posted["kwargs"]
        assert isinstance(raw_kwargs, dict)
        assert raw_kwargs["headers"] == {}


class TestProxyGatewayAuthContract:
    """Header and env literals must match across proxy and gateway (finding #30)."""

    def test_header_literal_shared(self) -> None:
        """Both services name the same admin token header on the wire."""
        from apme_gateway import _galaxy_proxy_sync as sync

        assert proxy_server._ADMIN_TOKEN_HEADER == sync._PROXY_ADMIN_TOKEN_HEADER
        assert proxy_server._ADMIN_TOKEN_HEADER == "x-apme-proxy-token"

    def test_env_literal_shared(self) -> None:
        """Both services read the token from the same environment variable."""
        from apme_gateway import _galaxy_proxy_sync as sync

        assert proxy_server._ADMIN_TOKEN_ENV == sync._PROXY_ADMIN_TOKEN_ENV
        assert proxy_server._ADMIN_TOKEN_ENV == "APME_PROXY_ADMIN_TOKEN"


class TestConvertTarballsRollout:
    """``/convert-tarballs`` stays operator-only with proxy-first rollout (finding #7)."""

    def test_only_proxy_server_references_convert_tarballs_path(self) -> None:
        """The ``/convert-tarballs`` HTTP path is defined only by the proxy server."""
        assert _files_containing(REPO_ROOT / "src", "/convert-tarballs") == [
            "src/galaxy_proxy/proxy/server.py",
        ]

    def test_gateway_never_posts_to_convert_tarballs(self) -> None:
        """The gateway has no ``convert-tarballs`` caller; pushes target galaxy-config."""
        assert _files_containing(REPO_ROOT / "src" / "apme_gateway", "convert-tarballs") == []

    def test_engine_has_no_convert_tarballs_client(self) -> None:
        """The engine has no ``convert-tarballs`` HTTP client (operator-only endpoint)."""
        assert _files_containing(REPO_ROOT / "src" / "apme_engine", "convert-tarballs") == []

    def test_gateway_push_targets_galaxy_config_only(self) -> None:
        """The gateway sync module pushes ``/admin/galaxy-config`` and nothing else admin."""
        text = (REPO_ROOT / "src" / "apme_gateway" / "_galaxy_proxy_sync.py").read_text(encoding="utf-8")
        assert "/admin/galaxy-config" in text
        assert "convert-tarballs" not in text
