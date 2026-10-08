"""Trust-boundary tests for repo URL SSRF validation and token scoping (N1)."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme_gateway.scan import driver as driver_mod
from apme_gateway.scan.repo_url import (
    get_scm_allowed_hosts,
    is_global_token_allowed_for_host,
    resolve_scm_token,
    validate_repo_url,
)


def _public_ip(monkeypatch: pytest.MonkeyPatch, ip: str = "140.82.113.4") -> None:
    """Patch DNS to resolve any host to a public IP.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        ip: Public IP string to return from ``getaddrinfo``.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)),
        ],
    )


def test_validate_repo_url_rejects_loopback_literal() -> None:
    """Loopback literal IPs are rejected."""
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://127.0.0.1/org/repo.git")


def test_validate_repo_url_rejects_private_literal() -> None:
    """RFC 1918 private literals are rejected."""
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://192.168.1.10/org/repo.git")


def test_validate_repo_url_rejects_metadata_ip() -> None:
    """Cloud metadata IP is explicitly blocked."""
    with pytest.raises(ValueError, match="blocked"):
        validate_repo_url("https://169.254.169.254/latest/meta-data/")


def test_validate_repo_url_rejects_userinfo() -> None:
    """Embedded userinfo is rejected by the validator."""
    with pytest.raises(ValueError, match="userinfo"):
        validate_repo_url("https://user:pass@github.com/org/repo.git")


def test_validate_repo_url_rejects_nonstandard_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-443 ports are rejected.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _public_ip(monkeypatch)
    with pytest.raises(ValueError, match="non-standard port"):
        validate_repo_url("https://github.com:8443/org/repo.git")


def test_validate_repo_url_rejects_non_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-HTTPS schemes are rejected.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _public_ip(monkeypatch)
    with pytest.raises(ValueError, match="Only https"):
        validate_repo_url("ssh://git@github.com/org/repo.git")


def test_validate_repo_url_resolves_dns_and_blocks_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS-resolved private IPs are rejected (DNS rebinding guard).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://evil.example.com/org/repo.git")


def test_validate_repo_url_allows_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public hosts pass validation.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _public_ip(monkeypatch)
    assert validate_repo_url("https://github.com/org/repo.git") == "https://github.com/org/repo.git"


def test_validate_repo_url_rejects_ipv4_mapped_private_literal() -> None:
    """IPv4-mapped IPv6 literals unwrap to the embedded private IPv4."""
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://[::ffff:192.168.1.10]/org/repo.git")


def test_validate_repo_url_rejects_ipv4_mapped_loopback_literal() -> None:
    """IPv4-mapped loopback cannot bypass the filter."""
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://[::ffff:127.0.0.1]/org/repo.git")


