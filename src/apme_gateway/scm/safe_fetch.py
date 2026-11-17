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


def _open_storage() -> int:
    """Hold and revalidate the actual filesystem used for checkout creation.

    Returns:
        Open descriptor pinning a bounded tmpfs mount.

    Raises:
        RuntimeError: Mount changed or no longer enforces the limits.
        BaseException: Validation failed; the opened descriptor is closed before propagation.
    """
    root = _storage_root()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = Path(f"/proc/self/fdinfo/{descriptor}").read_text()
        mount_id = next(
            (line.partition(":")[2].strip() for line in info.splitlines() if line.startswith("mnt_id:")), ""
        )
        if not mount_id.isdecimal():
            raise RuntimeError("Git fetch storage changed or lost its kernel quota")
        entries = Path("/proc/self/mountinfo").read_text().splitlines()
        matching = [line for line in entries if line.split()[0] == mount_id]
        stats = os.fstatvfs(descriptor)
        if (
            not matching
            or matching[0].split(" - ")[1].split()[0] != "tmpfs"
            or not (0 < stats.f_blocks * stats.f_frsize <= _MAX_STORAGE and 0 < stats.f_files <= _MAX_INODES)
        ):
            raise RuntimeError("Git fetch storage changed or lost its kernel quota")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _create_checkout() -> tuple[int, tempfile.TemporaryDirectory[str]]:
    """Allocate a checkout through a held mount, closing it on allocation failure.

    Returns:
        Mount descriptor and disposable operation directory.

    Raises:
        RuntimeError: Shared storage cannot allocate a checkout.
        BaseException: Allocation failed; the held descriptor is closed before propagation.
    """
    descriptor = _open_storage()
    try:
        directory = tempfile.TemporaryDirectory(prefix="apme_git_", dir=f"/proc/self/fd/{descriptor}")
        return descriptor, directory
    except BaseException as exc:
        os.close(descriptor)
        if isinstance(exc, OSError):
            raise RuntimeError(
                "Git fetch storage allocation failed; check shared quota and mount permissions"
            ) from None
        raise


