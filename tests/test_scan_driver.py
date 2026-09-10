"""Unit tests for the gateway project scan driver (ADR-037)."""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme.v1 import engine_pb2
from apme_gateway.scan.driver import (
    _REMOTE_HEAD_CACHE,
    _REMOTE_HEAD_NEG_CACHE,
    _git_auth_env,
    _git_subprocess_env,
    _inject_token_in_url,
    _merge_git_config_env,
    clone_repo,
    coerce_option_bool,
    derive_session_id,
    fetch_remote_head,
    get_clone_head,
    run_project_scan,
)
from apme_gateway.scm.redaction import redact_credentials


def _decode_auth_env(env: dict[str, str]) -> str:
    """Decode the ``http.extraHeader`` basic credentials from a git env mapping.

    Args:
        env: Git subprocess environment containing ``GIT_CONFIG_*`` entries.

    Returns:
        Decoded ``username:password`` credential string.
    """
    count = int(env["GIT_CONFIG_COUNT"])
    value = next(
        env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(count - 1, -1, -1)
        if env.get(f"GIT_CONFIG_KEY_{i}", "").endswith(".extraHeader")
    )
    scheme, _, encoded = value.partition("Basic ")
    assert scheme.strip().rstrip(":").upper() == "AUTHORIZATION"
    return base64.b64decode(encoded.strip()).decode("utf-8")


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("  false  ", False),
        (None, False),
        ("maybe", False),
        ([], False),
    ],
)
def test_coerce_option_bool(value: object, expected: bool) -> None:
    """Untyped option values must not treat the string 'false' as True.

    Args:
        value: Raw option value under test.
        expected: Expected coerced boolean.
    """
    assert coerce_option_bool(value) is expected


def test_coerce_option_bool_default() -> None:
    """Unknown strings fall back to the explicit default."""
    assert coerce_option_bool("maybe", default=True) is True
    assert coerce_option_bool(None, default=True) is True


def test_derive_session_id_deterministic() -> None:
    """Same project ID always produces the same session ID."""
    sid1 = derive_session_id("project-abc")
    sid2 = derive_session_id("project-abc")
    assert sid1 == sid2
    assert len(sid1) == 16


def test_derive_session_id_different_projects() -> None:
    """Different project IDs produce different session IDs."""
    sid1 = derive_session_id("project-a")
    sid2 = derive_session_id("project-b")
    assert sid1 != sid2


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_success() -> None:
    """Verify clone_repo succeeds when git returns 0."""
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "repo")
            await clone_repo("https://github.com/test/repo.git", "main", dest)


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_failure() -> None:
    """Verify clone_repo raises RuntimeError when git fails."""
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 128
        result.stderr = "fatal: repository not found"
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "repo")
            with pytest.raises(RuntimeError, match="git clone failed"):
                await clone_repo("https://github.com/bad/repo.git", "main", dest)


def test_git_subprocess_env_uses_ssl_cert_file_for_git() -> None:
    """Git subprocesses bridge generic CA variables into ``GIT_SSL_CAINFO``."""
    with patch.dict(
        os.environ,
        {
            "SSL_CERT_FILE": "/etc/ssl/certs/custom-ca.pem",
            "GIT_SSL_CAINFO": "",
        },
        clear=True,
    ):
        env = _git_subprocess_env()

    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/custom-ca.pem"
    assert env["GIT_SSL_CAINFO"] == "/etc/ssl/certs/custom-ca.pem"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_uses_git_ca_env() -> None:
    """Remote head lookups pass the derived CA env through to git."""
    fake_sha = "b" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch.dict(os.environ, {"SSL_CERT_FILE": "/etc/ssl/certs/custom-ca.pem"}, clear=True),
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _executor, func: func())

        sha = await fetch_remote_head("https://github.com/test/repo.git", "main")

    assert sha == fake_sha
    assert mock_run.call_args.kwargs["env"]["GIT_SSL_CAINFO"] == "/etc/ssl/certs/custom-ca.pem"


