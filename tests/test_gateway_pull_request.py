"""Unit tests for the SCM integration feature (ADR-050)."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import PatchedFile, Project, Scan, Session
from apme_gateway.operation_registry import get_operation_registry
from apme_gateway.operation_types import OperationResult, OperationStatus
from apme_gateway.scm.base import PullRequestResult, detect_provider
from apme_gateway.scm.github import GitHubProvider, _custom_ca_bundle, _http_verify, _parse_owner_repo
from apme_gateway.scm.registry import get_provider


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB and clear operation registry per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    registry = get_operation_registry()
    await registry.shutdown()
    await close_db()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_project_with_remediation(
    *,
    project_id: str = "proj-1",
    scan_id: str = "scan-1",
    scm_token: str | None = None,
    scm_provider: str | None = None,
    add_patched_files: bool = True,
    pr_url: str | None = None,
) -> None:
    """Insert a project, session, scan, and optionally patched files.

    Args:
        project_id: Project UUID.
        scan_id: Scan UUID.
        scm_token: Per-project SCM token.
        scm_provider: Explicit provider type.
        add_patched_files: Whether to add PatchedFile rows.
        pr_url: Pre-existing PR URL on the scan.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="test-project",
                repo_url="https://github.com/org/repo.git",
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token=scm_token,
                scm_provider=scm_provider,
            )
        )
        db.add(
            Session(
                session_id="sess-1",
                project_path="/proj",
                first_seen="t0",
                last_seen="t1",
            )
        )
        db.add(
            Scan(
                scan_id=scan_id,
                session_id="sess-1",
                project_id=project_id,
                project_path="/proj",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=5,
                auto_fixable=3,
                fixed_count=3,
                pr_url=pr_url,
            )
        )
        if add_patched_files:
            db.add(
                PatchedFile(
                    scan_id=scan_id,
                    path="playbooks/main.yml",
                    content=b"---\n- hosts: all\n  tasks: []\n",
                )
            )
            db.add(
                PatchedFile(
                    scan_id=scan_id,
                    path="roles/web/tasks/main.yml",
                    content=b"---\n- name: Install nginx\n  ansible.builtin.package:\n    name: nginx\n",
                )
            )
        await db.commit()


# ── ScmProvider detection tests ──────────────────────────────────────


class TestDetectProvider:
    """Tests for detect_provider URL parsing."""

    def test_github_com(self) -> None:
        """Detect github from github.com URL."""
        assert detect_provider("https://github.com/org/repo.git") == "github"

    def test_gitlab_com(self) -> None:
        """Detect gitlab from gitlab.com URL."""
        assert detect_provider("https://gitlab.com/org/repo.git") == "gitlab"

    def test_bitbucket_org(self) -> None:
        """Detect bitbucket from bitbucket.org URL."""
        assert detect_provider("https://bitbucket.org/org/repo.git") == "bitbucket"

    def test_unknown_host(self) -> None:
        """Return None for unrecognised hosts."""
        assert detect_provider("https://selfhosted.example.com/org/repo") is None

    def test_invalid_url(self) -> None:
        """Return None for garbage input."""
        assert detect_provider("not a url") is None


class TestParseOwnerRepo:
    """Tests for GitHub URL parsing."""

    def test_standard_url(self) -> None:
        """Parse owner/repo from standard HTTPS URL."""
        owner, repo = _parse_owner_repo("https://github.com/ansible/apme.git")
        assert owner == "ansible"
        assert repo == "apme"

    def test_url_without_git_suffix(self) -> None:
        """Parse works without .git suffix."""
        owner, repo = _parse_owner_repo("https://github.com/ansible/apme")
        assert owner == "ansible"
        assert repo == "apme"

    def test_invalid_url_raises(self) -> None:
        """Raise ValueError for unparseable URL."""
        with pytest.raises(ValueError, match="Cannot extract"):
            _parse_owner_repo("https://github.com/")


# ── Registry tests ───────────────────────────────────────────────────


