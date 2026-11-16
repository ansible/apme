"""Security boundaries for Gateway Git snapshots."""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apme_gateway.scm import safe_fetch


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "64:ff9b::a9fe:a9fe",
        "64:ff9b:1::7f00:1",
        "2002:7f00:1::",
        "2001::7f00:1",
    ],
)  # type: ignore[untyped-decorator]
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
    monkeypatch.setattr(safe_fetch, "_open_storage", lambda: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
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

    async def run(command: list[str], env: dict[str, str], *, storage_fd: int | None = None) -> None:
        assert "HTTPS_PROXY" not in env
        assert "GIT_SSL_NO_VERIFY" not in env
        assert "SSH_ASKPASS" not in env
        assert env["GIT_ASKPASS"] == "/bin/false"
        assert "http.followRedirects=false" in command
        assert "http.curloptResolve=git.example:443:8.8.8.8" in command
        assert Path(env["TMPDIR"]).resolve().is_relative_to(tmp_path)
        commands.append(command)

    monkeypatch.setattr(safe_fetch, "_run", run)
    async with safe_fetch.git_snapshot("https://git.example/r", "HEAD") as checkout:
        assert checkout.is_dir()
        assert len(commands) == 3
    assert not checkout.exists()


@pytest.mark.parametrize("literal", [False, True])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("redirect", ["/redirected", "https://restricted.example/repo"])  # type: ignore[untyped-decorator]
async def test_real_git_pins_tls_connection_and_refuses_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str, literal: bool
) -> None:
    """Real Git connects through the approved address and never follows redirects.

    Args:
        tmp_path: Test checkout filesystem.
        monkeypatch: Storage and DNS policy overrides.
        redirect: Same-host or cross-host redirection.
        literal: Use a numeric IPv6 URL without a DNS resolve override.

    Raises:
        OSError: Binding the required IPv4 listener failed.
    """
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    if not shutil.which("git") or not shutil.which("openssl"):
        pytest.skip("Real TLS test requires Git and OpenSSL")
    config = subprocess.run(["git", "help", "--config"], capture_output=True, check=True).stdout
    if b"http.curloptResolve\n" not in config:
        pytest.skip("Real TLS test requires Git http.curloptResolve support")
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
            "subjectAltName=DNS:pinned.example,IP:::1",
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

    class IPv6Server(HTTPServer):
        address_family = socket.AF_INET6

    try:
        server = IPv6Server(("::1", 0), Handler) if literal else HTTPServer(("127.0.0.1", 0), Handler)
    except OSError:
        if literal:
            pytest.skip("IPv6 loopback unavailable")
        raise
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_port
    monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
    monkeypatch.setattr(safe_fetch, "_open_storage", lambda: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    # pinned.example has no DNS entry; Git must use the numeric address below
    # while retaining the hostname for certificate verification.
    monkeypatch.setattr(
        safe_fetch,
        "_destination",
        AsyncMock(
            return_value=(f"https://[::1]:{port}/repo", "")
            if literal
            else (f"https://pinned.example:{port}/repo", f"pinned.example:{port}:127.0.0.1")
        ),
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
    process = SimpleNamespace(pid=12345, returncode=None, stderr=None)
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


async def test_invalid_private_cidr_names_setting_without_exposing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed operator configuration is actionable without leaking its value.

    Args:
        monkeypatch: Invalid deployment setting.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "secret-configuration")
    with pytest.raises(ValueError, match="^Invalid APME_GIT_PRIVATE_CIDRS setting$"):
        await safe_fetch._destination("https://git.example/repo")


async def test_private_ipv6_literal_is_canonicalized_and_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly authorized IPv6 literals retain the required URL and curl brackets.

    Args:
        monkeypatch: DNS and private address policy overrides.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "fd00:1234::10/128")
    lookup = AsyncMock(return_value=[(socket.AF_INET6, 0, 0, "", ("fd00:1234::10", 443, 0, 0))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    assert await safe_fetch._destination("https://[fd00:1234::10]/repo") == (
        "https://[fd00:1234::10]:443/repo",
        "",
    )
    lookup.assert_not_awaited()


async def test_rebinding_does_not_change_approved_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """An approved fetch keeps its numeric pin; subsequent restricted answers are rejected.

    Args:
        monkeypatch: DNS answers changing between requests.
    """
    monkeypatch.setenv("APME_GIT_PRIVATE_CIDRS", "")
    lookup = AsyncMock(side_effect=[[(0, 0, 0, "", ("8.8.8.8", 443))], [(0, 0, 0, "", ("127.0.0.1", 443))]])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    _, pin = await safe_fetch._destination("https://git.example/repo")
    with pytest.raises(ValueError, match="restricted"):
        await safe_fetch._destination("https://git.example/repo")
    assert pin == "git.example:443:8.8.8.8"
    assert lookup.await_count == 2


async def test_git_without_resolve_support_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing Git destination enforcement never falls back to unpinned fetching.

    Args:
        monkeypatch: Unsupported Git capability probe.
    """
    monkeypatch.setattr(safe_fetch, "_destination", AsyncMock(return_value=("https://git.example/repo", "pin")))
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"http.proxy\n", None)))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="must support http.curloptResolve"):
        async with safe_fetch.git_snapshot("https://git.example/repo", "HEAD"):
            pytest.fail("Unsupported Git must fail closed")
    spawn.assert_awaited_once()


@pytest.mark.parametrize("probe", [False, True])  # type: ignore[untyped-decorator]
async def test_cancel_during_process_creation_reaps_child(monkeypatch: pytest.MonkeyPatch, probe: bool) -> None:
    """Cancellation owns a child even before subprocess creation returns its handle.

    Args:
        monkeypatch: Delayed creation and controlled process lifecycle.
        probe: Exercise the capability probe as well as normal Git commands.
    """
    import signal

    created, release = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(pid=12345, returncode=None, stdout=None, stderr=None, wait=AsyncMock(return_value=-15))
    signals = []

    async def spawn(*args: object, **kwargs: object) -> SimpleNamespace:
        created.set()
        await release.wait()
        return process

    async def fetch() -> None:
        async with safe_fetch.git_snapshot("https://git.example/repo", "HEAD"):
            pytest.fail("Canceled creation must not yield a snapshot")

    monkeypatch.setattr(safe_fetch, "_destination", AsyncMock(return_value=("https://git.example/repo", "pin")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append(sig))
    task = asyncio.create_task(fetch() if probe else safe_fetch._run(["git"], {}))
    await created.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.wait.await_count >= 1


def test_replaced_storage_is_revalidated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disk directory substituted after validation cannot inherit tmpfs authorization.

    Args:
        tmp_path: Replacement ordinary filesystem.
        monkeypatch: Simulate a successful earlier pathname validation.
    """
    monkeypatch.setattr(safe_fetch, "_storage_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="changed or lost"):
        safe_fetch._open_storage()


def test_checkout_uses_held_mount_after_path_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing the mount name after opening cannot redirect checkout creation.

    Args:
        tmp_path: Original and replacement directories.
        monkeypatch: Simulated validated mount handle.
    """
    root, held = tmp_path / "root", tmp_path / "held"
    root.mkdir()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    root.rename(held)
    root.mkdir()
    monkeypatch.setattr(safe_fetch, "_open_storage", lambda: descriptor)
    fd, directory = safe_fetch._create_checkout()
    try:
        assert Path(directory.name).resolve().parent == held
        assert not list(root.iterdir())
    finally:
        directory.cleanup()
        os.close(fd)
    assert not list(held.iterdir())


async def test_failure_diagnostics_are_bounded_and_do_not_expose_stderr() -> None:
    """Large hostile stderr is drained but only a fixed actionable category escapes."""
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('Authentication failed SECRET\\n' + 'x' * 2000000); sys.exit(7)",
    ]
    with pytest.raises(RuntimeError, match=r"^Git fetch failed \(stage git, exit 7\): authentication failed$"):
        await safe_fetch._run(command, dict(os.environ))


def test_checkout_allocation_failure_closes_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exhausted shared metadata capacity produces a public fetch error without leaking a handle.

    Args:
        tmp_path: Simulated quota mount.
        monkeypatch: Exhausted allocation and validated descriptor.
    """
    from unittest.mock import Mock

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(safe_fetch, "_open_storage", lambda: descriptor)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", Mock(side_effect=OSError(28, "No space left")))
    with pytest.raises(RuntimeError, match="storage allocation failed"):
        safe_fetch._create_checkout()
    with pytest.raises(OSError):
        os.fstat(descriptor)


async def test_snapshot_path_works_in_child_without_inheriting_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public checkout path remains usable when a consumer starts an ordinary child.

    Args:
        tmp_path: Simulated quota mount.
        monkeypatch: Controlled Git setup and operation directory.
    """
    monkeypatch.setattr(safe_fetch, "_open_storage", lambda: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    monkeypatch.setattr(safe_fetch, "_destination", AsyncMock(return_value=("https://git.example/repo", "pin")))
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"http.curloptResolve\n", None)))
    monkeypatch.setattr(safe_fetch, "_spawn", AsyncMock(return_value=process))
    monkeypatch.setattr(safe_fetch, "_run", AsyncMock())
    async with safe_fetch.git_snapshot("https://git.example/repo", "HEAD") as checkout:
        (checkout / "content").write_text("snapshot")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())",
            str(checkout / "content"),
            stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await child.communicate()
        assert child.returncode == 0
        assert output == b"snapshot\n"
    assert not checkout.exists()


async def test_dns_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolver infrastructure errors honor the documented public failure contract.

    Args:
        monkeypatch: Failing resolver.
    """
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(side_effect=socket.gaierror("SECRET")))
    with pytest.raises(RuntimeError, match="^Git hostname resolution failed$"):
        await safe_fetch._destination("https://git.example/repo")


async def test_missing_git_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing executables produce a public fetch error without internal path details.

    Args:
        monkeypatch: Failed subprocess creation.
    """
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("SECRET")))
    with pytest.raises(RuntimeError, match="^Git process could not be started$"):
        await safe_fetch._run(["git"], {})


async def test_cancel_during_creation_drains_full_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child blocked on a full pipe is drained and reaped before cancellation completes.

    Args:
        monkeypatch: Hold a real child handle inside subprocess creation.
    """
    original_spawn = asyncio.create_subprocess_exec
    created, release = asyncio.Event(), asyncio.Event()
    children = []

    async def spawn(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original_spawn(*args, **kwargs)  # type: ignore[arg-type]
        children.append(process)
        await asyncio.sleep(0.1)
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        safe_fetch._run(
            [
                sys.executable,
                "-c",
                "import sys,time; sys.stderr.write('x' * 2000000); sys.stderr.flush(); time.sleep(60)",
            ],
            dict(os.environ),
        )
    )
    await created.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert children[0].returncode is not None
    assert children[0].stderr is not None
    assert children[0].stderr.at_eof()


async def test_cancel_during_reader_cleanup_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting for a canceled reader cannot swallow a fresh caller cancellation.

    Args:
        monkeypatch: Delayed diagnostic reader cleanup.
    """
    closing, release = asyncio.Event(), asyncio.Event()

    async def read(size: int) -> bytes:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            closing.set()
            await release.wait()
        return b""

    process = SimpleNamespace(returncode=0, stderr=SimpleNamespace(read=read), wait=AsyncMock(return_value=0))
    monkeypatch.setattr(safe_fetch, "_spawn", AsyncMock(return_value=process))
    task = asyncio.create_task(safe_fetch._run(["git"], {}))
    await closing.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_canceled_allocation_preserves_cancellation_on_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allocation worker failure after caller cancellation cannot replace cancellation.

    Args:
        monkeypatch: Delayed failing allocator and capability probe.
    """
    import threading

    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def allocate() -> None:
        loop.call_soon_threadsafe(started.set)
        release.wait()
        raise OSError("SECRET mount failure")

    async def fetch() -> None:
        async with safe_fetch.git_snapshot("https://git.example/repo", "HEAD"):
            pytest.fail("Canceled allocation must not yield")

    monkeypatch.setattr(safe_fetch, "_destination", AsyncMock(return_value=("https://git.example/repo", "pin")))
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"http.curloptResolve\n", None)))
    monkeypatch.setattr(safe_fetch, "_spawn", AsyncMock(return_value=process))
    monkeypatch.setattr(safe_fetch, "_create_checkout", allocate)
    task = asyncio.create_task(fetch())
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_creation_cancel_drains_pipe_of_already_exited_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exited leader cannot leave a paused pipe and buffered output behind.

    Args:
        monkeypatch: Hold the process handle until the real child has exited.
    """
    original_spawn = asyncio.create_subprocess_exec
    created, release = asyncio.Event(), asyncio.Event()
    children = []

    async def spawn(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original_spawn(*args, **kwargs)  # type: ignore[arg-type]
        children.append(process)
        async with asyncio.timeout(3):
            while process.returncode is None:
                await asyncio.sleep(0.01)
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        safe_fetch._run(
            [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'x' * 180000); sys.stderr.flush()"],
            dict(os.environ),
        )
    )
    await asyncio.wait_for(created.wait(), 4)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert children[0].returncode == 0
    assert children[0].stderr is not None
    assert children[0].stderr.at_eof()


@pytest.mark.parametrize("fdinfo", ["pos:\t0\n", "mnt_id:\n", "mnt_id:\tnot-a-number\n"])  # type: ignore[untyped-decorator]
def test_missing_mount_identity_fails_closed_and_closes_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fdinfo: str,
) -> None:
    """Missing or malformed kernel mount identity is a descriptive storage failure.

    Args:
        tmp_path: Mount pathname already checked by the caller.
        monkeypatch: Kernel metadata override.
        fdinfo: Missing, empty or invalid mount identity.
    """
    monkeypatch.setattr(safe_fetch, "_storage_root", lambda: tmp_path)
    original_read = Path.read_text
    descriptors = []

    def read(path: Path) -> str:
        if str(path).startswith("/proc/self/fdinfo/"):
            descriptors.append(int(path.name))
            return fdinfo
        return original_read(path)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(RuntimeError, match="storage changed or lost its kernel quota"):
        safe_fetch._open_storage()
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


async def test_failure_diagnostics_drain_after_process_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Late buffered stderr is classified before the public failure is constructed.

    Args:
        monkeypatch: Already exited child with delayed stderr delivery.
    """
    chunks = [b"fatal: Authentication failed SECRET\n", b""]

    async def read(size: int) -> bytes:
        await asyncio.sleep(0.01)
        return chunks.pop(0)

    process = SimpleNamespace(returncode=128, stderr=SimpleNamespace(read=read), wait=AsyncMock(return_value=128))
    monkeypatch.setattr(safe_fetch, "_spawn", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match=r"^Git fetch failed \(stage fetch, exit 128\): authentication failed$"):
        await safe_fetch._run(["git", "fetch"], {})
    assert not chunks


async def test_diagnostic_eof_wait_has_finite_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A descendant holding stderr cannot stall diagnostics or keep pipe handles open.

    Args:
        monkeypatch: Exited leader with a pipe that never reaches EOF.
    """
    from unittest.mock import Mock

    async def read(size: int) -> bytes:
        await asyncio.Event().wait()
        return b""

    transport = Mock()
    process = SimpleNamespace(
        returncode=128,
        stderr=SimpleNamespace(read=read),
        wait=AsyncMock(return_value=128),
        _transport=transport,
    )
    monkeypatch.setattr(safe_fetch, "_spawn", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="Git fetch failed"):
        await asyncio.wait_for(safe_fetch._run(["git", "fetch"], {}), 3)
    transport.close.assert_called_once()