async def _async_iter(
    items: list[object],
) -> AsyncIterator[object]:
    """Wrap items into an async iterator for mocking gRPC streams.

    Args:
        items: Objects to yield.

    Yields:
        object: Each item in sequence.
    """
    for item in items:
        yield item


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_run_project_scan_full_flow() -> None:
    """Verify run_project_scan clones, chunks, and streams FixSession (check mode)."""
    mock_chunks = [engine_pb2.ScanChunk(last=True, scan_id="test-scan")]

    progress_events: list[object] = []

    async def track_progress(event: object) -> None:
        progress_events.append(event)

    with (
        patch("apme_gateway.scan.driver.clone_repo", new_callable=AsyncMock) as mock_clone,
        patch("apme_gateway.scan.driver.get_clone_head", return_value="abc123def456" * 4),
        patch("apme_gateway.scan.driver.yield_scan_chunks", return_value=mock_chunks),
        patch("apme_gateway.scan.driver.grpc.aio.insecure_channel") as mock_channel_cls,
    ):
        mock_report = MagicMock()
        mock_report.fixed = 0
        mock_report.remaining_ai = 0
        mock_report.remaining_manual = 0

        mock_result = MagicMock()
        mock_result.report = mock_report
        mock_result.remaining_violations = []

        mock_event = MagicMock()
        mock_event.WhichOneof.return_value = "result"
        mock_event.result = mock_result

        mock_stub = MagicMock()
        mock_stub.FixSession.return_value = _async_iter([mock_event])

        mock_channel = MagicMock()
        mock_channel.close = AsyncMock()
        mock_channel_cls.return_value = mock_channel

        with patch(
            "apme_gateway.scan.driver.engine_pb2_grpc.EngineStub",
            return_value=mock_stub,
        ):
            scan_id, result, commit_sha = await run_project_scan(
                project_id="test-proj",
                repo_url="https://github.com/test/repo.git",
                branch="main",
                engine_address="127.0.0.1:50051",
                progress_callback=track_progress,
            )

        mock_clone.assert_called_once()
        assert scan_id is not None
        assert len(scan_id) == 32
        assert result is not None
        assert commit_sha == "abc123def456" * 4
        # ADR-068: server enforces adaptive deadlines; no fixed client timeout.
        assert mock_stub.FixSession.call_args.kwargs.get("timeout") is None


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_success() -> None:
    """Verify fetch_remote_head returns SHA from ls-remote output."""
    fake_sha = "a" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        sha = await fetch_remote_head("https://github.com/test/repo.git", "main")
        assert sha == fake_sha


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_non_https_returns_none() -> None:
    """Verify non-https URLs are rejected."""
    sha = await fetch_remote_head("ssh://git@github.com/test/repo.git", "main")
    assert sha is None


def test_get_clone_head_returns_sha() -> None:
    """Verify get_clone_head reads HEAD from a real git init."""
    with tempfile.TemporaryDirectory() as td:
        import subprocess

        subprocess.run(["git", "init", td], check=True, capture_output=True)  # noqa: S603, S607
        subprocess.run(  # noqa: S603, S607
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@test",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--allow-empty",
                "-m",
                "init",
            ],
            check=True,
            capture_output=True,
            cwd=td,
        )
        sha = get_clone_head(td)
        assert sha is not None
        assert len(sha) == 40


def test_get_clone_head_invalid_dir_returns_none() -> None:
    """Verify get_clone_head returns None for a non-git directory."""
    with tempfile.TemporaryDirectory() as td:
        sha = get_clone_head(td)
        assert sha is None