class TestProviderRegistry:
    """Tests for get_provider."""

    def test_github_provider(self) -> None:
        """Return GitHubProvider for 'github'."""
        provider = get_provider("github")
        assert isinstance(provider, GitHubProvider)

    def test_github_with_custom_url(self) -> None:
        """Return GitHubProvider with custom API URL for GHE."""
        provider = get_provider("github", api_base_url="https://ghe.example.com/api/v3")
        assert isinstance(provider, GitHubProvider)
        assert provider._api == "https://ghe.example.com/api/v3"  # noqa: SLF001

    def test_unsupported_provider_raises(self) -> None:
        """Raise ValueError for unknown provider type."""
        with pytest.raises(ValueError, match="Unsupported SCM provider"):
            get_provider("svn")


class TestGitHubProviderTls:
    """Tests for GitHub provider TLS configuration."""

    def test_custom_ca_bundle_prefers_ssl_cert_file(self) -> None:
        """SCM API calls use the injected CA bundle when configured."""
        with patch.dict(
            "os.environ",
            {
                "SSL_CERT_FILE": "/etc/ssl/certs/custom-ca-bundle.pem",
                "REQUESTS_CA_BUNDLE": "",
                "CURL_CA_BUNDLE": "",
            },
            clear=True,
        ):
            assert _custom_ca_bundle() == "/etc/ssl/certs/custom-ca-bundle.pem"

    def test_http_verify_builds_ssl_context_with_custom_bundle(self) -> None:
        """Custom CA configuration merges the extra bundle into TLS verification."""
        with (
            patch.dict("os.environ", {"SSL_CERT_FILE": "/etc/ssl/certs/custom-ca-bundle.pem"}, clear=True),
            patch.object(ssl.SSLContext, "load_default_certs") as mock_defaults,
            patch.object(ssl.SSLContext, "load_verify_locations") as mock_verify_locations,
        ):
            verify = _http_verify()

        assert isinstance(verify, ssl.SSLContext)
        mock_defaults.assert_called_once()
        mock_verify_locations.assert_called_once_with(cafile="/etc/ssl/certs/custom-ca-bundle.pem")

    def test_client_passes_verify_to_httpx(self) -> None:
        """Provider clients pass the resolved TLS settings to ``httpx``."""
        with (
            patch.dict("os.environ", {"SSL_CERT_FILE": "/etc/ssl/certs/custom-ca-bundle.pem"}, clear=True),
            patch.object(ssl.SSLContext, "load_default_certs"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch("apme_gateway.scm.github.httpx.AsyncClient") as mock_client,
        ):
            provider = GitHubProvider()
            provider._client(timeout=30)  # noqa: SLF001

        assert isinstance(mock_client.call_args.kwargs["verify"], ssl.SSLContext)
        assert mock_client.call_args.kwargs["timeout"] == 30


# ── DB model tests ───────────────────────────────────────────────────


class TestPatchedFileModel:
    """Tests for the PatchedFile DB model."""

    async def test_store_and_retrieve(self) -> None:
        """PatchedFile rows are persisted and retrievable."""
        from apme_gateway.db.queries import get_patched_files, store_patched_files

        async with get_session() as db:
            db.add(Session(session_id="s1", project_path="/p", first_seen="t0", last_seen="t1"))
            db.add(
                Scan(
                    scan_id="sc1",
                    session_id="s1",
                    project_path="/p",
                    created_at="2026-01-01T00:00:00Z",
                )
            )
            await db.commit()

        async with get_session() as db:
            count = await store_patched_files(
                db,
                "sc1",
                {"a.yml": b"content-a", "b.yml": b"content-b"},
            )
        assert count == 2

        async with get_session() as db:
            files = await get_patched_files(db, "sc1")
        assert len(files) == 2
        assert files[0].path == "a.yml"
        assert files[0].content == b"content-a"
        assert files[1].path == "b.yml"

    async def test_cascade_delete(self) -> None:
        """PatchedFile rows are deleted when scan is deleted."""
        from apme_gateway.db.queries import delete_scan, get_patched_files

        async with get_session() as db:
            db.add(Session(session_id="s1", project_path="/p", first_seen="t0", last_seen="t1"))
            db.add(
                Scan(
                    scan_id="sc1",
                    session_id="s1",
                    project_path="/p",
                    created_at="2026-01-01T00:00:00Z",
                )
            )
            db.add(PatchedFile(scan_id="sc1", path="a.yml", content=b"data"))
            await db.commit()

        async with get_session() as db:
            await delete_scan(db, "sc1")

        async with get_session() as db:
            files = await get_patched_files(db, "sc1")
        assert files == []


class TestScanPrUrl:
    """Tests for the pr_url field on Scan."""

    async def test_set_pr_url(self) -> None:
        """PR URL can be recorded on a scan row."""
        from apme_gateway.db.queries import set_scan_pr_url

        async with get_session() as db:
            db.add(Session(session_id="s1", project_path="/p", first_seen="t0", last_seen="t1"))
            db.add(
                Scan(
                    scan_id="sc1",
                    session_id="s1",
                    project_path="/p",
                    created_at="2026-01-01T00:00:00Z",
                )
            )
            await db.commit()

        async with get_session() as db:
            ok = await set_scan_pr_url(db, "sc1", "https://github.com/org/repo/pull/42")
        assert ok is True

        from apme_gateway.db.queries import get_scan

        async with get_session() as db:
            scan = await get_scan(db, "sc1")
        assert scan is not None
        assert scan.pr_url == "https://github.com/org/repo/pull/42"

    async def test_set_pr_url_not_found(self) -> None:
        """set_scan_pr_url returns False for missing scan."""
        from apme_gateway.db.queries import set_scan_pr_url

        async with get_session() as db:
            ok = await set_scan_pr_url(db, "nonexistent", "https://example.com")
        assert ok is False


class TestProjectScmFields:
    """Tests for SCM-related project fields (ADR-050)."""

    async def test_create_project_with_scm_fields(self) -> None:
        """Project stores scm_token and scm_provider."""
        from apme_gateway.db.queries import create_project, get_project

        async with get_session() as db:
            await create_project(
                db,
                project_id="p1",
                name="test",
                repo_url="https://github.com/o/r",
                scm_token="ghp_secret",
                scm_provider="github",
            )

        async with get_session() as db:
            proj = await get_project(db, "p1")
        assert proj is not None
        assert proj.scm_token == "ghp_secret"
        assert proj.scm_provider == "github"


# ── REST endpoint tests ──────────────────────────────────────────────


_MOCK_PR_RESULT = PullRequestResult(
    pr_url="https://github.com/org/repo/pull/99",
    branch_name="apme/remediate-scan-1",
    provider="github",
)


def _setup_completed_operation(
    *,
    project_id: str = "proj-1",
    scan_id: str = "scan-1",
    scan_type: str = "remediate",
    patches: list[dict[str, str]] | None = None,
) -> None:
    """Register a completed remediation operation in the registry.

    Args:
        project_id: Project UUID.
        scan_id: Scan UUID.
        scan_type: Operation type.
        patches: Patch diffs to include in the result.
    """
    registry = get_operation_registry()
    state = registry.create(
        operation_id=f"op-{scan_id}",
        project_id=project_id,
        scan_id=scan_id,
        scan_type=scan_type,
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)
    result = OperationResult(
        total_violations=5,
        fixable=3,
        remediated_count=3,
        patches=patches or [{"file": "playbooks/main.yml", "diff": "--- a\n+++ b"}],
    )
    registry.set_result(state.operation_id, result)


class TestSubmitEndpoint:
    """Tests for POST /api/v1/projects/{id}/operation/submit."""

    async def test_success_with_pr(self, client: AsyncClient) -> None:
        """Successful submit with create_pr=true returns PR URL.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test123")
        _setup_completed_operation()

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock()
            mock_provider.push_files = AsyncMock(return_value="abc123def")
            mock_provider.create_pull_request = AsyncMock(return_value=_MOCK_PR_RESULT)
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post("/api/v1/projects/proj-1/operation/submit")

        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_url"] == "https://github.com/org/repo/pull/99"
        assert data["commit_sha"] == "abc123def"
        assert data["provider"] == "github"
        assert data["branch_name"].startswith("apme/remediate-")

    async def test_push_only_without_pr(self, client: AsyncClient) -> None:
        """Submit with create_pr=false pushes but does not open a PR.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test123")
        _setup_completed_operation()

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock()
            mock_provider.push_files = AsyncMock(return_value="deadbeef")
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"create_pr": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_url"] is None
        assert data["commit_sha"] == "deadbeef"
        mock_provider.create_pull_request.assert_not_called()

    async def test_no_operation(self, client: AsyncClient) -> None:
        """Return 404 when no operation exists for the project.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")
        resp = await client.post("/api/v1/projects/proj-1/operation/submit")
        assert resp.status_code == 404

    async def test_operation_not_completed(self, client: AsyncClient) -> None:
        """Return 409 when operation is not in completed state.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")
        registry = get_operation_registry()
        registry.create(
            operation_id="op-running",
            project_id="proj-1",
            scan_id="scan-1",
            scan_type="remediate",
        )
        registry.transition("op-running", OperationStatus.SCANNING)

        resp = await client.post("/api/v1/projects/proj-1/operation/submit")
        assert resp.status_code == 409
        assert "not 'completed'" in resp.json()["detail"]

    async def test_check_operation_rejected(self, client: AsyncClient) -> None:
        """Return 409 when operation is a check, not remediate.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")
        _setup_completed_operation(scan_type="check")

        resp = await client.post("/api/v1/projects/proj-1/operation/submit")
        assert resp.status_code == 409
        assert "remediate" in resp.json()["detail"]

    async def test_no_scm_token(self, client: AsyncClient) -> None:
        """Return 422 when no SCM token is configured.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation()
        _setup_completed_operation()

        with patch("apme_gateway.config.load_config") as mock_cfg:
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post("/api/v1/projects/proj-1/operation/submit")

        assert resp.status_code == 422
        assert "No SCM token" in resp.json()["detail"]

    async def test_global_token_fallback(self, client: AsyncClient) -> None:
        """Use global APME_SCM_TOKEN when project has no token.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation()
        _setup_completed_operation()

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock()
            mock_provider.push_files = AsyncMock(return_value="abc123")
            mock_provider.create_pull_request = AsyncMock(return_value=_MOCK_PR_RESULT)
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = "ghp_global_token"
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post("/api/v1/projects/proj-1/operation/submit")

        assert resp.status_code == 200

    async def test_custom_branch_and_title(self, client: AsyncClient) -> None:
        """Custom branch_name and title are forwarded to the provider.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")
        _setup_completed_operation()

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock()
            mock_provider.push_files = AsyncMock(return_value="abc123")
            mock_provider.create_pull_request = AsyncMock(
                return_value=PullRequestResult(
                    pr_url="https://github.com/org/repo/pull/100",
                    branch_name="custom/branch",
                    provider="github",
                )
            )
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={
                    "branch_name": "custom/branch",
                    "title": "Custom title",
                },
            )

        assert resp.status_code == 200
        mock_provider.create_branch.assert_called_once()
        call_args = mock_provider.create_branch.call_args
        assert call_args[0][2] == "custom/branch"

    async def test_scm_provider_error_returns_502(self, client: AsyncClient) -> None:
        """Return 502 when the SCM provider raises an exception.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")
        _setup_completed_operation()

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock(side_effect=RuntimeError("API down"))
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post("/api/v1/projects/proj-1/operation/submit")

        assert resp.status_code == 502
        assert "SCM provider error" in resp.json()["detail"]

    async def test_activity_id_from_db(self, client: AsyncClient) -> None:
        """Submit with activity_id loads patched files from DB.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test123")

        with (
            patch("apme_gateway.scm.get_provider") as mock_get,
            patch("apme_gateway.config.load_config") as mock_cfg,
        ):
            mock_provider = AsyncMock()
            mock_provider.create_branch = AsyncMock()
            mock_provider.push_files = AsyncMock(return_value="db_commit_sha")
            mock_provider.create_pull_request = AsyncMock(return_value=_MOCK_PR_RESULT)
            mock_get.return_value = mock_provider
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_url"] == "https://github.com/org/repo/pull/99"
        assert data["commit_sha"] == "db_commit_sha"

    async def test_activity_id_wrong_project(self, client: AsyncClient) -> None:
        """Reject activity_id that does not belong to the project.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(scm_token="ghp_test")

        with patch("apme_gateway.config.load_config") as mock_cfg:
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/wrong-project/operation/submit",
                json={"activity_id": "scan-1"},
            )

        assert resp.status_code == 404
        assert "does not belong" in resp.json()["detail"]

    async def test_activity_id_no_patched_files(self, client: AsyncClient) -> None:
        """Reject activity_id when no patched files exist in DB.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(
            scm_token="ghp_test",
            add_patched_files=False,
        )

        with patch("apme_gateway.config.load_config") as mock_cfg:
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1"},
            )

        assert resp.status_code == 404
        assert "No patched files" in resp.json()["detail"]

    async def test_activity_id_check_scan_rejected(self, client: AsyncClient) -> None:
        """Reject activity_id that references a check scan (not remediate).

        Args:
            client: Async test client.
        """
        async with get_session() as db:
            db.add(
                Project(
                    id="proj-1",
                    name="test-project",
                    repo_url="https://github.com/org/repo.git",
                    branch="main",
                    created_at="2026-01-01T00:00:00Z",
                    scm_token="ghp_test",
                )
            )
            db.add(
                Session(
                    session_id="sess-1",
                    project_path="/proj",
                    first_seen="t0",
                    last_seen="t1",
                )
            )
            db.add(
                Scan(
                    scan_id="scan-check",
                    session_id="sess-1",
                    project_id="proj-1",
                    project_path="/proj",
                    source="gateway",
                    created_at="2026-01-01T00:00:00Z",
                    scan_type="check",
                    total_violations=5,
                    auto_fixable=3,
                )
            )
            await db.commit()

        with patch("apme_gateway.config.load_config") as mock_cfg:
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-check"},
            )

        assert resp.status_code == 409
        assert "remediate" in resp.json()["detail"]

    async def test_activity_id_pr_already_exists(self, client: AsyncClient) -> None:
        """Reject when activity already has a PR URL.

        Args:
            client: Async test client.
        """
        await _seed_project_with_remediation(
            scm_token="ghp_test",
            pr_url="https://github.com/org/repo/pull/1",
        )

        with patch("apme_gateway.config.load_config") as mock_cfg:
            mock_cfg.return_value.scm_token = ""
            mock_cfg.return_value.github_api_url = "https://api.github.com"

            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1"},
            )

        assert resp.status_code == 409
        assert "already created" in resp.json()["detail"]


