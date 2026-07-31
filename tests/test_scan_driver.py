"""Unit tests for the gateway project scan driver (ADR-037)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme.v1 import primary_pb2
from apme_gateway.scan.driver import (
    _REMOTE_HEAD_CACHE,
    _git_subprocess_env,
    _inject_token_in_url,
    _redact_credentials,
    clone_repo,
    coerce_option_bool,
    derive_session_id,
    fetch_remote_head,
    get_clone_head,
    run_project_scan,
)


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
    mock_chunks = [primary_pb2.ScanChunk(last=True, scan_id="test-scan")]

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
            "apme_gateway.scan.driver.primary_pb2_grpc.PrimaryStub",
            return_value=mock_stub,
        ):
            scan_id, result, commit_sha = await run_project_scan(
                project_id="test-proj",
                repo_url="https://github.com/test/repo.git",
                branch="main",
                primary_address="127.0.0.1:50051",
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
    """Tests for _redact_credentials helper."""

    def test_redacts_token_in_url(self) -> None:
        """Credentials in URLs are redacted."""
        text = "fatal: https://x-access-token:ghp_secret123@github.com/repo.git not found"
        result = _redact_credentials(text)
        assert "ghp_secret123" not in result
        assert "[REDACTED]" in result
        assert "github.com/repo.git" in result

    def test_redacts_multiple_urls(self) -> None:
        """Multiple URLs in text are all redacted."""
        text = "tried https://user:pass1@a.com and https://user:pass2@b.com"
        result = _redact_credentials(text)
        assert "pass1" not in result
        assert "pass2" not in result

    def test_preserves_urls_without_auth(self) -> None:
        """URLs without credentials are unchanged."""
        text = "clone https://github.com/public/repo.git"
        result = _redact_credentials(text)
        assert result == text


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_clone_repo_with_scm_token() -> None:
    """Verify clone_repo injects token into URL when provided."""
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
        assert "x-access-token:ghp_test@github.com" in call_args[-2]


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
async def test_fetch_remote_head_with_scm_token() -> None:
    """Verify fetch_remote_head injects token when provided."""
    fake_sha = "c" * 40
    _REMOTE_HEAD_CACHE.clear()
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
    assert "oauth2:glpat-test@gitlab.com" in call_args[3]


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_fetch_remote_head_cache_separates_auth_and_unauth() -> None:
    """Verify cache keys differentiate authenticated vs unauthenticated requests."""
    fake_sha_unauth = "a" * 40
    fake_sha_auth = "b" * 40
    _REMOTE_HEAD_CACHE.clear()

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