class TestInjectTokenInUrl:
    """Tests for _inject_token_in_url with multiple SCM providers."""

    def test_github_uses_x_access_token(self) -> None:
        """GitHub URLs use x-access-token username."""
        url = _inject_token_in_url("https://github.com/owner/repo.git", "ghp_test123")
        assert url == "https://x-access-token:ghp_test123@github.com/owner/repo.git"

    def test_gitlab_uses_oauth2(self) -> None:
        """GitLab URLs use oauth2 username."""
        url = _inject_token_in_url("https://gitlab.com/owner/repo.git", "glpat-test123")
        assert url == "https://oauth2:glpat-test123@gitlab.com/owner/repo.git"

    def test_bitbucket_uses_x_token_auth(self) -> None:
        """Bitbucket URLs use x-token-auth username."""
        url = _inject_token_in_url("https://bitbucket.org/owner/repo.git", "bb_test123")
        assert url == "https://x-token-auth:bb_test123@bitbucket.org/owner/repo.git"

    def test_bitbucket_app_password_uses_user_pass(self) -> None:
        """Bitbucket app passwords use username:password credentials."""
        url = _inject_token_in_url("https://bitbucket.org/owner/repo.git", "alice:app-secret")
        assert url == "https://alice:app-secret@bitbucket.org/owner/repo.git"

    def test_gitlab_deploy_token_uses_user_pass(self) -> None:
        """GitLab deploy tokens use username:password credentials."""
        url = _inject_token_in_url(
            "https://gitlab.com/group/repo.git",
            "gitlab+deploy-token-1:secret",
        )
        assert url == "https://gitlab%2Bdeploy-token-1:secret@gitlab.com/group/repo.git"

    def test_provider_hint_for_self_hosted_bitbucket(self) -> None:
        """Explicit scm_provider selects Bitbucket auth on non-bitbucket hostnames."""
        url = _inject_token_in_url(
            "https://git.corp.example.com/scm/PROJ/repo.git",
            "bb_token",
            scm_provider="bitbucket",
        )
        assert url == "https://x-token-auth:bb_token@git.corp.example.com/scm/PROJ/repo.git"

    def test_provider_hint_for_self_hosted_gitlab(self) -> None:
        """Explicit scm_provider selects GitLab oauth2 on custom hosts."""
        url = _inject_token_in_url(
            "https://git.corp.example.com/group/repo.git",
            "glpat-x",
            scm_provider="gitlab",
        )
        assert url == "https://oauth2:glpat-x@git.corp.example.com/group/repo.git"

    def test_unknown_provider_uses_git(self) -> None:
        """Unknown providers use git as fallback username."""
        url = _inject_token_in_url("https://custom-git.example.com/repo.git", "token123")
        assert url == "https://git:token123@custom-git.example.com/repo.git"

    def test_preserves_port(self) -> None:
        """Port numbers are preserved in the URL."""
        url = _inject_token_in_url("https://gitlab.com:8443/owner/repo.git", "token")
        assert url == "https://oauth2:token@gitlab.com:8443/owner/repo.git"

    def test_preserves_path(self) -> None:
        """Path components are preserved."""
        url = _inject_token_in_url("https://github.com/org/sub/repo.git", "token")
        assert url == "https://x-access-token:token@github.com/org/sub/repo.git"

    def test_encodes_special_characters(self) -> None:
        """Special characters in tokens are percent-encoded."""
        url = _inject_token_in_url("https://github.com/owner/repo.git", "token@with:special/chars")
        # @ becomes %40, : becomes %3A, / becomes %2F
        assert "token%40with%3Aspecial%2Fchars" in url
        assert "@github.com" in url  # The auth separator @ is preserved

    def test_encodes_hash_and_question(self) -> None:
        """Hash and question mark in tokens are encoded to prevent URL parsing issues."""
        url = _inject_token_in_url("https://github.com/owner/repo.git", "token#with?chars")
        assert "%23" in url  # # encoded
        assert "%3F" in url  # ? encoded