class TestProjectScmApi:
    """Tests for SCM fields in project CRUD endpoints."""

    async def test_create_with_scm_fields(self, client: AsyncClient) -> None:
        """Project creation accepts and returns SCM fields.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "scm-test",
                "repo_url": "https://github.com/org/repo",
                "scm_token": "ghp_secret",
                "scm_provider": "github",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scm_provider"] == "github"
        assert data["has_scm_token"] is True

    async def test_update_scm_token(self, client: AsyncClient) -> None:
        """Project update can set/clear SCM token.

        Args:
            client: Async test client.
        """
        create_resp = await client.post(
            "/api/v1/projects",
            json={"name": "scm-update", "repo_url": "https://github.com/org/repo"},
        )
        project_id = create_resp.json()["id"]
        assert create_resp.json()["has_scm_token"] is False

        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"scm_token": "ghp_new_token"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["has_scm_token"] is True

    async def test_scm_provider_normalized(self, client: AsyncClient) -> None:
        """SCM provider is stripped and lowercased on create and update.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "norm-test",
                "repo_url": "https://github.com/org/repo",
                "scm_provider": "  GitHub  ",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["scm_provider"] == "github"

        project_id = resp.json()["id"]
        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"scm_provider": " GITHUB "},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["scm_provider"] == "github"

    async def test_scm_provider_empty_clears(self, client: AsyncClient) -> None:
        """Empty string for scm_provider clears the value.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "clear-provider",
                "repo_url": "https://github.com/org/repo",
                "scm_provider": "github",
            },
        )
        project_id = resp.json()["id"]

        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"scm_provider": ""},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["scm_provider"] is None
