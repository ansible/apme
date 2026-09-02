"""Unit tests for ADR-050 Phase 2 SCM providers (GitLab + Bitbucket)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient, Response

from apme_gateway.app import create_app
from apme_gateway.config import GatewayConfig
from apme_gateway.db import get_session
from apme_gateway.db.models import PatchedFile, Project, Scan, Session
from apme_gateway.scm.base import PullRequestResult
from apme_gateway.scm.bitbucket import (
    BitbucketCloudProvider,
    BitbucketServerProvider,
    _server_pr_url,
    parse_server_project_repo,
)
from apme_gateway.scm.gitlab import GitLabProvider, _parse_project_path
from apme_gateway.scm.urls import (
    is_bitbucket_cloud_api,
    require_https_api_base,
    resolve_bitbucket_api_url,
    resolve_gitlab_api_url,
    split_user_pass_token,
)

pytestmark = pytest.mark.usefixtures("gateway_db")


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


class TestUrlHelpers:
    """Tests for SCM URL / token helpers."""

    def test_split_user_pass(self) -> None:
        """Split username:password tokens on the first colon."""
        assert split_user_pass_token("alice:secret:extra") == ("alice", "secret:extra")
        assert split_user_pass_token("token-only") is None

    def test_parse_gitlab_nested_path(self) -> None:
        """Nested GitLab groups are URL-encoded."""
        assert _parse_project_path("https://gitlab.com/group/sub/repo.git") == "group%2Fsub%2Frepo"

    def test_parse_server_scm_path(self) -> None:
        """Parse Bitbucket Server /scm/PROJECT/repo.git URLs."""
        assert parse_server_project_repo("https://bb.example.com/scm/PROJ/my-repo.git") == (
            "PROJ",
            "my-repo",
        )

    def test_parse_server_projects_path(self) -> None:
        """Parse Bitbucket Server /projects/.../repos/... URLs."""
        assert parse_server_project_repo("https://bb.example.com/projects/PROJ/repos/my-repo") == (
            "PROJ",
            "my-repo",
        )

    def test_parse_server_unknown_layout_fails(self) -> None:
        """Refuse to guess project/repo from arbitrary path layouts."""
        with pytest.raises(ValueError, match="Expected /scm/"):
            parse_server_project_repo("https://bb.example.com/weird/layout/repo.git")

    def test_resolve_gitlab_cloud(self) -> None:
        """SaaS gitlab.com keeps the configured cloud API base."""
        url = resolve_gitlab_api_url(
            "https://gitlab.com/api/v4",
            "https://gitlab.com/group/repo.git",
        )
        assert url == "https://gitlab.com/api/v4"

    def test_resolve_gitlab_cloud_www_host(self) -> None:
        """www.gitlab.com clone URLs resolve as SaaS."""
        url = resolve_gitlab_api_url(
            "https://gitlab.com/api/v4",
            "https://www.gitlab.com/group/repo.git",
        )
        assert url == "https://gitlab.com/api/v4"

    def test_resolve_bitbucket_cloud_www_host(self) -> None:
        """www.bitbucket.org clone URLs resolve as Cloud."""
        url = resolve_bitbucket_api_url(
            "https://api.bitbucket.org/2.0",
            "https://www.bitbucket.org/ws/repo.git",
        )
        assert url == "https://api.bitbucket.org/2.0"

    def test_resolve_gitlab_self_hosted_requires_env(self) -> None:
        """Self-hosted GitLab rejects cloud-default API URL (SSRF / context-path)."""
        with pytest.raises(ValueError, match="APME_GITLAB_API_URL"):
            resolve_gitlab_api_url(
                "https://gitlab.com/api/v4",
                "https://gitlab.corp.example/group/repo.git",
            )

    def test_resolve_gitlab_dedicated_requires_env(self) -> None:
        """GitLab Dedicated (*.gitlab.com) is not treated as SaaS."""
        with pytest.raises(ValueError, match="APME_GITLAB_API_URL"):
            resolve_gitlab_api_url(
                "https://gitlab.com/api/v4",
                "https://customer.gitlab.com/group/repo.git",
            )

    def test_resolve_gitlab_self_hosted_with_explicit_url(self) -> None:
        """Explicit non-default API URL is used for self-hosted GitLab."""
        url = resolve_gitlab_api_url(
            "https://gitlab.corp.example/gitlab/api/v4",
            "https://gitlab.corp.example/group/repo.git",
        )
        assert url == "https://gitlab.corp.example/gitlab/api/v4"

    def test_resolve_gitlab_self_hosted_ignores_cloud_host_in_path(self) -> None:
        """Only parsed hostname determines cloud API base, not path substrings."""
        url = resolve_gitlab_api_url(
            "https://gitlab.corp.example/gitlab.com/api/v4",
            "https://gitlab.corp.example/group/repo.git",
        )
        assert url == "https://gitlab.corp.example/gitlab.com/api/v4"

    def test_resolve_bitbucket_self_hosted_requires_env(self) -> None:
        """Self-hosted Bitbucket rejects Cloud-default API URL."""
        with pytest.raises(ValueError, match="APME_BITBUCKET_API_URL"):
            resolve_bitbucket_api_url(
                "https://api.bitbucket.org/2.0",
                "https://bitbucket.corp.example/scm/PROJ/repo.git",
            )

    def test_resolve_bitbucket_self_hosted_with_explicit_url(self) -> None:
        """Explicit Server API URL (including context path) is preserved."""
        url = resolve_bitbucket_api_url(
            "https://corp.example/bitbucket/rest/api/1.0",
            "https://corp.example/bitbucket/scm/PROJ/repo.git",
        )
        assert url == "https://corp.example/bitbucket/rest/api/1.0"

    def test_resolve_gitlab_self_hosted_rejects_http_api_url(self) -> None:
        """Self-hosted GitLab API bases must use HTTPS."""
        with pytest.raises(ValueError, match="absolute https://"):
            resolve_gitlab_api_url(
                "http://gitlab.corp.example/api/v4",
                "https://gitlab.corp.example/group/repo.git",
            )

    def test_resolve_bitbucket_self_hosted_rejects_http_api_url(self) -> None:
        """Self-hosted Bitbucket API bases must use HTTPS."""
        with pytest.raises(ValueError, match="absolute https://"):
            resolve_bitbucket_api_url(
                "http://bb.corp.example/rest/api/1.0",
                "https://bb.corp.example/scm/PROJ/repo.git",
            )

    def test_resolve_gitlab_self_hosted_rejects_schemeless_api_url(self) -> None:
        """API bases without a scheme are rejected."""
        with pytest.raises(ValueError, match="absolute https://"):
            resolve_gitlab_api_url(
                "gitlab.corp.example/api/v4",
                "https://gitlab.corp.example/group/repo.git",
            )

    def test_require_https_api_base(self) -> None:
        """require_https_api_base rejects non-HTTPS URLs."""
        require_https_api_base("https://api.github.com", "GitHub")
        with pytest.raises(ValueError, match="GitHub API URL"):
            require_https_api_base("http://api.github.com", "GitHub")

    def test_server_pr_url_fallback_strips_credentials(self) -> None:
        """Fallback PR URLs must not echo clone URL credentials."""
        url = _server_pr_url(
            {"id": 7},
            "https://x-token-auth:SECRET@bb.example.com:7990/scm/KEY/repo.git",
        )
        assert url == "https://bb.example.com:7990/projects/KEY/repos/repo/pull-requests/7"
        assert "SECRET" not in url
        assert "x-token-auth" not in url

    def test_is_bitbucket_cloud_api_hostname_only(self) -> None:
        """Only api.bitbucket.org is Cloud; /2.0 on other hosts is Server."""
        assert is_bitbucket_cloud_api("https://api.bitbucket.org/2.0") is True
        assert is_bitbucket_cloud_api("https://bitbucket.corp/rest/api/2.0") is False
        assert is_bitbucket_cloud_api("https://bitbucket.corp/rest/api/1.0") is False


def _mock_response(
    status: int,
    payload: dict[str, object] | None = None,
    *,
    text: str = "",
) -> MagicMock:
    """Build a MagicMock that behaves like an httpx Response.

    Args:
        status: HTTP status code to expose on the mock.
        payload: Optional JSON body returned by ``resp.json()``.
        text: Optional response text body.

    Returns:
        MagicMock configured with ``status_code``, ``json``, and ``raise_for_status``.
    """
    resp = MagicMock(spec=Response)
    resp.status_code = status
    resp.json.return_value = payload or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status >= 400:

        def _raise() -> None:
            raise httpx.HTTPStatusError(
                f"HTTP {status}",
                request=httpx.Request("GET", "https://example.invalid"),
                response=resp,
            )

        resp.raise_for_status.side_effect = _raise
    return resp


class TestGitLabProviderUnit:
    """Mocked unit tests for GitLabProvider."""

    async def test_create_branch_and_mr(self) -> None:
        """Create branch, push files, and open an MR."""
        provider = GitLabProvider()
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        client.get = AsyncMock(return_value=_mock_response(404))
        client.post = AsyncMock(return_value=_mock_response(201, {"commit": {"id": "abc123"}}))

        with patch.object(GitLabProvider, "_client", return_value=client):
            sha = await provider.create_branch(
                "https://gitlab.com/group/repo.git",
                "main",
                "apme/fix",
                "token",
            )
            assert sha == "abc123"

            client.post = AsyncMock(return_value=_mock_response(201, {"id": "def456"}))
            commit = await provider.push_files(
                "https://gitlab.com/group/repo.git",
                "apme/fix",
                {"a.yml": b"content"},
                "msg",
                "token",
            )
            assert commit == "def456"
            actions = client.post.call_args.kwargs["json"]["actions"]
            assert actions[0]["action"] == "update"

            client.post = AsyncMock(
                return_value=_mock_response(201, {"web_url": "https://gitlab.com/g/r/-/merge_requests/9"})
            )
            result = await provider.create_pull_request(
                "https://gitlab.com/group/repo.git",
                "main",
                "apme/fix",
                "title",
                "body",
                "token",
            )
            assert result.provider == "gitlab"
            assert "merge_requests/9" in result.pr_url

    def test_headers_include_private_token(self) -> None:
        """PAT headers send Bearer and PRIVATE-TOKEN."""
        headers = GitLabProvider()._headers("glpat-x")
        assert headers["Authorization"] == "Bearer glpat-x"
        assert headers["PRIVATE-TOKEN"] == "glpat-x"

    def test_headers_basic_for_deploy_token(self) -> None:
        """username:token deploy tokens use Basic auth."""
        headers = GitLabProvider()._headers("gitlab+deploy-token-1:secret")
        assert headers["Authorization"].startswith("Basic ")

    async def test_push_files_retries_mixed_on_400_without_english_error(self) -> None:
        """On 400, probe each path instead of matching GitLab error text."""
        provider = GitLabProvider()
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(
            side_effect=[
                _mock_response(400, text="erreur inconnue"),
                _mock_response(201, {"id": "mixed123"}),
            ]
        )
        client.head = AsyncMock(
            side_effect=[
                _mock_response(404),
                _mock_response(200),
            ]
        )

        with patch.object(GitLabProvider, "_client", return_value=client):
            commit = await provider.push_files(
                "https://gitlab.com/group/repo.git",
                "apme/fix",
                {"new.yml": b"n", "old.yml": b"o"},
                "msg",
                "token",
            )

        assert commit == "mixed123"
        assert client.post.call_count == 2
        retry_actions = client.post.call_args_list[1].kwargs["json"]["actions"]
        by_path = {action["file_path"]: action["action"] for action in retry_actions}
        assert by_path == {"new.yml": "create", "old.yml": "update"}


class TestBitbucketCloudUnit:
    """Mocked unit tests for Bitbucket Cloud."""

    async def test_basic_auth_for_app_password(self) -> None:
        """App passwords produce Basic Authorization headers."""
        from apme_gateway.scm.bitbucket import _auth_headers

        headers = _auth_headers("alice:secret")
        assert headers["Authorization"].startswith("Basic ")

    async def test_create_pr(self) -> None:
        """Create a Cloud PR and return the HTML link."""
        provider = BitbucketCloudProvider()
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(
            return_value=_mock_response(
                201,
                {"links": {"html": {"href": "https://bitbucket.org/ws/repo/pull-requests/3"}}},
            )
        )
        with patch.object(BitbucketCloudProvider, "_client", return_value=client):
            result = await provider.create_pull_request(
                "https://bitbucket.org/ws/repo.git",
                "main",
                "apme/fix",
                "title",
                "body",
                "token",
            )
        assert result.provider == "bitbucket"
        assert result.pr_url.endswith("/pull-requests/3")

    async def test_push_files_multipart_metadata_has_no_filename(self) -> None:
        """Cloud /src metadata parts use (None, value) so httpx omits filename."""
        provider = BitbucketCloudProvider()
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=_mock_response(201))

        with (
            patch.object(BitbucketCloudProvider, "_client", return_value=client),
            patch.object(
                BitbucketCloudProvider,
                "branch_head_sha",
                new=AsyncMock(return_value="newsha123"),
            ),
        ):
            sha = await provider.push_files(
                "https://bitbucket.org/ws/repo.git",
                "apme/fix",
                {"roles/a.yml": b"x: 1\n"},
                "commit msg",
                "token",
                parent_commit_sha="parentsha",
            )

        assert sha == "newsha123"
        files_arg = client.post.call_args.kwargs["files"]
        by_name = {name: value for name, value in files_arg}
        assert by_name["message"] == (None, "commit msg")
        assert by_name["branch"] == (None, "apme/fix")
        assert by_name["parents"] == (None, "parentsha")
        assert by_name["roles/a.yml"][0] == "a.yml"
        assert by_name["roles/a.yml"][1] == b"x: 1\n"

    async def test_push_files_rejects_non_success_status(self) -> None:
        """Cloud /src push treats 3xx responses as failure instead of success."""
        provider = BitbucketCloudProvider()
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=_mock_response(302))

        with (
            patch.object(BitbucketCloudProvider, "_client", return_value=client),
            patch.object(
                BitbucketCloudProvider,
                "branch_head_sha",
                new=AsyncMock(return_value="unchangedsha"),
            ) as mock_tip,
            pytest.raises(RuntimeError, match="Unexpected status 302"),
        ):
            await provider.push_files(
                "https://bitbucket.org/ws/repo.git",
                "apme/fix",
                {"roles/a.yml": b"x: 1\n"},
                "commit msg",
                "token",
                parent_commit_sha="parentsha",
            )

        mock_tip.assert_not_called()


class TestBitbucketServerUnit:
    """Mocked unit tests for Bitbucket Server/DC."""

    async def test_create_branch_uses_latest_commit_not_ref_id(self) -> None:
        """Branch-create ``id`` is a ref name; return ``latestCommit`` SHA only."""
        provider = BitbucketServerProvider("https://bb.example.com/rest/api/1.0")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=_mock_response(404))
        client.post = AsyncMock(
            return_value=_mock_response(
                200,
                {
                    "id": "refs/heads/apme/fix",
                    "latestCommit": "abc123def",
                },
            )
        )

        with patch.object(BitbucketServerProvider, "_client", return_value=client):
            sha = await provider.create_branch(
                "https://bb.example.com/scm/PROJ/repo.git",
                "main",
                "apme/fix",
                "token",
            )

        assert sha == "abc123def"

    async def test_create_pr(self) -> None:
        """Create a Server PR and resolve the web URL."""
        provider = BitbucketServerProvider("https://bb.example.com/rest/api/1.0")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(
            return_value=_mock_response(
                201,
                {
                    "id": 7,
                    "links": {
                        "self": [
                            {
                                "href": ("https://bb.example.com/projects/PROJ/repos/repo/pull-requests/7"),
                            }
                        ]
                    },
                },
            )
        )
        with patch.object(BitbucketServerProvider, "_client", return_value=client):
            result = await provider.create_pull_request(
                "https://bb.example.com/scm/PROJ/repo.git",
                "main",
                "apme/fix",
                "title",
                "body",
                "token",
            )
        assert result.provider == "bitbucket"
        assert result.pr_url.endswith("/pull-requests/7")

    async def test_reuse_existing_pr_matches_target_branch(self) -> None:
        """Reuse only an open PR whose target branch matches base_branch."""
        provider = BitbucketServerProvider("https://bb.example.com/rest/api/1.0")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=_mock_response(409))
        client.get = AsyncMock(
            return_value=_mock_response(
                200,
                {
                    "values": [
                        {
                            "id": 3,
                            "fromRef": {"id": "refs/heads/apme/fix"},
                            "toRef": {"id": "refs/heads/develop"},
                            "links": {
                                "self": [
                                    {
                                        "href": ("https://bb.example.com/projects/PROJ/repos/repo/pull-requests/3"),
                                    }
                                ]
                            },
                        },
                        {
                            "id": 7,
                            "fromRef": {"id": "refs/heads/apme/fix"},
                            "toRef": {"id": "refs/heads/main"},
                            "links": {
                                "self": [
                                    {
                                        "href": ("https://bb.example.com/projects/PROJ/repos/repo/pull-requests/7"),
                                    }
                                ]
                            },
                        },
                    ],
                },
            )
        )
        with patch.object(BitbucketServerProvider, "_client", return_value=client):
            result = await provider.create_pull_request(
                "https://bb.example.com/scm/PROJ/repo.git",
                "main",
                "apme/fix",
                "title",
                "body",
                "token",
            )
        assert result.pr_url.endswith("/pull-requests/7")

    async def test_push_files_uses_multipart_and_omits_source_for_create(self) -> None:
        """Server browse PUT uses multipart; new files omit sourceCommitId."""
        provider = BitbucketServerProvider("https://bb.example.com/rest/api/1.0")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        # Probe: file missing → create; PUT returns new commit; tip refresh.
        client.get = AsyncMock(
            side_effect=[
                _mock_response(404),
                _mock_response(200, {"values": [{"id": "tip2"}]}),
            ]
        )
        client.put = AsyncMock(return_value=_mock_response(200, {"id": "tip2"}))

        with (
            patch.object(BitbucketServerProvider, "_client", return_value=client),
            patch.object(
                BitbucketServerProvider,
                "branch_head_sha",
                new=AsyncMock(side_effect=["tip1", "tip2", "tip2"]),
            ),
        ):
            sha = await provider.push_files(
                "https://bb.example.com/scm/PROJ/repo.git",
                "apme/fix",
                {"new.yml": b"a: 1\n"},
                "msg",
                "token",
                parent_commit_sha="tip1",
            )

        assert sha == "tip2"
        assert "files" in client.put.call_args.kwargs
        assert "data" not in client.put.call_args.kwargs
        form_parts = {name: value for name, value in client.put.call_args.kwargs["files"]}
        assert form_parts["content"] == (None, b"a: 1\n")
        assert form_parts["message"] == (None, "msg")
        assert form_parts["branch"] == (None, "apme/fix")
        assert "sourceCommitId" not in form_parts

    async def test_push_files_includes_source_commit_for_update(self) -> None:
        """Existing files include sourceCommitId in the multipart body."""
        provider = BitbucketServerProvider("https://bb.example.com/rest/api/1.0")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=_mock_response(200, {"lines": []}))
        client.put = AsyncMock(return_value=_mock_response(200, {"id": "tip2"}))

        with (
            patch.object(BitbucketServerProvider, "_client", return_value=client),
            patch.object(
                BitbucketServerProvider,
                "branch_head_sha",
                new=AsyncMock(return_value="tip2"),
            ),
        ):
            await provider.push_files(
                "https://bb.example.com/scm/PROJ/repo.git",
                "apme/fix",
                {"existing.yml": b"a: 2\n"},
                "msg",
                "token",
                parent_commit_sha="tip1",
            )

        form_parts = {name: value for name, value in client.put.call_args.kwargs["files"]}
        assert form_parts["sourceCommitId"] == (None, "tip1")


async def _seed(
    *,
    repo_url: str,
    scm_provider: str | None,
    scm_token: str = "tok",  # noqa: S107 - test fixture value, not a real credential
) -> None:
    """Seed a remediated project for submit tests.

    Args:
        repo_url: HTTPS clone URL for the seeded project.
        scm_provider: Explicit provider type, or ``None`` for auto-detect.
        scm_token: Per-project SCM token.
    """
    async with get_session() as db:
        db.add(
            Project(
                id="proj-1",
                name="p",
                repo_url=repo_url,
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token=scm_token,
                scm_provider=scm_provider,
            )
        )
        db.add(Session(session_id="s1", project_path="/p", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id="scan-1",
                session_id="s1",
                project_id="proj-1",
                project_path="/p",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                fixed_count=1,
            )
        )
        db.add(PatchedFile(scan_id="scan-1", path="a.yml", content=b"x: 1\n"))
        await db.commit()


class TestSubmitPhase2Providers:
    """End-to-end submit path for GitLab and Bitbucket (mocked providers)."""

    async def test_submit_gitlab(self, client: AsyncClient) -> None:
        """Submit creates an MR via the GitLab provider.

        Args:
            client: Async test client.
        """
        await _seed(repo_url="https://gitlab.com/group/repo.git", scm_provider=None)
        mock_provider = AsyncMock()
        mock_provider.branch_head_sha = AsyncMock(return_value=None)
        mock_provider.create_branch = AsyncMock(return_value="sha1")
        mock_provider.push_files = AsyncMock(return_value="sha2")
        mock_provider.create_pull_request = AsyncMock(
            return_value=PullRequestResult(
                pr_url="https://gitlab.com/group/repo/-/merge_requests/1",
                branch_name="apme/remediate-scan-1",
                provider="gitlab",
            )
        )
        with (
            patch(
                "apme_gateway.config.load_config",
                return_value=GatewayConfig(gitlab_api_url="https://gitlab.com/api/v4", scm_token=""),
            ),
            patch("apme_gateway.scm.get_provider", return_value=mock_provider) as mock_get,
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1", "create_pr": True},
            )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "gitlab"
        assert "merge_requests" in resp.json()["pr_url"]
        assert mock_get.call_args.kwargs.get("api_base_url") == "https://gitlab.com/api/v4"

    async def test_submit_bitbucket_cloud(self, client: AsyncClient) -> None:
        """Submit creates a PR via the Bitbucket provider.

        Args:
            client: Async test client.
        """
        await _seed(repo_url="https://bitbucket.org/ws/repo.git", scm_provider=None)
        mock_provider = AsyncMock()
        mock_provider.branch_head_sha = AsyncMock(return_value=None)
        mock_provider.create_branch = AsyncMock(return_value="sha1")
        mock_provider.push_files = AsyncMock(return_value="sha2")
        mock_provider.create_pull_request = AsyncMock(
            return_value=PullRequestResult(
                pr_url="https://bitbucket.org/ws/repo/pull-requests/2",
                branch_name="apme/remediate-scan-1",
                provider="bitbucket",
            )
        )
        with (
            patch(
                "apme_gateway.config.load_config",
                return_value=GatewayConfig(
                    bitbucket_api_url="https://api.bitbucket.org/2.0",
                    scm_token="",
                ),
            ),
            patch("apme_gateway.scm.get_provider", return_value=mock_provider),
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1", "create_pr": True},
            )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "bitbucket"

    async def test_submit_self_hosted_without_api_url_returns_422(self, client: AsyncClient) -> None:
        """Self-hosted Bitbucket without APME_BITBUCKET_API_URL fails clearly.

        Args:
            client: Async test client.
        """
        await _seed(
            repo_url="https://bb.corp.example/scm/PROJ/repo.git",
            scm_provider="bitbucket",
        )
        with patch(
            "apme_gateway.config.load_config",
            return_value=GatewayConfig(bitbucket_api_url="https://api.bitbucket.org/2.0"),
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/operation/submit",
                json={"activity_id": "scan-1", "create_pr": True},
            )
        assert resp.status_code == 422
        assert "APME_BITBUCKET_API_URL" in resp.json()["detail"]


class TestProjectScmValidation:
    """REST validation for scm_provider on create."""

    async def test_reject_unknown_provider(self, client: AsyncClient) -> None:
        """Unknown scm_provider returns 400.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "bad-provider",
                "repo_url": "https://github.com/org/repo.git",
                "branch": "main",
                "scm_provider": "svn",
            },
        )
        assert resp.status_code == 400

    async def test_reject_provider_mismatch(self, client: AsyncClient) -> None:
        """Explicit scm_provider must match a detectable cloud host.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "mismatch",
                "repo_url": "https://github.com/org/repo.git",
                "branch": "main",
                "scm_provider": "gitlab",
            },
        )
        assert resp.status_code == 400
        assert "does not match" in resp.json()["detail"]

    async def test_self_hosted_requires_provider(self, client: AsyncClient) -> None:
        """Self-hosted URL without scm_provider returns 400.

        Args:
            client: Async test client.
        """
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "self-hosted",
                "repo_url": "https://git.corp.example/scm/PROJ/repo.git",
                "branch": "main",
            },
        )
        assert resp.status_code == 400
        assert "scm_provider" in resp.json()["detail"]

    async def test_update_unrelated_fields_skips_provider_check(self, client: AsyncClient) -> None:
        """Patching name on legacy self-hosted projects does not require scm_provider.

        Args:
            client: Async test client.
        """
        await _seed(
            repo_url="https://bb.corp.example/scm/PROJ/repo.git",
            scm_provider=None,
        )
        resp = await client.patch(
            "/api/v1/projects/proj-1",
            json={"name": "renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed"

    async def test_clear_scm_provider_with_null(self, client: AsyncClient) -> None:
        """Explicit JSON null clears scm_provider on update.

        Args:
            client: Async test client.
        """
        await _seed(
            repo_url="https://github.com/org/repo.git",
            scm_provider="github",
        )
        resp = await client.patch(
            "/api/v1/projects/proj-1",
            json={"scm_provider": None},
        )
        assert resp.status_code == 200
        assert resp.json()["scm_provider"] is None