class TestRedactCredentials:
    """Tests for redact_credentials helper."""

    def test_redacts_token_in_url(self) -> None:
        """Credentials in URLs are redacted."""
        text = "fatal: https://x-access-token:ghp_secret123@github.com/repo.git not found"
        result = redact_credentials(text)
        assert "ghp_secret123" not in result
        assert "[REDACTED]" in result
        assert "github.com/repo.git" in result

    def test_redacts_multiple_urls(self) -> None:
        """Multiple URLs in text are all redacted."""
        text = "tried https://user:pass1@a.com and https://user:pass2@b.com"
        result = redact_credentials(text)
        assert "pass1" not in result
        assert "pass2" not in result

    def test_preserves_urls_without_auth(self) -> None:
        """URLs without credentials are unchanged."""
        text = "clone https://github.com/public/repo.git"
        result = redact_credentials(text)
        assert result == text

    def test_redacts_basic_auth_header(self) -> None:
        """AUTHORIZATION Basic header tokens are masked."""
        token = base64.b64encode(b"x-access-token:ghp_secret123").decode("ascii")
        text = f"http.https://github.com.extraHeader: AUTHORIZATION: Basic {token}"
        result = redact_credentials(text)
        assert token not in result
        assert "Basic [REDACTED]" in result

    def test_redacts_curl_verbose_authorization(self) -> None:
        """Curl-verbose Authorization headers are masked case-insensitively."""
        token = base64.b64encode(b"oauth2:glpat-secret").decode("ascii")
        text = f"> Authorization: Basic {token}\r\n< HTTP/1.1 401"
        result = redact_credentials(text)
        assert token not in result
        assert "[REDACTED]" in result

    def test_redacts_url_and_basic_header_together(self) -> None:
        """URL credentials and header tokens are both masked."""
        token = base64.b64encode(b"git:s3cret-token").decode("ascii")
        text = f"fatal: https://x-access-token:s3cret-token@github.com/repo not found (AUTHORIZATION: Basic {token})"
        result = redact_credentials(text)
        assert "s3cret-token" not in result
        assert token not in result
        assert result.count("[REDACTED]") >= 2


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_with_scm_token() -> None:
    """Verify clone_repo passes the token via env, never in argv."""
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with (
            tempfile.TemporaryDirectory() as td,
            patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
        ):
            mock_run.return_value = result
            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

            dest = os.path.join(td, "repo")
            await clone_repo("https://github.com/owner/repo.git", "main", dest, scm_token="ghp_test")

        call_args = mock_run.call_args[0][0]
        assert call_args[-2] == "https://github.com/owner/repo.git"
        assert "ghp_test" not in " ".join(call_args)
        env = mock_run.call_args.kwargs["env"]
        assert _decode_auth_env(env) == "x-access-token:ghp_test"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_redacts_error_messages() -> None:
    """Verify clone_repo redacts tokens from error messages."""
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 128
        result.stderr = "fatal: https://x-access-token:ghp_secret@github.com/repo not found"
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "repo")
            with pytest.raises(RuntimeError) as exc_info:
                await clone_repo(
                    "https://github.com/bad/repo.git",
                    "main",
                    dest,
                    scm_token="ghp_secret",
                )

        assert "ghp_secret" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_redacts_basic_header_in_error() -> None:
    """Verify clone_repo masks AUTHORIZATION Basic material from git stderr."""
    token = base64.b64encode(b"x-access-token:ghp_secret123").decode("ascii")
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 128
        result.stderr = f"trace: http.extraHeader: AUTHORIZATION: Basic {token}\nfatal: auth failed"
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "repo")
            with pytest.raises(RuntimeError) as exc_info:
                await clone_repo(
                    "https://github.com/bad/repo.git",
                    "main",
                    dest,
                    scm_token="ghp_secret123",
                )

        assert token not in str(exc_info.value)
        assert "ghp_secret123" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_with_scm_token() -> None:
    """Verify fetch_remote_head passes the token via env, never in argv."""
    fake_sha = "c" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        sha = await fetch_remote_head(
            "https://gitlab.com/owner/repo.git",
            "main",
            scm_token="glpat-test",
        )

    assert sha == fake_sha
    call_args = mock_run.call_args[0][0]
    assert call_args[3] == "https://gitlab.com/owner/repo.git"
    assert "glpat-test" not in " ".join(call_args)
    env = mock_run.call_args.kwargs["env"]
    assert _decode_auth_env(env) == "oauth2:glpat-test"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_negative_cache_throttles_retries() -> None:
    """Failed ls-remote lookups are cached with a short TTL, not forever."""
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 128
        result.stdout = ""
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        assert await fetch_remote_head("https://github.com/owner/repo.git", "main") is None
        assert await fetch_remote_head("https://github.com/owner/repo.git", "main") is None

    assert _REMOTE_HEAD_CACHE == {}
    assert len(_REMOTE_HEAD_NEG_CACHE) == 1
    # Second lookup served from the short-TTL negative cache: no new subprocess.
    assert mock_run.call_count == 1