async def _destination(url: str) -> tuple[str, str]:
    """Validate every DNS answer and pin allowed addresses for libcurl.

    Args:
        url: HTTPS Git URL without userinfo.

    Returns:
        Canonical URL and libcurl resolve entry.

    Raises:
        ValueError: URL, policy or network destination is disallowed.
        RuntimeError: Hostname resolution failed.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Git fetch requires an HTTPS URL without embedded credentials or fragments")
    if "%" in parsed.hostname:
        raise ValueError("Scoped Git addresses are not allowed")
    literal = False
    try:
        host = str(ipaddress.ip_address(parsed.hostname))
        literal = True
    except ValueError:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port or 443
    if not 0 < port < 65536:
        raise ValueError("Invalid Git HTTPS port")
    try:
        networks = [
            ipaddress.ip_network(c.strip())
            for c in os.environ.get("APME_GIT_PRIVATE_CIDRS", "").split(",")
            if c.strip()
        ]
    except ValueError:
        raise ValueError("Invalid APME_GIT_PRIVATE_CIDRS setting") from None
    if literal:
        resolved = [host]
    else:
        try:
            answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise RuntimeError("Git hostname resolution failed") from None
        resolved = [str(answer[4][0]) for answer in answers]
    if not resolved:
        raise ValueError("Git hostname has no usable addresses")
    addresses = []
    for resolved_address in resolved:
        address = ipaddress.ip_address(resolved_address)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        # Translation gateways can turn a globally routed IPv6 address into a
        # restricted IPv4 target. Reject standard translation/tunnel ranges.
        if isinstance(address, ipaddress.IPv6Address) and any(
            address in ipaddress.ip_network(prefix)
            for prefix in (
                "64:ff9b::/96",
                "64:ff9b:1::/48",
                "2002::/16",
                "2001::/32",
            )
        ):
            raise ValueError("Git translation and tunnel destinations are restricted")
        prohibited = address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
        site_local = isinstance(address, ipaddress.IPv6Address) and address.is_site_local
        if prohibited or not (
            (address.is_global and not site_local) or any(address in network for network in networks)
        ):
            raise ValueError("Git destination is restricted by deployment network policy")
        addresses.append(address)
    address_text = ",".join(f"[{a}]" if a.version == 6 else str(a) for a in dict.fromkeys(addresses))
    authority = f"[{host}]" if ":" in host else host
    canonical = urlunsplit(("https", f"{authority}:{port}", parsed.path, parsed.query, ""))
    # Numeric URLs already select their connection address. Older libcurl builds
    # cannot parse an IPv6 host in a resolve entry, even when Git supports the key.
    return canonical, "" if literal else f"{authority}:{port}:{address_text}"


async def _spawn(
    command: list[str],
    env: dict[str, str],
    *,
    stdout: int = asyncio.subprocess.DEVNULL,
    stderr: int = asyncio.subprocess.DEVNULL,
    storage_fd: int | None = None,
) -> asyncio.subprocess.Process:
    """Own subprocess creation through cancellation before returning its handle.

    Args:
        command: Executable and arguments.
        env: Child environment.
        stdout: Standard output destination.
        stderr: Standard error destination.
        storage_fd: Optional bounded mount descriptor inherited by Git.

    Returns:
        Started child process.

    Raises:
        asyncio.CancelledError: Caller canceled after spawned child cleanup.
        RuntimeError: Git could not be started.
    """
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            pass_fds=() if storage_fd is None else (storage_fd,),
        )
    )
    try:
        return await asyncio.shield(creation)
    except OSError:
        raise RuntimeError("Git process could not be started") from None
    except asyncio.CancelledError:

        async def cleanup_created() -> None:
            try:
                process = await creation
            except OSError:
                return

            async def discard(stream: asyncio.StreamReader) -> None:
                while await stream.read(65536):
                    pass

            readers = [
                asyncio.create_task(discard(stream))
                for stream in (process.stdout, process.stderr)
                if stream is not None
            ]
            try:
                await _terminate(process)
            finally:
                # An already exited child can still have a paused pipe transport.
                # Let readers consume buffered data and EOF before closing handles.
                try:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(asyncio.gather(*readers, return_exceptions=True), 1)
                finally:
                    for reader in readers:
                        reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
                    # Process exposes no public close API for inherited pipe handles.
                    transport = getattr(process, "_transport", None)
                    if transport is not None:
                        transport.close()

        await _drain(asyncio.create_task(cleanup_created()))
        raise


async def _run(command: list[str], env: dict[str, str], *, storage_fd: int | None = None) -> None:
    """Run Git with bounded diagnostic capture and cancellation-safe cleanup.

    Args:
        command: Git argv.
        env: Sanitized child environment.
        storage_fd: Optional bounded mount descriptor inherited by Git.

    Raises:
        RuntimeError: Git failed or exceeded its deadline.
        asyncio.CancelledError: Caller canceled after process cleanup.
    """
    process = await _spawn(command, env, stderr=asyncio.subprocess.PIPE, storage_fd=storage_fd)
    diagnostic = bytearray()

    async def read_errors() -> None:
        if process.stderr is not None:
            while data := await process.stderr.read(4096):
                diagnostic.extend(data[: max(0, 8192 - len(diagnostic))])

    reader = asyncio.create_task(read_errors())
    try:
        await asyncio.wait_for(process.wait(), 120)
    except (asyncio.CancelledError, TimeoutError) as exc:
        canceled = await _drain(asyncio.create_task(_terminate(process)))
        if canceled or isinstance(exc, asyncio.CancelledError):
            raise asyncio.CancelledError from exc
        raise RuntimeError("Git fetch timed out") from exc
    finally:
        # Do not wait forever if a descendant inherited stderr after its leader exited.
        async def close_reader() -> None:
            try:
                # An exited child can leave buffered diagnostics or a descendant
                # holding the pipe. Drain to EOF, with a finite cleanup deadline.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(reader), 1)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
                transport = getattr(process, "_transport", None)
                if transport is not None:
                    transport.close()

        if await _drain(asyncio.create_task(close_reader())):
            raise asyncio.CancelledError
    if process.returncode:
        stage = next((part for part in ("init", "fetch", "checkout", "clone") if part in command), "git")
        lowered = bytes(diagnostic).lower()
        categories = (
            (b"no space left", "storage quota exhausted"),
            (b"authentication failed", "authentication failed"),
            (b"certificate", "TLS certificate verification failed"),
            (b"couldn't find remote ref", "ref not found"),
            (b"repository not found", "repository not found"),
            (b"failed to connect", "connection failed"),
        )
        reason = next(
            (message for pattern, message in categories if pattern in lowered),
            "check repository access, version and fetch storage capacity",
        )
        raise RuntimeError(f"Git fetch failed (stage {stage}, exit {process.returncode}): {reason}")


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
    support = await _spawn(["git", "help", "--config"], env, stdout=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(support.communicate(), 10)
    except TimeoutError:
        raise RuntimeError("Git capability probe timed out") from None
    finally:
        if support.returncode is None and await _drain(asyncio.create_task(_terminate(support))):
            raise asyncio.CancelledError
    if support.returncode or b"http.curloptResolve\n" not in output:
        raise RuntimeError("Git must support http.curloptResolve for destination enforcement")
    # Create relative to an open, revalidated mount, not its replaceable name.
    # /proc/self/fd resolves the held mount in both Python and Git children.
    creation = asyncio.create_task(asyncio.to_thread(_create_checkout))
    try:
        descriptor, temporary = await asyncio.shield(creation)
    except OSError:
        raise RuntimeError("Git fetch storage is unavailable") from None
    except asyncio.CancelledError:

        async def cleanup_created() -> None:
            try:
                held_fd, directory = await creation
            except Exception:
                # The allocator closes partial resources itself. Preserve cancellation
                # even when mount validation or allocation fails in the worker.
                return
            try:
                await asyncio.to_thread(directory.cleanup)
            finally:
                os.close(held_fd)

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
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        if resolve:
            prefix.extend(["-c", f"http.curloptResolve={resolve}"])
        await _run([*prefix, "init", "--template=", checkout], env, storage_fd=descriptor)
        await _run([*prefix, "-C", checkout, "fetch", "--depth=1", "--", canonical, ref], env, storage_fd=descriptor)
        await _run([*prefix, "-C", checkout, "checkout", "--detach", "FETCH_HEAD"], env, storage_fd=descriptor)
        # A consumer may pass this Path to a child without inheriting our fd.
        yield Path(f"/proc/{os.getpid()}/fd/{descriptor}") / Path(checkout).name
    finally:
        try:
            if await _drain(asyncio.create_task(asyncio.to_thread(temporary.cleanup))):
                raise asyncio.CancelledError
        finally:
            os.close(descriptor)
