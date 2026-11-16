"""Security boundaries for Gateway Git snapshots."""

import asyncio
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apme_gateway.scm import safe_fetch


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1"])  # type: ignore[untyped-decorator]
async def test_private_destinations_rejected(address: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS answers cannot bypass the destination policy.

    Args:
        address: Restricted address.
        monkeypatch: DNS override.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "")
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(0, 0, 0, "", (address, 443))])
    )
    with pytest.raises(ValueError, match="restricted"):
        await safe_fetch._destination("https://git.example/repo")


async def test_operator_private_allowlist_and_pinned_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit private CIDR authorizes a pinned connection address.

    Args:
        monkeypatch: DNS and deployment configuration overrides.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "10.20.30.40/32")
    lookup = AsyncMock(return_value=[(socket.AF_INET, 0, 0, "", ("10.20.30.40", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    url, pin = await safe_fetch._destination("https://git.example/repo")
    assert url == "https://git.example:443/repo"
    assert pin == "git.example:443:10.20.30.40"
    lookup.assert_awaited_once()


async def test_mixed_dns_answers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public answer cannot hide a restricted answer.

    Args:
        monkeypatch: DNS overrides.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "")
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=[(0, 0, 0, "", ("8.8.8.8", 443)), (0, 0, 0, "", ("127.0.0.1", 443))]),
    )
    with pytest.raises(ValueError, match="restricted"):
        await safe_fetch._destination("https://git.example/repo")


def test_storage_requires_enforced_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary directory cannot masquerade as a storage quota.

    Args:
        tmp_path: Ordinary disk directory.
        monkeypatch: Deployment setting.
    """
    monkeypatch.setenv("APME_GIT_FETCH_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="tmpfs"):
        safe_fetch._storage_root()


@pytest.mark.parametrize(
    "size,inodes,accepted", [(512 * 1024 * 1024, 8192, True), (1024 * 1024 * 1024, 8192, False), (1024, 1000000, False)]
)  # type: ignore[untyped-decorator]
def test_kernel_byte_and_inode_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int, inodes: int, accepted: bool
) -> None:
    """Both byte and inode budgets must be enforced across concurrent fetches.

    Args:
        tmp_path: Mock mount root.
        monkeypatch: Mount table and stat overrides.
        size: Filesystem capacity in bytes.
        inodes: Filesystem inode limit.
        accepted: Whether the configuration is bounded.
    """
    monkeypatch.setenv("APME_GIT_FETCH_ROOT", str(tmp_path))
    monkeypatch.setattr(Path, "read_text", lambda _: f"1 0 0:1 / {tmp_path} rw - tmpfs tmpfs rw")
    monkeypatch.setattr(os, "statvfs", lambda _: SimpleNamespace(f_blocks=size, f_frsize=1, f_files=inodes))
    if accepted:
        assert safe_fetch._storage_root() == tmp_path
    else:
        with pytest.raises(RuntimeError, match="capped"):
            safe_fetch._storage_root()


async def test_fetch_scrubs_environment_and_preserves_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git cannot bypass pins through proxies or ambient config; checkout stays bounded.

    Args:
        tmp_path: Fake enforced filesystem.
        monkeypatch: Process and policy overrides.
    """
    monkeypatch.setattr(safe_fetch, "_storage_root", lambda: tmp_path)
    monkeypatch.setattr(
        safe_fetch, "_destination", AsyncMock(return_value=("https://git.example:443/r", "git.example:443:8.8.8.8"))
    )
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.proxy")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "9")
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "true")
    monkeypatch.setenv("SSH_ASKPASS", "/untrusted/helper")
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"http.curloptResolve\n", None)))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    commands = []

    async def run(command: list[str], env: dict[str, str]) -> None:
        assert "HTTPS_PROXY" not in env
        assert "GIT_SSL_NO_VERIFY" not in env
        assert "SSH_ASKPASS" not in env
        assert env["GIT_ASKPASS"] == "/bin/false"
        assert "http.followRedirects=false" in command
        assert "http.curloptResolve=git.example:443:8.8.8.8" in command
        assert Path(env["TMPDIR"]).is_relative_to(tmp_path)
        commands.append(command)

    monkeypatch.setattr(safe_fetch, "_run", run)
    async with safe_fetch.git_snapshot("https://git.example/r", "HEAD") as checkout:
        assert checkout.is_dir()
        assert len(commands) == 3
    assert not checkout.exists()


@pytest.mark.parametrize("redirect", ["/redirected", "https://restricted.example/repo"])  # type: ignore[untyped-decorator]
async def test_real_git_pins_tls_connection_and_refuses_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str
) -> None:
    """Real Git connects through the approved address and never follows redirects.

    Args:
        tmp_path: Test checkout filesystem.
        monkeypatch: Storage and DNS policy overrides.
        redirect: Same-host or cross-host redirection.
    """
    import ssl
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=pinned.example",
            "-addext",
            "subjectAltName=DNS:pinned.example",
        ],
        check=True,
        capture_output=True,
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requests.append(self.path)
            self.send_response(302)
            self.send_header("Location", redirect)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_port
    monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
    monkeypatch.setattr(safe_fetch, "_storage_root", lambda: tmp_path)
    # pinned.example has no DNS entry; Git must use the numeric address below
    # while retaining the hostname for certificate verification.
    monkeypatch.setattr(
        safe_fetch,
        "_destination",
        AsyncMock(return_value=(f"https://pinned.example:{port}/repo", f"pinned.example:{port}:127.0.0.1")),
    )
    try:
        with pytest.raises(RuntimeError, match="Git fetch failed"):
            async with safe_fetch.git_snapshot("https://pinned.example/repo", "HEAD"):
                pytest.fail("Redirect must not be followed")
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        worker.join()
    assert len(requests) == 1
    assert requests[0].startswith("/repo/info/refs")
    assert not list(tmp_path.glob("apme_git_*"))


def test_ipv6_credential_origin_preserves_brackets() -> None:
    """Git can match the authenticated origin of a private IPv6 Git server."""
    from apme_gateway.scan.driver import _apply_git_auth_env

    env = _apply_git_auth_env({}, "https://[fd00:1234::10]:443/repo", "token", None)
    keys = [value for key, value in env.items() if key.startswith("GIT_CONFIG_KEY_")]
    assert "http.https://[fd00:1234::10]:443.extraHeader" in keys


async def test_cancel_reaps_leader_and_kills_surviving_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader exiting on TERM cannot leave a remote helper holding quota space.

    Args:
        monkeypatch: Controlled process lifecycle.
    """
    import signal

    started = asyncio.Event()
    terminated = asyncio.Event()
    process = SimpleNamespace(pid=12345, returncode=None)
    signals = []

    async def wait() -> int:
        started.set()
        await terminated.wait()
        process.returncode = -15
        return -15

    def killpg(pid: int, sig: int) -> None:
        signals.append(sig)
        terminated.set()

    process.wait = wait
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(os, "killpg", killpg)
    task = asyncio.create_task(safe_fetch._run(["git"], {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode == -15