def test_validate_repo_url_allow_private_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """APME_SSRF_ALLOW_PRIVATE=1 permits RFC 1918 literals (on-prem forges).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SSRF_ALLOW_PRIVATE", "1")
    assert validate_repo_url("https://192.168.1.10/org/repo.git") == "https://192.168.1.10/org/repo.git"
    # Opt-in covers private ranges only — loopback/metadata still blocked.
    with pytest.raises(ValueError, match="blocked"):
        validate_repo_url("https://127.0.0.1/org/repo.git")
    with pytest.raises(ValueError, match="blocked"):
        validate_repo_url("https://169.254.169.254/latest/meta-data/")


def test_validate_repo_url_blocks_private_by_default_for_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS-resolved private IPs stay blocked unless explicitly opted in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_SSRF_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("::ffff:10.0.0.5", 443)),
        ],
    )
    with pytest.raises(ValueError, match="blocked non-public IP"):
        validate_repo_url("https://evil.example.com/org/repo.git")


async def test_validate_repo_url_async_offloads_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Async validator applies the same trust boundary via run_in_executor.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.scan.repo_url import validate_repo_url_async

    _public_ip(monkeypatch)
    assert await validate_repo_url_async("https://github.com/org/repo.git") == "https://github.com/org/repo.git"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )
    with pytest.raises(ValueError, match="blocked non-public IP"):
        await validate_repo_url_async("https://evil.example.com/org/repo.git")


def test_global_token_allowlist_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default allowlist covers the three cloud forges.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_SCM_ALLOWED_HOSTS", raising=False)
    assert is_global_token_allowed_for_host("github.com")
    assert is_global_token_allowed_for_host("gitlab.com")
    assert is_global_token_allowed_for_host("bitbucket.org")
    assert not is_global_token_allowed_for_host("evil.com")


def test_global_token_allowlist_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-hosted hosts opt in via APME_SCM_ALLOWED_HOSTS.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SCM_ALLOWED_HOSTS", "git.example.com, hub.internal")
    hosts = get_scm_allowed_hosts()
    assert "git.example.com" in hosts
    assert "hub.internal" in hosts
    assert is_global_token_allowed_for_host("git.example.com")


def test_resolve_scm_token_prefers_project_token() -> None:
    """Per-project tokens are honored for any host."""
    assert resolve_scm_token("https://evil.com/r.git", "proj-tok", "glob-tok") == "proj-tok"


def test_resolve_scm_token_allows_global_on_allowlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global fallback is returned for allowlisted hosts.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_SCM_ALLOWED_HOSTS", raising=False)
    _public_ip(monkeypatch)
    assert resolve_scm_token("https://github.com/o/r.git", None, "glob-tok") == "glob-tok"


def test_resolve_scm_token_fails_closed_for_attacker_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global fallback for attacker hosts fails closed with a clear error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_SCM_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError, match="per-project scm_token"):
        resolve_scm_token("https://evil.com/o/r.git", None, "glob-tok")


async def test_clone_repo_withholds_global_token_from_attacker_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global tokens are never sent to non-allowlisted hosts (fail closed).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SCM_TOKEN", "glob-secret")
    monkeypatch.delenv("APME_SCM_ALLOWED_HOSTS", raising=False)
    _public_ip(monkeypatch)
    with (
        patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop,
        patch("apme_gateway.scan.driver.subprocess.run") as mock_run,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda _e, f: f())
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        import tempfile

        with tempfile.TemporaryDirectory() as td, pytest.raises(ValueError, match="Global SCM token is not allowed"):
            await driver_mod.clone_repo(
                "https://evil.com/org/repo.git",
                "main",
                f"{td}/repo",
                scm_token="glob-secret",
            )
        assert mock_run.call_count == 0


async def test_fetch_remote_head_withholds_global_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ls-remote to attacker hosts fails closed, never probing unauthenticated.

    Matches :func:`clone_repo`: a global token for a non-allowlisted host
    raises instead of silently dropping the credential and probing
    unauthenticated.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SCM_TOKEN", "glob-secret")
    monkeypatch.delenv("APME_SCM_ALLOWED_HOSTS", raising=False)
    _public_ip(monkeypatch)
    driver_mod._REMOTE_HEAD_CACHE.clear()
    driver_mod._REMOTE_HEAD_NEG_CACHE.clear()

    with patch("apme_gateway.scan.driver.asyncio.get_running_loop") as mock_loop:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "a" * 40 + "\trefs/heads/main\n"
        mock_loop.return_value.run_in_executor = AsyncMock(
            side_effect=lambda _e, f: f(),
        )
        with patch(
            "apme_gateway.scan.driver.subprocess.run",
            return_value=mock_process,
        ) as mock_run:
            with pytest.raises(ValueError, match="Global SCM token is not allowed"):
                await driver_mod.fetch_remote_head(
                    "https://evil.com/org/repo.git",
                    "main",
                    scm_token="glob-secret",
                )
            assert mock_run.call_count == 0


def test_check_resolved_ips_rejects_empty_sockaddrs() -> None:
    """Non-empty infos with no usable IPs fail closed (no fail-open pass)."""
    from apme_gateway.scan.repo_url import _check_resolved_ips

    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("", 443))]
    with pytest.raises(ValueError, match="failed DNS resolution"):
        _check_resolved_ips(infos, "https://example.com/org/repo.git")


def test_check_resolved_ips_rejects_short_tuples() -> None:
    """Truncated getaddrinfo tuples (no sockaddr) fail closed."""
    from apme_gateway.scan.repo_url import _check_resolved_ips

    with pytest.raises(ValueError, match="failed DNS resolution"):
        _check_resolved_ips(
            [(socket.AF_INET, socket.SOCK_STREAM)],
            "https://example.com/org/repo.git",
        )


def test_check_resolved_ips_rejects_unparseable_ips() -> None:
    """Unparseable IP strings across all entries fail closed."""
    from apme_gateway.scan.repo_url import _check_resolved_ips

    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 443))]
    with pytest.raises(ValueError, match="failed DNS resolution"):
        _check_resolved_ips(infos, "https://example.com/org/repo.git")


def test_check_resolved_ips_empty_list_fails() -> None:
    """An empty infos list still fails (regression guard for the fail-open fix)."""
    from apme_gateway.scan.repo_url import _check_resolved_ips

    with pytest.raises(ValueError, match="failed DNS resolution"):
        _check_resolved_ips([], "https://example.com/org/repo.git")


def test_validate_repo_url_rejects_unusable_dns_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: getaddrinfo entries with empty IPs raise via the sync validator.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("", 443)),
        ],
    )
    with pytest.raises(ValueError, match="failed DNS resolution"):
        validate_repo_url("https://example.com/org/repo.git")


async def test_validate_repo_url_async_rejects_unusable_dns_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: unusable getaddrinfo entries raise via the async validator.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.scan.repo_url import validate_repo_url_async

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 443)),
        ],
    )
    with pytest.raises(ValueError, match="failed DNS resolution"):
        await validate_repo_url_async("https://example.com/org/repo.git")


async def test_revalidate_host_ip_literal_skips_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-literal hosts skip DNS re-resolution entirely.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    resolve_mock = AsyncMock(side_effect=AssertionError("DNS must not be called for IP literals"))
    monkeypatch.setattr(driver_mod, "_resolve_host_ips", resolve_mock)
    await driver_mod._revalidate_host_before_spawn("https://93.184.216.34/org/repo.git")
    assert resolve_mock.call_count == 0


async def test_revalidate_host_rebinding_to_blocked_ip_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rebinding to a loopback IP aborts with the wrapper message and cause chain.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        driver_mod,
        "_resolve_host_ips",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(ValueError, match="re-resolution failed trust boundary") as exc_info:
        await driver_mod._revalidate_host_before_spawn("https://github.com/org/repo.git")
    assert isinstance(exc_info.value.__cause__, ValueError)


async def test_revalidate_host_resolution_failure_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS resolution failures propagate chained to the underlying gaierror.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        MagicMock(side_effect=socket.gaierror("boom")),
    )
    with pytest.raises(ValueError, match="failed DNS resolution") as exc_info:
        await driver_mod._revalidate_host_before_spawn("https://github.com/org/repo.git")
    assert isinstance(exc_info.value.__cause__, socket.gaierror)


async def test_clone_repo_aborts_before_spawn_on_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    """clone_repo aborts before spawning git when re-resolution yields a blocked IP.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "apme_gateway.scan.repo_url.validate_repo_url_async",
        AsyncMock(return_value="https://github.com/org/repo.git"),
    )
    monkeypatch.setattr(
        driver_mod,
        "_resolve_host_ips",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
            ]
        ),
    )
    with patch("apme_gateway.scan.driver.subprocess.run") as mock_run:
        with pytest.raises(ValueError, match="Invalid repository URL"):
            await driver_mod.clone_repo(
                "https://github.com/org/repo.git",
                "main",
                "/tmp/apme-revalidate-clone-dest",
            )
        assert mock_run.call_count == 0


async def test_fetch_remote_head_aborts_before_spawn_on_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_remote_head returns None before spawning git on rebinding to a blocked IP.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    driver_mod._REMOTE_HEAD_CACHE.clear()
    driver_mod._REMOTE_HEAD_NEG_CACHE.clear()
    monkeypatch.setattr(
        "apme_gateway.scan.repo_url.validate_repo_url_async",
        AsyncMock(return_value="https://github.com/org/repo.git"),
    )
    monkeypatch.setattr(
        driver_mod,
        "_resolve_host_ips",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
            ]
        ),
    )
    with patch("apme_gateway.scan.driver.subprocess.run") as mock_run:
        assert await driver_mod.fetch_remote_head("https://github.com/org/repo.git", "main") is None
        assert mock_run.call_count == 0
