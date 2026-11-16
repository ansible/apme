"""Bounded Git snapshots with connection-time destination pinning."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import signal
import socket
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_MAX_STORAGE = 512 * 1024 * 1024
_MAX_INODES = 65536


def _storage_root() -> Path:
    """Require an explicitly configured tmpfs with kernel-enforced byte and inode limits.

    Returns:
        Validated mount root for all concurrent Git fetches.

    Raises:
        RuntimeError: The configured filesystem cannot enforce the storage budget.
    """
    raw = os.environ.get("APME_GIT_FETCH_ROOT", "")
    if not raw:
        raise RuntimeError("Configure APME_GIT_FETCH_ROOT as a bounded tmpfs before fetching Git requirements")
    try:
        root = Path(raw).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Git fetch storage is unavailable") from exc
    mounted = False
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields, filesystem = line.split(" - ", 1)
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields.split()[4])
        if Path(mount) == root and filesystem.split()[0] == "tmpfs":
            mounted = True
    stats = os.statvfs(root)
    if not mounted or not 0 < stats.f_blocks * stats.f_frsize <= _MAX_STORAGE or not 0 < stats.f_files <= _MAX_INODES:
        raise RuntimeError("Git fetch storage must be a tmpfs mount capped at 512 MiB and 65536 inodes")
    return root


async def _destination(url: str) -> tuple[str, str]:
    """Validate every DNS answer and pin one allowed address for libcurl.

    Args:
        url: HTTPS Git URL without userinfo.

    Returns:
        Canonical URL and libcurl resolve entry.

    Raises:
        ValueError: URL, policy or network destination is disallowed.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Git fetch requires an HTTPS URL without embedded credentials or fragments")
    if "%" in parsed.hostname:
        raise ValueError("Scoped Git addresses are not allowed")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port or 443
    if not 0 < port < 65536:
        raise ValueError("Invalid Git HTTPS port")
    networks = [
        ipaddress.ip_network(c.strip()) for c in os.environ.get("APME_GIT_PRIVATE_CIDRS", "").split(",") if c.strip()
    ]
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("Git hostname has no usable addresses")
    addresses = []
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        prohibited = address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
        if prohibited or not (address.is_global or any(address in network for network in networks)):
            raise ValueError("Git destination is restricted by deployment network policy")
        addresses.append(address)
    address_text = str(addresses[0])
    if ":" in address_text:
        address_text = f"[{address_text}]"
    authority = f"[{host}]" if ":" in host else host
    canonical = urlunsplit(("https", f"{authority}:{port}", parsed.path, parsed.query, ""))
    return canonical, f"{authority}:{port}:{address_text}"


async def _run(command: list[str], env: dict[str, str]) -> None:
    """Run Git within a finite deadline and reap its process group on cancellation.

    Args:
        command: Git argv.
        env: Sanitized child environment.

    Raises:
        RuntimeError: Git failed or exceeded its deadline.
        asyncio.CancelledError: Caller canceled after process cleanup.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(process.wait(), 120)
    except (asyncio.CancelledError, TimeoutError) as exc:
        canceled = await _drain(asyncio.create_task(_terminate(process)))
        if canceled or isinstance(exc, asyncio.CancelledError):
            raise asyncio.CancelledError from exc
        raise RuntimeError("Git fetch timed out") from exc
    if process.returncode:
        raise RuntimeError("Git fetch failed; check repository access, version and fetch storage capacity")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop and reap a process and kill any remaining process-group descendants.

    Args:
        process: Child with its own process group.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        pass
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def _drain(task: asyncio.Task[None]) -> bool:
    """Finish resource cleanup even if the caller is canceled repeatedly.

    Args:
        task: Independently owned cleanup task.

    Returns:
        Whether caller cancellation was deferred until cleanup completed.
    """
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
    task.result()
    return canceled


@asynccontextmanager
async def git_snapshot(
    url: str,
    ref: str,
    token: str | None = None,
    *,
    scm_provider: str | None = None,
    url_userpass: tuple[str, str] | None = None,
) -> AsyncIterator[Path]:
    """Fetch a disposable snapshot inside an enforced shared storage budget.

    Args:
        url: HTTPS repository URL.
        ref: Branch, tag, commit or HEAD.
        token: Optional origin-scoped credential.
        scm_provider: Authentication provider.
        url_userpass: Optional origin-scoped project URL credentials.

    Yields:
        Path: Checkout available until context exit.

    Raises:
        ValueError: Invalid ref or disallowed destination.
        RuntimeError: Missing storage isolation, unsupported Git, or failed fetch.
        asyncio.CancelledError: Caller canceled after checkout cleanup.
    """
    from apme_gateway.scan.driver import _apply_git_auth_env, _git_subprocess_env

    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", ref) or ".." in ref:
        raise ValueError("Invalid Git requirement version")
    root = await asyncio.to_thread(_storage_root)
    canonical, resolve = await _destination(url)
    env = {
        k: v
        for k, v in _git_subprocess_env().items()
        if not k.startswith("GIT_") and not k.lower().endswith("_proxy") and k != "SSH_ASKPASS"
    }
    # Keep custom CA trust, but never ambient Git config, proxies, helpers or TLS bypasses.
    ca = _git_subprocess_env().get("GIT_SSL_CAINFO")
    if ca:
        env["GIT_SSL_CAINFO"] = ca
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false"
    )
    support = await asyncio.create_subprocess_exec(
        "git", "help", "--config", env=env, stdout=asyncio.subprocess.PIPE, start_new_session=True
    )
    try:
        output, _ = await asyncio.wait_for(support.communicate(), 10)
    finally:
        if support.returncode is None and await _drain(asyncio.create_task(_terminate(support))):
            raise asyncio.CancelledError
    if support.returncode or b"http.curloptResolve\n" not in output:
        raise RuntimeError("Git must support http.curloptResolve for destination enforcement")
    creation = asyncio.create_task(asyncio.to_thread(tempfile.TemporaryDirectory, prefix="apme_git_", dir=root))
    try:
        temporary = await asyncio.shield(creation)
    except asyncio.CancelledError:

        async def cleanup_created() -> None:
            directory = await creation
            await asyncio.to_thread(directory.cleanup)

        await _drain(asyncio.create_task(cleanup_created()))
        raise
    checkout = temporary.name
    try:
        env.update(TMPDIR=checkout, HOME=checkout, XDG_CONFIG_HOME=checkout)
        env = _apply_git_auth_env(env, canonical, token, url_userpass, scm_provider=scm_provider)
        prefix = [
            "git",
            "-c",
            "http.proxy=",
            "-c",
            "http.followRedirects=false",
            "-c",
            f"http.curloptResolve={resolve}",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        await _run([*prefix, "init", "--template=", checkout], env)
        await _run([*prefix, "-C", checkout, "fetch", "--depth=1", "--", canonical, ref], env)
        await _run([*prefix, "-C", checkout, "checkout", "--detach", "FETCH_HEAD"], env)
        yield Path(checkout)
    finally:
        if await _drain(asyncio.create_task(asyncio.to_thread(temporary.cleanup))):
            raise asyncio.CancelledError