class TestGitAuthEnv:
    """Tests for _git_auth_env token transport via git config env."""

    def test_github_credentials(self) -> None:
        """GitHub tokens map to x-access-token basic credentials."""
        env = _git_auth_env("https://github.com/owner/repo.git", "ghp_test123")
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com.extraHeader"
        assert _decode_auth_env(env) == "x-access-token:ghp_test123"

    def test_auth_header_scoped_to_repo_origin(self) -> None:
        """The extraHeader key is scoped per origin, including ports."""
        env = _git_auth_env("https://git.example.com:8443/org/repo.git", "token123")
        assert env["GIT_CONFIG_KEY_0"] == "http.https://git.example.com:8443.extraHeader"
        assert _decode_auth_env(env) == "git:token123"

    def test_merge_preserves_existing_git_config(self) -> None:
        """Merging appends pairs instead of clobbering existing entries."""
        base = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.sslVerify",
            "GIT_CONFIG_VALUE_0": "false",
        }
        merged = _merge_git_config_env(base, [("http.https://a.example.extraHeader", "AUTHORIZATION: Basic eA==")])
        assert merged["GIT_CONFIG_COUNT"] == "2"
        assert merged["GIT_CONFIG_KEY_0"] == "http.sslVerify"
        assert merged["GIT_CONFIG_KEY_1"] == "http.https://a.example.extraHeader"
        assert merged["GIT_CONFIG_VALUE_1"] == "AUTHORIZATION: Basic eA=="

    def test_gitlab_user_pass_credentials(self) -> None:
        """GitLab deploy tokens keep username:password credentials."""
        env = _git_auth_env(
            "https://gitlab.com/group/repo.git",
            "gitlab+deploy-token-1:secret",
        )
        assert _decode_auth_env(env) == "gitlab+deploy-token-1:secret"

    def test_unknown_provider_git_fallback(self) -> None:
        """Unknown providers fall back to the git username."""
        env = _git_auth_env("https://custom-git.example.com/repo.git", "token123")
        assert _decode_auth_env(env) == "git:token123"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_cache_separates_auth_and_unauth() -> None:
    """Verify cache keys differentiate authenticated vs unauthenticated requests."""
    fake_sha_unauth = "a" * 40
    fake_sha_auth = "b" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()

    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        # First call: unauthenticated
        result_unauth = MagicMock()
        result_unauth.returncode = 0
        result_unauth.stdout = f"{fake_sha_unauth}\trefs/heads/main\n"
        mock_run.return_value = result_unauth
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        sha1 = await fetch_remote_head("https://github.com/owner/repo.git", "main")
        assert sha1 == fake_sha_unauth

        # Second call: authenticated (different token, should NOT use cached unauth result)
        result_auth = MagicMock()
        result_auth.returncode = 0
        result_auth.stdout = f"{fake_sha_auth}\trefs/heads/main\n"
        mock_run.return_value = result_auth

        sha2 = await fetch_remote_head(
            "https://github.com/owner/repo.git",
            "main",
            scm_token="ghp_secret",
        )
        # Should make a new request, not return cached unauthenticated result
        assert sha2 == fake_sha_auth
        assert mock_run.call_count == 2


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_separates_distinct_tokens() -> None:
    """Two different tokens on the same repo+branch do not share a cache entry."""
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        first = MagicMock()
        first.returncode = 0
        first.stdout = f"{'a' * 40}\trefs/heads/main\n"
        mock_run.return_value = first
        assert await fetch_remote_head("https://github.com/o/r.git", "main", scm_token="tok-one") == "a" * 40

        second = MagicMock()
        second.returncode = 0
        second.stdout = f"{'b' * 40}\trefs/heads/main\n"
        mock_run.return_value = second
        assert await fetch_remote_head("https://github.com/o/r.git", "main", scm_token="tok-two") == "b" * 40

        assert mock_run.call_count == 2


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_timeout_expired_surfaces_runtime_error() -> None:
    """clone_repo TimeoutExpired surfaces as RuntimeError, never raw."""
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=120)
        )
        with tempfile.TemporaryDirectory() as td, pytest.raises(RuntimeError, match="timed out"):
            await clone_repo("https://github.com/o/r.git", "main", td + "/repo", scm_token="s3cret")


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_strips_embedded_userinfo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Userinfo in the stored URL never reaches git argv; host is warned, secret is not.

    Args:
        caplog: Pytest log capture fixture.
    """
    secret_url = "https://deployer:s3cret-token@github.com/owner/repo.git"
    with (
        caplog.at_level("WARNING", logger="apme_gateway.scan.driver"),
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        with tempfile.TemporaryDirectory() as td:
            await clone_repo(secret_url, "main", os.path.join(td, "repo"))

    call_args = mock_run.call_args[0][0]
    assert call_args[-2] == "https://github.com/owner/repo.git"
    assert "s3cret-token" not in " ".join(call_args)
    env = mock_run.call_args.kwargs["env"]
    assert _decode_auth_env(env) == "deployer:s3cret-token"
    assert "github.com" in caplog.text
    assert "s3cret-token" not in caplog.text


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_userinfo_with_token_keeps_header_auth() -> None:
    """Stripped userinfo URLs still authenticate via header env when scm_token is set."""
    secret_url = "https://deployer:old-credential@github.com/owner/repo.git"
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with (
            tempfile.TemporaryDirectory() as td,
            patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
        ):
            mock_run.return_value = result
            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

            dest = os.path.join(td, "repo")
            await clone_repo(secret_url, "main", dest, scm_token="ghp_test")

        call_args = mock_run.call_args[0][0]
        assert call_args[-2] == "https://github.com/owner/repo.git"
        assert "old-credential" not in " ".join(call_args)
        assert "ghp_test" not in " ".join(call_args)
        env = mock_run.call_args.kwargs["env"]
        assert _decode_auth_env(env) == "x-access-token:ghp_test"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_malformed_port_returns_none() -> None:
    """Malformed ports in authenticated URLs return None instead of raising."""
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    bad_url = "https://host:badport/repo.git"
    sha = await fetch_remote_head(bad_url, "main", scm_token="tok")
    assert sha is None


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_strips_embedded_userinfo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ls-remote receives the stripped URL; header auth still sent when scm_token is set.

    Args:
        caplog: Pytest log capture fixture.
    """
    fake_sha = "f" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    secret_url = "https://deployer:s3cret-token@github.com/owner/repo.git"
    with (
        caplog.at_level("WARNING", logger="apme_gateway.scan.driver"),
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        sha = await fetch_remote_head(secret_url, "main", scm_token="ghp_test")

    assert sha == fake_sha
    call_args = mock_run.call_args[0][0]
    assert call_args[3] == "https://github.com/owner/repo.git"
    assert "s3cret-token" not in " ".join(call_args)
    assert "ghp_test" not in " ".join(call_args)
    env = mock_run.call_args.kwargs["env"]
    assert _decode_auth_env(env) == "x-access-token:ghp_test"
    assert "github.com" in caplog.text
    assert "s3cret-token" not in caplog.text


class TestRedactTightenedPatterns:
    """Tightened Basic/Bearer patterns avoid prose while covering headers."""

    def test_bearer_token_masked(self) -> None:
        """Bearer tokens are masked."""
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.c29tZXBheWxvYWQ.c2lnbmF0dXJl"
        text = f"Authorization: Bearer {token}"
        result = redact_credentials(text)
        assert token not in result
        assert "[REDACTED]" in result

    def test_plain_english_untouched(self) -> None:
        """Plain English mentioning bearer/basic is left alone."""
        text = "The bearer of bad news arrived yesterday with a Basic understanding of the system"
        assert redact_credentials(text) == text

    def test_bare_basic_prose_untouched(self) -> None:
        """Bare Basic prose without an Authorization prefix is not redacted."""
        text = "This is a Basic understanding of the configuration format"
        assert redact_credentials(text) == text

    def test_short_bearer_fragment_untouched(self) -> None:
        """Short Bearer fragments below the token floor are not redacted."""
        text = "Bearer of light"
        assert redact_credentials(text) == text

    def test_short_basic_auth_header_masked(self) -> None:
        """Short Basic auth values are redacted when prefixed by Authorization."""
        text = "AUTHORIZATION: Basic YTpi"
        result = redact_credentials(text)
        assert "YTpi" not in result
        assert "Basic [REDACTED]" in result

    def test_short_bearer_token_masked(self) -> None:
        """Short bearer access tokens are redacted."""
        text = "Authorization: Bearer ghp_ab"
        result = redact_credentials(text)
        assert "ghp_ab" not in result
        assert "[REDACTED]" in result


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_cache_normalizes_repo_url() -> None:
    """Variant URL spellings share one cache entry; raw URL still goes to git."""
    fake_sha = "e" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        sha1 = await fetch_remote_head("https://github.com/org/repo.git", "main")
        assert sha1 == fake_sha
        assert mock_run.call_count == 1
        raw_argv = mock_run.call_args[0][0]
        assert raw_argv[3] == "https://github.com/org/repo.git"

        sha2 = await fetch_remote_head("https://GitHub.com/org/repo", "main")
        assert sha2 == fake_sha
        assert mock_run.call_count == 1


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_redacts_tokens_straddling_stderr_truncation() -> None:
    """Tokens straddling the 500-char cut are masked (redact before truncate)."""
    secret = "ghp_straddlingsecret123456"
    filler = "E" * 470
    stderr = f"{filler} https://x-access-token:{secret}@github.com/org/repo not found"
    assert "@" not in stderr[:500]
    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        result = MagicMock()
        result.returncode = 128
        result.stderr = stderr
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "repo")
            with pytest.raises(RuntimeError) as exc_info:
                await clone_repo("https://github.com/bad/repo.git", "main", dest)

        assert secret not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_stores_fresh_timestamp() -> None:
    """Cache entries use the post-subprocess clock reading, not the stale one."""
    fake_sha = "d" * 40
    _REMOTE_HEAD_CACHE.clear()
    _REMOTE_HEAD_NEG_CACHE.clear()
    with (
        patch("apme_gateway.scan.driver.time.monotonic", side_effect=[100.0, 200.0]),
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f"{fake_sha}\trefs/heads/main\n"
        mock_run.return_value = result
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _exec, func: func())

        sha = await fetch_remote_head("https://github.com/org/fresh.git", "main")
        assert sha == fake_sha

    assert len(_REMOTE_HEAD_CACHE) == 1
    stored_ts = next(iter(_REMOTE_HEAD_CACHE.values()))[0]
    assert stored_ts == 200.0
