"""Session-scoped venvs with multi-version layout and incremental installs.

Each session (identified by a client-provided ``session_id``) can hold
multiple venvs, one per ``ansible-core`` version — like tox matrix entries.
Collections are installed *incrementally* into the active core-version venv;
old core-version venvs are retained until TTL reaping.

Write authority / read-only consumers:
    The Engine orchestrator is the sole venv authority (calls ``acquire()``).
    Validators mount the sessions volume read-only and receive a ``venv_path``
    in ``ValidateRequest``.

Concurrency safety:
    Creation and mutation are serialised per session via ``fcntl.flock``
    on a ``.lock`` file inside the session directory.

Storage layout::

    $SESSIONS_ROOT/
        <session_id>/
            <core_version>/
                venv/             # the actual virtualenv
                meta.json         # installed_collections, timestamps
            session.json          # session-level metadata (created_at, last_used)
            .lock                 # flock target
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import NamedTuple

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from apme_engine.cache import BoundedCache
from apme_engine.config_env import get_env_int
from apme_engine.venv_manager.venv_collections import (
    _SAFE_VERSION_RE,
    _installed_satisfies_spec,
    _spec_to_pip,
    enforce_sibling_cap,
)
from apme_engine.venv_manager.venv_collections import (
    _merge_installed_collections as _merge_installed_collections,
)
from apme_engine.venv_manager.venv_collections import (
    _requirements_hash as _requirements_hash,
)

logger = logging.getLogger("apme.venv")

_PROXY_ENV = "APME_GALAXY_PROXY_URL"
_PROXY_ADMIN_TOKEN_ENV = "APME_PROXY_ADMIN_TOKEN"
_PROXY_ADMIN_TOKEN_HEADER = "x-apme-proxy-token"


def get_data_root() -> Path:
    """Return the APME data root directory for session venv storage.

    Reads ``APME_COLLECTION_CACHE`` from the environment.  Falls back to
    ``~/.apme-data/collection-cache`` when unset.

    Returns:
        Path to the data root directory.
    """
    base = os.environ.get("APME_COLLECTION_CACHE", "").strip()
    if base:
        return Path(base).expanduser().resolve()
    return Path(os.path.expanduser("~/.apme-data/collection-cache")).resolve()


def _proxy_url() -> str | None:
    """Return the galaxy proxy URL if configured, else None.

    Returns:
        The proxy URL string, or None if not set.
    """
    return os.environ.get(_PROXY_ENV, "").strip() or None


def _uv_available() -> bool:
    """Check if uv is available on PATH.

    Returns:
        True if uv executable is found, False otherwise.
    """
    return shutil.which("uv") is not None


def get_venv_python(venv_root: Path) -> Path:
    """Return the python executable inside the venv.

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        Path to the python executable.

    Raises:
        FileNotFoundError: If venv has no python executable.
    """
    exe = venv_root / "Scripts" / "python.exe" if os.name == "nt" else venv_root / "bin" / "python"
    if not exe.is_file():
        raise FileNotFoundError(f"venv has no python: {venv_root}")
    return exe


def _venv_site_packages(venv_root: Path) -> Path:
    """Return site-packages path for a venv (e.g. venv/lib/python3.12/site-packages).

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        Path to site-packages directory.

    Raises:
        FileNotFoundError: If venv has no lib dir or pythonX.Y directory.
    """
    lib = venv_root / "lib"
    if not lib.is_dir():
        raise FileNotFoundError(f"venv has no lib dir: {venv_root}")
    py_dirs = list(lib.glob("python*"))
    if not py_dirs:
        raise FileNotFoundError(f"venv has no pythonX.Y in lib: {venv_root}")
    site = py_dirs[0] / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return site


_FAILED_BUILD_RE = re.compile(r"Failed to build `([^`]+)`")

#: Wall-clock bound for a single pip/uv install invocation.  Without it a
#: stalled index fetch hangs ``subprocess.run`` forever, pinning the
#: ``run_in_executor`` worker until the session lifetime cap fires.
_PIP_INSTALL_TIMEOUT_S = 600


class PipInstallTimeout(TimeoutError):
    """A pip/uv install exceeded its wall-clock bound.

    Distinct from a build failure: retrying with excludes or ``--no-build``
    cannot help a stalled index, so callers must fail fast instead of
    walking the fallback chain.
    """


#: Brief bound for reaping a pip/uv process group after SIGTERM/SIGKILL.
_SUBPROCESS_REAP_TIMEOUT_S = 5.0


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Send SIGTERM to ``proc`` and its descendants (when supported).

    Args:
        proc: Running subprocess handle.
    """
    if proc.pid is None:
        return
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return
    with contextlib.suppress(OSError):
        proc.terminate()


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Force-kill ``proc`` and its descendants (when supported).

    Args:
        proc: Running subprocess handle.
    """
    if proc.pid is None:
        return
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    with contextlib.suppress(OSError):
        proc.kill()


def _run_subprocess_timed(
    cmd: list[str],
    *,
    timeout: float,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in an isolated process group with a wall-clock bound.

    ``subprocess.run(..., timeout=...)`` kills only the direct child; pip/uv
    workers can survive and keep the session lock pinned.  This helper starts
    the command in a new session/process group and terminates the whole group
    on timeout.

    Args:
        cmd: Command argv.
        timeout: Wall-clock bound in seconds.
        check: If True, raise ``CalledProcessError`` on non-zero exit.
        env: Explicit subprocess environment (defaults to inheriting
            ``os.environ``). Per-session Galaxy configs travel here so
            concurrent sessions never observe each other via global state.

    Returns:
        CompletedProcess with stdout/stderr captured.

    Raises:
        subprocess.TimeoutExpired: If the command exceeds ``timeout``.
        subprocess.CalledProcessError: If ``check`` is True and exit code is non-zero.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=_SUBPROCESS_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                stdout, stderr = proc.communicate(timeout=_SUBPROCESS_REAP_TIMEOUT_S)
        raise subprocess.TimeoutExpired(cmd, timeout, output=exc.output, stderr=exc.stderr) from exc

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode or 0, stdout, stderr)


def _run_pip_install(
    pip_python: Path,
    pip_specs: list[str],
    simple_url: str,
    use_uv: bool,
    *,
    exclude_file: Path | None = None,
    no_build: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a single pip/uv install command and return the result.

    Args:
        pip_python: Python interpreter inside the venv.
        pip_specs: Pip package specifiers to install.
        simple_url: PEP 503 simple index URL.
        use_uv: Whether to use uv for installation.
        exclude_file: Path to a requirements-format file listing packages to
            exclude from resolution (uv ``--excludes``).  Ignored for pip.
        no_build: If True, refuse to build source distributions.  Uses
            ``--no-build`` for uv or ``--only-binary :all:`` for pip so
            that packages requiring native compilation are skipped.  Only
            used as a last-resort fallback when ``exclude_file`` is not
            available (non-uv installs).
        env: Explicit subprocess environment for per-session Galaxy
            isolation (defaults to process inheritance).

    Returns:
        CompletedProcess with stdout/stderr captured.

    Raises:
        PipInstallTimeout: If the install exceeds its wall-clock bound.
            The message carries the stalled command argv (including the
            specs) so the failure identifies what hung. Callers must fail
            fast — the exclude/no-build fallback chain only helps build
            failures, not stalled indexes.
    """
    if use_uv:
        cmd = [
            "uv",
            "pip",
            "install",
            "--python",
            str(pip_python),
            "--extra-index-url",
            simple_url,
            "--index-strategy",
            os.environ.get("APME_UV_INDEX_STRATEGY", "").strip() or "unsafe-best-match",
        ]
        if exclude_file is not None:
            cmd.extend(["--excludes", str(exclude_file)])
        if no_build:
            cmd.append("--no-build")
        cmd.extend(pip_specs)
    else:
        cmd = [
            str(pip_python),
            "-m",
            "pip",
            "install",
            "--extra-index-url",
            simple_url,
        ]
        if no_build:
            cmd.extend(["--only-binary", ":all:"])
        cmd.extend(pip_specs)
    try:
        return _run_subprocess_timed(cmd, timeout=_PIP_INSTALL_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "pip/uv install timed out after %ds, failing fast (no exclude/no-build retry)",
            _PIP_INSTALL_TIMEOUT_S,
        )
        raise PipInstallTimeout(f"pip/uv install timed out after {_PIP_INSTALL_TIMEOUT_S}s: {' '.join(cmd)}") from exc


def _is_build_failure(output: str) -> bool:
    """Detect whether pip/uv output indicates a source-distribution build failure.

    Args:
        output: Combined stderr/stdout from the install command.

    Returns:
        True if the failure is caused by an unbuildable source distribution.
    """
    markers = (
        "Failed to build",
        "build_wheel",
        "build_sdist",
        "pkg-config",
        "error: command",
    )
    return any(m in output for m in markers)


def _extract_unbuildable_packages(output: str) -> list[str]:
    r"""Parse package names that failed to build from pip/uv error output.

    Looks for patterns like ``Failed to build \`systemd-python==235\``` and
    returns the bare package name (without version).

    Args:
        output: Combined stderr/stdout from a failed install.

    Returns:
        De-duplicated list of package names that could not be built.
    """
    raw = _FAILED_BUILD_RE.findall(output)
    seen: set[str] = set()
    result: list[str] = []
    for entry in raw:
        name = entry.split("==")[0].split(">=")[0].split("<=")[0].split("!=")[0]
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


class _PrepareOutcome(NamedTuple):
    """Result of asking the proxy to resolve collection specs.

    Attributes:
        scope_id: Opaque PEP 503 scope ID for request-scoped settings, else ``None``.
        failed_specs: Requested specs the proxy could not resolve or download.
        prepared: Wheel filenames the proxy prepared for the requested specs.
        shared: Whether the proxy served the request from its process-wide
            (shared) cache instead of a request-scoped handoff.
        generation: Source generation the shared wheels were prepared under,
            used to fence the later ``/wheels/`` fetch, else ``None``.
    """

    scope_id: str | None
    failed_specs: list[str]
    prepared: list[str]
    shared: bool
    generation: int | None = None


def _prepared_wheels(payload: object) -> list[str]:
    """Extract prepared wheel filenames from a prepare response body.

    Args:
        payload: Parsed prepare response.

    Returns:
        Wheel basenames that end in ``.whl`` and carry no path separator.
    """
    prepared = payload.get("prepared", []) if isinstance(payload, dict) else []
    if not isinstance(prepared, list):
        return []
    return [name for name in prepared if isinstance(name, str) and name.endswith(".whl") and "/" not in name]


def _prepare_collections_via_proxy(
    proxy_url: str,
    collection_specs: list[str],
    galaxy_server_configs: list[dict[str, object]] | None = None,
) -> _PrepareOutcome:
    """Ask the proxy to resolve pins/latest versions using its Galaxy CLI.

    Args:
        proxy_url: Base URL of the Galaxy Proxy.
        collection_specs: Collection FQCNs with optional version constraints.
        galaxy_server_configs: Optional scan-scoped server settings forwarded
            to the authenticated proxy request.

    Raises:
        RuntimeError: If the proxy cannot prepare the requested collections.

    Returns:
        Scope ID for session-specific settings plus the requested specs the
        proxy reported as unprepared (both empty/``None`` when nothing failed).
    """
    token = os.environ.get(_PROXY_ADMIN_TOKEN_ENV, "").strip()
    headers = {_PROXY_ADMIN_TOKEN_HEADER: token} if token else {}
    payload: dict[str, object] = {"specs": collection_specs}
    if galaxy_server_configs is not None:
        payload["servers"] = galaxy_server_configs
    try:
        response = httpx.post(
            proxy_url.rstrip("/") + "/admin/prepare-collections",
            headers=headers,
            json=payload,
            timeout=360.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Galaxy proxy could not prepare requested collections: {type(exc).__name__}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Galaxy proxy returned an invalid prepare response") from exc
    failed = payload.get("failed_specs", []) if isinstance(payload, dict) else []
    failed_specs = [spec for spec in failed if isinstance(spec, str)] if isinstance(failed, list) else []
    if failed_specs:
        logger.warning("Galaxy proxy could not prepare collection specs: %s", failed_specs)
    scope_id = payload.get("scope_id") if isinstance(payload, dict) else None
    valid_scope = scope_id if isinstance(scope_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{32}", scope_id) else None
    generation = payload.get("generation") if isinstance(payload, dict) else None
    return _PrepareOutcome(
        scope_id=valid_scope,
        failed_specs=failed_specs,
        prepared=_prepared_wheels(payload),
        shared=isinstance(payload, dict) and payload.get("shared") is True,
        generation=generation if isinstance(generation, int) and not isinstance(generation, bool) else None,
    )


def _unprepared_requested_specs(failed_specs: list[str], requested: list[str]) -> list[str]:
    """Match proxy ``failed_specs`` entries back to the requested specs.

    The proxy echoes the requested spec strings verbatim (``ns.name`` or
    ``ns.name:<constraint>``), so this is an exact comparison after trimming
    and case-folding; a looser match would exclude collections that installed
    fine.

    Args:
        failed_specs: Spec strings the proxy reported as unprepared.
        requested: Spec strings passed to the prepare request.

    Returns:
        Requested specs that must not be installed, in request order.
    """
    blocked = {spec.strip().lower() for spec in failed_specs}
    return [spec for spec in requested if spec.strip().lower() in blocked]


def _prepared_version(filename: str, prefix: str) -> Version | None:
    """Return the version a prepared wheel filename declares.

    Args:
        filename: Wheel basename such as ``ansible_collection_ns_coll-1.2.3-py3-none-any.whl``.
        prefix: Distribution prefix the filename must start with.

    Returns:
        The parsed version, or ``None`` when the filename is not a wheel of
        that distribution or its version is unparseable.
    """
    rest = filename[len(prefix) :]
    version = rest.split("-", 1)[0]
    try:
        return Version(version)
    except InvalidVersion:
        return None


def _bind_prepared_pip_specs(
    installable: list[tuple[str, str]],
    prepared: list[str],
    base_url: str,
    generation: int | None = None,
) -> list[tuple[str, str]]:
    """Point each prepared collection requirement at its wheel URL.

    The proxy index is added as an *extra* index while the default index stays
    configured, so pip/uv (``unsafe-best-match``) is free to prefer a
    same-named distribution published on the default index over the wheel this
    request prepared — even at an equal version, where the resolver's tie-break
    is unspecified. Installing the prepared wheel by URL binds the requirement
    to that exact artifact; wheels it depends on still resolve from the
    configured indexes, which keeps dependency installation working.

    The requirement's own constraint still has to be honoured when several
    prepared wheels exist for one collection: a previous install can leave
    another version in the cache, so ``ansible.posix:1.5.4`` may see both 1.5.3
    and 1.5.4. Candidates outside the constraint are dropped, and the highest
    remaining one is bound — the same choice the resolver would make among
    artifacts this request prepared. A requirement with no prepared candidate
    is left as-is (its content did not come from this scope).

    Args:
        installable: ``(spec, pip_spec)`` pairs to install.
        prepared: Wheel basenames the proxy prepared for this request.
        base_url: Proxy URL prefix that serves those wheels (the scoped handoff
            or the shared ``/wheels/`` directory).
        generation: Source generation the shared wheels were prepared under;
            appended as a query parameter so a later source change cannot swap
            the artifact behind the same filename.

    Returns:
        ``(spec, pip_spec)`` pairs with prepared collections bound to their
        wheel URL.
    """
    fence = f"?generation={generation}" if generation is not None else ""
    bound: list[tuple[str, str]] = []
    for spec, pip_spec in installable:
        fqcn = spec.split(":", 1)[0].strip()
        namespace, _, collection = fqcn.partition(".")
        dist = f"ansible-collection-{namespace}-{collection}"
        prefix = f"ansible_collection_{namespace}_{collection}-"
        constraint = pip_spec[len(dist) :].strip() if pip_spec.startswith(dist) else ""
        candidates: list[tuple[Version, str]] = []
        try:
            wanted = SpecifierSet(constraint)
        except InvalidSpecifier:  # pragma: no cover - validated by _spec_to_pip
            wanted = SpecifierSet()
        for name in prepared:
            if not name.startswith(prefix):
                continue
            version = _prepared_version(name, prefix)
            if version is None or version not in wanted:
                continue
            candidates.append((version, name))
        if candidates:
            _, chosen = max(candidates)
            bound.append((spec, base_url + chosen + fence))
        else:
            bound.append((spec, pip_spec))
    return bound


def _release_session_scope(proxy_url: str, scope_id: str) -> None:
    """Release a request-scoped wheel handoff after installation finishes.

    Args:
        proxy_url: Base URL of the Galaxy Proxy.
        scope_id: Opaque scope returned by the prepare request.
    """
    token = os.environ.get(_PROXY_ADMIN_TOKEN_ENV, "").strip()
    headers = {_PROXY_ADMIN_TOKEN_HEADER: token} if token else {}
    try:
        response = httpx.delete(
            proxy_url.rstrip("/") + f"/admin/session-scopes/{scope_id}",
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # The proxy TTL remains a bounded fallback if release cannot be
        # delivered. Do not mask the collection-install result with cleanup.
        logger.warning("Could not release scoped Galaxy wheel handoff: %s", type(exc).__name__)


def _is_stale_wheel_fence(output: str) -> bool:
    """Detect an install failure caused by a stale prepared-wheel generation.

    A shared handoff names wheels by filename, so the fetch is fenced with the
    generation they were prepared under. When the collection source changes in
    between, the proxy rejects that fetch with ``409 Conflict`` and the install
    fails without touching the session venv: the prepared wheels belong to a
    source the proxy no longer serves.

    Args:
        output: Combined stderr/stdout of the failed install command.

    Returns:
        True when the proxy rejected a fenced wheel fetch.
    """
    if "generation=" not in output:
        return False
    return "409" in output or "Conflict" in output


def _prepare_proxy_install(
    proxy_url: str,
    installable: list[tuple[str, str]],
    galaxy_server_configs: list[dict[str, object]] | None,
    failed: list[str],
) -> tuple[list[tuple[str, str]], str, str | None] | None:
    """Prepare collection wheels through the proxy and bind them to their URLs.

    Preparation resolves exact pins because the project page lists prepared
    wheels and does not enumerate remote Galaxy versions. A config update can
    race this request and return 409, so retry once after that conflict.

    Args:
        proxy_url: Base URL of the Galaxy Proxy.
        installable: ``(spec, pip_spec)`` pairs to install.
        galaxy_server_configs: Optional scan-scoped server settings forwarded
            to the authenticated proxy request.
        failed: Failure accumulator. Specs that must not be installed are
            appended here so the caller reports them.

    Returns:
        ``(installable, index_url, scope_id)`` with prepared collections bound
        to their wheel URL, or ``None`` when there is nothing left to install
        and every requested spec is already recorded as failed.
    """
    simple_url = proxy_url.rstrip("/") + "/simple/"
    installable_specs = [spec for spec, _ in installable]
    session_scope_id: str | None = None
    unprepared: list[str] = []
    prepared_wheels: list[str] = []
    shared_prepare = False
    generation: int | None = None
    for attempt in range(2):
        try:
            outcome = _prepare_collections_via_proxy(proxy_url, installable_specs, galaxy_server_configs)
            session_scope_id = outcome.scope_id
            unprepared = outcome.failed_specs
            prepared_wheels = outcome.prepared
            shared_prepare = outcome.shared
            generation = outcome.generation
            break
        except RuntimeError as exc:
            cause = exc.__cause__
            conflict = isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 409
            if conflict and attempt == 0:
                logger.info("Galaxy configuration changed during collection preparation; retrying once")
                continue
            if galaxy_server_configs is not None:
                logger.warning("Scoped collection preparation failed: %s", exc)
                failed.extend(installable_specs)
                return None
            logger.warning("Collection preparation failed; continuing with install: %s", exc)
            break

    if galaxy_server_configs is not None and session_scope_id is None and not shared_prepare:
        # A request-scoped Galaxy source must never install from the shared
        # index: its wheels were downloaded with other credentials. The proxy
        # only omits a scope when it explicitly reports reusing its shared
        # cache for settings identical to the ready configuration.
        failed.extend(installable_specs)
        return None
    if session_scope_id is None and not shared_prepare:
        return installable, simple_url, None

    if session_scope_id is not None:
        simple_url = proxy_url.rstrip("/") + f"/simple/session/{session_scope_id}/"
    # The scoped index is an *extra* index: pip and uv keep the default one
    # enabled, so a spec the proxy could not prepare would still resolve
    # elsewhere, install "successfully", and be recorded against this
    # scope. Drop those specs from the install command entirely and report
    # them as failed instead.
    blocked = _unprepared_requested_specs(unprepared, installable_specs)
    if blocked:
        logger.warning(
            "Not installing %d collection spec(s) the scoped Galaxy source could not prepare: %s",
            len(blocked),
            ", ".join(sorted(blocked)),
        )
        failed.extend(blocked)
        installable = [(spec, pip_spec) for spec, pip_spec in installable if spec not in blocked]
        if not installable:
            # Nothing will consume the prepared scope, so release it now
            # instead of leaving it to the proxy TTL.
            if session_scope_id is not None:
                _release_session_scope(proxy_url, session_scope_id)
            return None
    if prepared_wheels:
        # Bind prepared collections to their wheel; see
        # ``_bind_prepared_pip_specs`` for why a version pin is not enough.
        # Both paths need it: the shared cache serves its wheels from
        # ``/wheels/``, and a same-named distribution on the default index
        # would otherwise be an acceptable candidate there too.
        if session_scope_id is not None:
            base_url = proxy_url.rstrip("/") + f"/session-wheels/{session_scope_id}/"
            fence = None
        else:
            # The shared handoff names wheels only by filename. Fence the
            # fetch with the generation they were prepared under, so a
            # source change in between fails the install instead of
            # installing the same filename from the new source.
            base_url = proxy_url.rstrip("/") + "/wheels/"
            fence = generation
        installable = _bind_prepared_pip_specs(installable, prepared_wheels, base_url, fence)
    return installable, simple_url, session_scope_id


def _install_collections_via_proxy(
    pip_python: Path,
    collection_specs: list[str],
    proxy_url: str,
    use_uv: bool,
    env: dict[str, str] | None = None,
    galaxy_server_configs: list[dict[str, object]] | None = None,
) -> list[str]:
    """Install collections into the venv via the galaxy proxy (PEP 503).

    Attempts a bulk install first.  On failure the error output is inspected
    for unbuildable native packages (e.g. ``systemd-python`` needing
    ``pkg-config``).  When such packages are found **and** uv is in use,
    the install is retried with ``--excludes`` so the resolver drops only
    those native packages while keeping all Python/collection transitive
    deps.  For non-uv installs, ``--only-binary :all:`` is used as a
    coarser fallback.

    If the bulk retry still fails, individual per-collection installs are
    attempted with the same exclude/no-build strategy.

    Args:
        pip_python: Python interpreter inside the venv.
        collection_specs: Collection specifiers to install.
        proxy_url: Base URL of the galaxy proxy.
        use_uv: Whether to use uv for installation.
        env: Explicit subprocess environment for per-session Galaxy
            isolation (defaults to process inheritance).
        galaxy_server_configs: Optional scan-scoped server settings passed to
            the authenticated proxy preparation request.

    Returns:
        List of collection specs that failed to install (empty on full success).
        A spec whose version constraint is not valid PEP 440 is reported here
        without stopping the other collections: one typo must not leave the
        session venv empty (which would silently mis-resolve every later
        collection reference of the scan).

    Note:
        A stalled pip/uv install propagates ``PipInstallTimeout`` from the
        install helpers — failing fast instead of walking the fallback chain.

        For a request-scoped Galaxy source, specs the proxy reported in
        ``failed_specs`` are excluded from the install command and returned as
        failures. The scoped index is added as an extra index, so leaving them
        in would let pip/uv satisfy them from another index and report success
        for content that never came from this scope. Successfully prepared
        collections are installed from their scoped wheel URL, so their
        resolution cannot silently fall back to a same-named default-index
        distribution; their own dependencies still resolve normally.

        A preparation response that reuses the proxy's shared cache carries no
        scope; that is a success, not a failed preparation, and the install
        proceeds against the shared index.

        The shared handoff is fenced with the generation its wheels were
        prepared under. A source change in between makes the proxy reject that
        fetch; the preparation is then repeated once against the current
        source, because nothing was installed and the collections themselves
        are still available. Reporting them as failed would abort the scan.
    """
    installable: list[tuple[str, str]] = []
    failed: list[str] = []
    for spec in collection_specs:
        try:
            installable.append((spec, _spec_to_pip(spec)))
        except ValueError as exc:
            logger.warning("Skipping collection spec %r: %s", spec, exc)
            failed.append(spec)
    if not installable:
        return failed
    requested = list(installable)

    scope_ids: list[str] = []
    try:
        for attempt in range(2):
            plan = _prepare_proxy_install(proxy_url, requested, galaxy_server_configs, failed)
            if plan is None:
                return failed
            installable, simple_url, session_scope_id = plan
            if session_scope_id is not None:
                scope_ids.append(session_scope_id)
            pip_specs = [pip_spec for _, pip_spec in installable]

            result = _run_pip_install(pip_python, pip_specs, simple_url, use_uv, env=env)
            if result.returncode == 0:
                return failed
            bulk_output = result.stderr or result.stdout
            if attempt == 0 and session_scope_id is None and _is_stale_wheel_fence(bulk_output):
                # The collection source changed between preparation and the
                # wheel fetch, so the proxy fenced off the prepared artifacts.
                # Nothing was installed; prepare again against the current
                # source instead of reporting the collections as unavailable.
                logger.info("Galaxy source changed after collection preparation; preparing again")
                continue
            break

        if _is_build_failure(bulk_output):
            unbuildable = _extract_unbuildable_packages(bulk_output)
            result = _retry_without_native(
                pip_python,
                pip_specs,
                simple_url,
                use_uv,
                unbuildable,
                bulk_output,
                env,
            )
            if result.returncode == 0:
                return failed
            bulk_output = result.stderr or result.stdout

        logger.warning(
            "Bulk collection install failed, falling back to individual installs: %s",
            bulk_output,
        )

        for spec, pip_spec in installable:
            individual = _run_pip_install(pip_python, [pip_spec], simple_url, use_uv, env=env)
            if individual.returncode != 0:
                ind_output = individual.stderr or individual.stdout
                if _is_build_failure(ind_output):
                    unbuildable = _extract_unbuildable_packages(ind_output)
                    retry = _retry_without_native(
                        pip_python,
                        [pip_spec],
                        simple_url,
                        use_uv,
                        unbuildable,
                        ind_output,
                        env,
                    )
                    if retry.returncode == 0:
                        logger.info("Collection installed successfully (excluded native deps): %s", spec)
                        continue
                    ind_output = retry.stderr or retry.stdout
                logger.warning(
                    "Collection install failed for %s: %s",
                    spec,
                    ind_output,
                )
                failed.append(spec)
            else:
                logger.info("Collection installed successfully: %s", spec)

        return failed
    finally:
        for scope_id in scope_ids:
            _release_session_scope(proxy_url, scope_id)


_MAX_EXCLUDE_RETRIES = 5


def _retry_without_native(
    pip_python: Path,
    pip_specs: list[str],
    simple_url: str,
    use_uv: bool,
    unbuildable: list[str],
    original_output: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Retry an install after excluding unbuildable native packages.

    For uv: iteratively discovers and excludes native packages that cannot
    be built from source.  Each retry adds newly discovered unbuildable
    packages to the excludes file (up to ``_MAX_EXCLUDE_RETRIES`` rounds).
    For pip: falls back to ``--only-binary :all:`` (coarser but functional).

    Args:
        pip_python: Python interpreter inside the venv.
        pip_specs: Pip package specifiers to install.
        simple_url: PEP 503 simple index URL.
        use_uv: Whether to use uv for installation.
        unbuildable: Package names that failed to build from source.
        original_output: The original error output (for logging context).
        env: Explicit subprocess environment for per-session isolation.

    Returns:
        CompletedProcess from the last retry attempt.
    """
    if not (use_uv and unbuildable):
        fallback = "--no-build" if use_uv else "--only-binary :all:"
        logger.warning(
            "Build failed for native packages; retrying with %s: %s",
            fallback,
            original_output,
        )
        return _run_pip_install(pip_python, pip_specs, simple_url, use_uv, no_build=True, env=env)

    all_excluded: set[str] = set(unbuildable)
    excludes_dir = Path(tempfile.mkdtemp(prefix="apme-excludes-"))
    try:
        for attempt in range(_MAX_EXCLUDE_RETRIES):
            logger.warning(
                "Build failed for native packages %s; retrying with --excludes (attempt %d)",
                sorted(all_excluded),
                attempt + 1,
            )
            excludes_file = excludes_dir / "excludes.txt"
            excludes_file.write_text(
                "\n".join(sorted(all_excluded)) + "\n",
                encoding="utf-8",
            )
            result = _run_pip_install(
                pip_python,
                pip_specs,
                simple_url,
                use_uv,
                exclude_file=excludes_file,
                env=env,
            )
            if result.returncode == 0:
                return result

            output = result.stderr or result.stdout
            if not _is_build_failure(output):
                return result

            new_pkgs = _extract_unbuildable_packages(output)
            newly_found = set(new_pkgs) - all_excluded
            if not newly_found:
                return result
            all_excluded.update(newly_found)

        return result
    finally:
        shutil.rmtree(excludes_dir, ignore_errors=True)


def _run_base_venv_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one base-venv setup command with the shared wall-clock bound.

    Mirrors :func:`_run_pip_install`: a stalled ``uv venv`` / ``venv`` /
    ``ansible-core`` install must fail fast with :class:`PipInstallTimeout`
    instead of pinning the ``run_in_executor`` worker forever.  Callers
    (``VenvSessionManager.acquire``) already record timeout metrics and
    release the session lock on ``PipInstallTimeout``.

    Args:
        cmd: Command argv to run.

    Returns:
        CompletedProcess with stdout/stderr captured.

    Raises:
        PipInstallTimeout: If the command exceeds its wall-clock bound.
    """
    try:
        return _run_subprocess_timed(cmd, timeout=_PIP_INSTALL_TIMEOUT_S, check=True)
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "base venv setup timed out after %ds, failing fast",
            _PIP_INSTALL_TIMEOUT_S,
        )
        raise PipInstallTimeout(f"base venv setup timed out after {_PIP_INSTALL_TIMEOUT_S}s") from exc


def create_base_venv(
    venv_dir: Path,
    ansible_core_version: str,
    python_exe: str | None = None,
) -> None:
    """Create a virtual environment and install ansible-core into it.

    Each setup command shares the pip/uv wall-clock bound and fails fast
    with ``PipInstallTimeout`` on stalls, so ``VenvSessionManager.acquire``
    can record timeout metrics and release the session lock.

    Args:
        venv_dir: Exact directory for the virtualenv (created if absent).
        ansible_core_version: Pip-compatible version, e.g. ``"2.17.0"``.
        python_exe: Python interpreter for ``uv venv --python`` (optional).
    """
    use_uv = _uv_available()
    if use_uv:
        cmd = ["uv", "venv", str(venv_dir)]
        if python_exe:
            cmd.extend(["--python", python_exe])
        _run_base_venv_cmd(cmd)
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        if python_exe:
            cmd = [python_exe, "-m", "venv", str(venv_dir)]
        _run_base_venv_cmd(cmd)

    pip_python = get_venv_python(venv_dir)
    if use_uv:
        _run_base_venv_cmd(
            ["uv", "pip", "install", "--python", str(pip_python), f"ansible-core=={ansible_core_version}"],
        )
    else:
        _run_base_venv_cmd(
            [str(pip_python), "-m", "pip", "install", f"ansible-core=={ansible_core_version}"],
        )


def install_collections_incremental(
    venv_dir: Path,
    collection_specs: list[str],
    env: dict[str, str] | None = None,
    galaxy_server_configs: list[dict[str, object]] | None = None,
) -> list[str]:
    """Install collections into an existing venv via the galaxy proxy.

    Uses ``APME_GALAXY_PROXY_URL`` to install collections as pip packages
    through the proxy's PEP 503 simple index.  Safe to call repeatedly
    with overlapping specs — already-installed packages are no-ops.

    Individual collection failures are non-fatal: the scan continues with
    whatever collections could be installed.  Failed specs are returned
    so callers can report them.

    Args:
        venv_dir: Root of the virtualenv (must already exist with ansible-core).
        collection_specs: Collection specifiers to install.
        env: Explicit subprocess environment carrying the session Galaxy
            config (per-call, no global mutation).
        galaxy_server_configs: Optional session Galaxy servers for proxy
            collection preparation.

    Returns:
        List of collection specs that failed to install (empty on full success).

    Raises:
        RuntimeError: If APME_GALAXY_PROXY_URL is not configured.
    """
    if not collection_specs:
        return []

    proxy = _proxy_url()
    if not proxy:
        raise RuntimeError(
            "APME_GALAXY_PROXY_URL must be set. The galaxy proxy is the sole collection installation path."
        )

    pip_python = get_venv_python(venv_dir)
    use_uv = _uv_available()
    return _install_collections_via_proxy(pip_python, collection_specs, proxy, use_uv, env, galaxy_server_configs)


def list_installed_packages(venv_dir: Path) -> list[tuple[str, str, str, str]]:
    """List Python packages installed in a session venv.

    Uses ``importlib.metadata`` via the venv's Python to retrieve name,
    version, license, and author in a single subprocess call. Galaxy
    collection wrapper distributions generated by the local proxy are
    excluded because collections are reported separately by
    :func:`list_installed_collections`.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        List of ``(name, version, license, supplier)`` tuples.  Empty list on error.
    """
    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return []

    script = textwrap.dedent("""\
        import importlib.metadata, json, sys
        result = []
        for dist in importlib.metadata.distributions():
            meta = dist.metadata
            wheel_metadata = dist.read_text("WHEEL") or ""
            result.append({
                "name": meta["Name"] or "",
                "version": meta["Version"] or "",
                "license": meta.get("License", "") or "",
                "supplier": meta.get("Author", "") or meta.get("Author-email", "") or "",
                "is_galaxy_collection_wrapper": (
                    "Generator: ansible-collection-proxy" in wheel_metadata.splitlines()
                ),
            })
        result.sort(key=lambda e: (e.get("name", "").lower(), e.get("version", "")))
        json.dump(result, sys.stdout)
    """)
    cmd = [str(pip_python), "-c", script]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to list packages in %s: %s", venv_dir, exc)
        return []

    if result.returncode != 0:
        logger.warning("Package listing failed in %s: %s", venv_dir, result.stderr)
        return []

    try:
        entries = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse package list output from %s", venv_dir)
        return []

    return [
        (e["name"], e.get("version", ""), e.get("license", ""), e.get("supplier", ""))
        for e in entries
        if isinstance(e, dict) and "name" in e
        if isinstance(e["name"], str) and not e.get("is_galaxy_collection_wrapper", False)
    ]


def get_dependency_tree(venv_dir: Path) -> str:
    """Return the dependency tree for a session venv as raw text.

    Runs ``uv pip tree`` (preferred) or returns an empty string if
    uv is unavailable or the command fails.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        Human-readable dependency tree, or ``""`` on error.
    """
    if not _uv_available():
        return ""

    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return ""

    cmd = ["uv", "pip", "tree", "--python", str(pip_python)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to get dependency tree in %s: %s", venv_dir, exc)
        return ""

    if result.returncode != 0:
        logger.warning("uv pip tree failed in %s: %s", venv_dir, result.stderr)
        return ""

    return result.stdout.strip()


def _read_collection_metadata(coll_dir: Path, namespace: str) -> tuple[str, str]:
    """Read license and supplier from a collection's metadata files.

    Tries ``MANIFEST.json`` first (installed collections), then falls back
    to ``galaxy.yml`` (source collections).

    Args:
        coll_dir: Path to the collection directory.
        namespace: Collection namespace (used as supplier fallback).

    Returns:
        ``(license, supplier)`` tuple.  Empty strings if no metadata found.
    """
    manifest_file = coll_dir / "MANIFEST.json"
    if manifest_file.is_file():
        try:
            info = json.loads(manifest_file.read_text()).get("collection_info", {})
            license_val = ", ".join(info.get("license", [])) or ""
            supplier = ", ".join(info.get("authors", [])) or info.get("namespace", namespace) or ""
            return (license_val, supplier)
        except (json.JSONDecodeError, OSError):
            pass

    galaxy_file = coll_dir / "galaxy.yml"
    if galaxy_file.is_file():
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            pass
        else:
            try:
                galaxy = yaml.safe_load(galaxy_file.read_text()) or {}
                raw_license = galaxy.get("license", [])
                license_val = ", ".join(raw_license) if isinstance(raw_license, list) else str(raw_license or "")
                supplier = ", ".join(galaxy.get("authors", [])) or galaxy.get("namespace", namespace) or ""
                return (license_val, supplier)
            except (OSError, yaml.YAMLError):
                pass

    return ("", "")


def list_installed_collections(venv_dir: Path) -> list[tuple[str, str, str, str]]:
    """List Ansible collections installed in a session venv.

    Runs ``ansible-galaxy collection list --format json -p /dev/null`` using
    the venv's Python.  The ``-p /dev/null`` suppresses user-level collection
    paths so only venv-installed collections are reported.  Reads
    ``MANIFEST.json`` / ``galaxy.yml`` for license and supplier metadata.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        List of ``(fqcn, version, license, supplier)`` tuples.  Empty list on error.
    """
    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return []

    cmd = [
        str(pip_python),
        "-m",
        "ansible",
        "galaxy",
        "collection",
        "list",
        "--format",
        "json",
        "-p",
        "/dev/null",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to list collections in %s: %s", venv_dir, exc)
        return []

    if result.returncode != 0:
        logger.warning("ansible-galaxy collection list failed in %s: %s", venv_dir, result.stderr)
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse collection list output from %s", venv_dir)
        return []

    seen: set[str] = set()
    collections: list[tuple[str, str, str, str]] = []
    for install_path, entries in data.items():
        if not isinstance(entries, dict):
            continue
        for fqcn, info in entries.items():
            if fqcn.startswith("ansible._"):
                continue
            if fqcn in seen:
                continue
            seen.add(fqcn)
            version = info.get("version", "") if isinstance(info, dict) else ""
            if version == "*":
                version = ""
            ns, name = fqcn.split(".", 1)
            coll_dir = Path(install_path) / ns / name
            lic, supplier = _read_collection_metadata(coll_dir, ns)
            collections.append((fqcn, version, lic, supplier))
    return sorted(collections)


_DEFAULT_TTL = 3600


# Backward-compatibility alias: identical to the canonical parser with the
# positive floor, kept because tests import this name (single canonical
# home: config_env). Drift-proof: no body to diverge from get_env_int.
_parse_positive_int_env = partial(get_env_int, min_value=1)


#: Cap on sibling venvs (distinct ansible-core versions) per session (PE-36).
#: New versions beyond the cap are refused loudly instead of evicting live
#: venvs out from under in-flight scans.
_MAX_VENVS_PER_SESSION = _parse_positive_int_env("APME_SESSION_MAX_VENVS_PER_SESSION", 8)
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _redact_url_credentials(text: str) -> str:
    """Scrub ``user:pass@`` userinfo from URLs in subprocess output.

    Private-index pip URLs can embed basic-auth credentials that would
    otherwise land verbatim in daemon logs on install/uninstall failures.

    Args:
        text: Subprocess output possibly containing URLs.

    Returns:
        Text with URL userinfo replaced by ``***``.
    """
    return re.sub(r"(https?://)[^/\s@]+@", r"\1***@", text)


def _sanitize_session_id(session_id: str) -> str:
    """Validate session_id is a safe filesystem name (no path traversal).

    Args:
        session_id: Raw session identifier from the client.

    Returns:
        The validated session_id (unchanged if safe).

    Raises:
        ValueError: If session_id contains unsafe characters.
    """
    if not session_id or not _SAFE_SESSION_RE.match(session_id):
        raise ValueError(f"Invalid session_id {session_id!r}: must be non-empty and contain only [A-Za-z0-9_-]")
    return session_id


def _normalize_version(raw: str) -> str:
    """Ensure a three-part pip version string (e.g. ``"2.17"`` -> ``"2.17.0"``).

    Args:
        raw: Version string with 2 or 3 parts (e.g. ``"2.20"`` or ``"2.20.1"``).

    Returns:
        Normalised ``X.Y.Z`` version string.

    Raises:
        ValueError: If raw is not a valid version (must match ``X.Y`` or ``X.Y.Z``).
    """
    if not _SAFE_VERSION_RE.match(raw.strip()):
        raise ValueError(f"Invalid ansible version {raw!r}: must match X.Y or X.Y.Z")
    parts = raw.strip().split(".")
    return ".".join(parts[:2]) + ".0" if len(parts) < 3 else raw.strip()


class VenvGenerationLock:
    """Async readers-writer lock for one venv generation.

    The fcntl ``.lock`` serialises on-disk mutation, but it is released
    before validator fan-out reads the venv. This in-process RW lock
    covers that window: installs take the write (exclusive) side while
    validator fan-out holds the read (shared) side for its whole
    duration, so an incremental install cannot mutate a venv an
    in-flight scan is executing against.

    Writer-preference: a writer announces itself in ``_writer_waiting``
    before blocking; new readers arriving while a writer waits (or holds)
    park on the condition instead of barging, so a steady stream of
    readers cannot starve an install. Every reader rendezvous through the
    shared condition — a second concurrent reader therefore blocks on an
    active or pending writer just like the first one does (the old
    ``readers == 1`` shortcut let the second reader slip past a writer
    holding the exclusive side).

    Acquisition is fail-fast: ``acquire_read`` / ``acquire_write`` take an
    optional ``timeout`` (default 60s) and raise ``TimeoutError`` instead
    of parking a scan forever behind a wedged peer.
    """

    def __init__(self) -> None:
        """Initialise an unlocked generation lock."""
        self._mu = asyncio.Lock()
        self._cond = asyncio.Condition(self._mu)
        self._readers = 0
        self._readers_waiting = 0
        self._writer_active = False
        self._writer_waiting = 0

    def in_use_count(self) -> int:
        """Return the number of holders plus queued waiters.

        Counts shared holders (``_readers``), readers still inside
        ``acquire_read`` (``_readers_waiting``, parked or about to take the
        shared side), the exclusive holder (``_writer_active``), and queued
        writers (``_writer_waiting``). A notified reader must stay counted:
        ``release_write`` clears ``_writer_active`` before the woken readers
        run, so counting only holders would report the lock idle in that
        window, let the cache evict it, and allow a fresh lock for the same
        generation to admit an install alongside the parked reader. Summing
        waiters waits for every holder and waiter to leave. Best-effort
        snapshot for cache eviction only (callers must stay on the
        event-loop thread, where the counters cannot change mid-read); never
        a sync primitive.

        Returns:
            Holders + waiters (0 means provably idle).
        """
        return self._readers + self._readers_waiting + (1 if self._writer_active else 0) + self._writer_waiting

    def is_idle(self) -> bool:
        """Return True when no holder or waiter references this lock.

        Returns:
            True when ``in_use_count()`` is zero.
        """
        return self.in_use_count() == 0

    async def acquire_read(self, timeout: float | None = 60.0) -> None:
        """Acquire shared read access for validator fan-out.

        Args:
            timeout: Max seconds to wait for the exclusive side to clear
                (``None`` waits forever).

        Raises:
            TimeoutError: If the lock is not acquired within ``timeout``.
        """  # noqa: DOC502 -- TimeoutError surfaces from asyncio.wait_for on expiry
        async with self._cond:
            # Monotonic deadline: every wait consumes the same budget, so
            # the documented timeout holds under notify_all churn instead
            # of resetting on each wake.
            deadline = None if timeout is None else time.monotonic() + timeout
            # Counted from entry until the shared side is held: a reader whose
            # wait is notified by ``release_write`` has not resumed yet, and
            # must still keep the lock alive for cache eviction.
            self._readers_waiting += 1
            try:
                while self._writer_active or self._writer_waiting > 0:
                    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                    await asyncio.wait_for(self._cond.wait(), remaining)
                self._readers += 1
            finally:
                self._readers_waiting -= 1

    async def release_read(self) -> None:
        """Release shared read access."""
        async with self._cond:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self, timeout: float | None = 60.0) -> None:
        """Acquire exclusive write access for installs.

        Args:
            timeout: Max seconds to wait for readers to drain
                (``None`` waits forever).

        Raises:
            TimeoutError: If the lock is not acquired within ``timeout``.
        """  # noqa: DOC502 -- TimeoutError surfaces from asyncio.wait_for on expiry
        async with self._cond:
            self._writer_waiting += 1
            acquired = False
            try:
                # Monotonic deadline (same budget across wakes as the read
                # side) so repeated notifications cannot extend the wait
                # past the documented timeout.
                deadline = None if timeout is None else time.monotonic() + timeout
                while self._writer_active or self._readers > 0:
                    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                    await asyncio.wait_for(self._cond.wait(), remaining)
                self._writer_active = True
                acquired = True
            finally:
                self._writer_waiting -= 1
                if not acquired:
                    # Abandoned wait (timeout/cancel): wake readers parked
                    # on writer-preference so they re-check the cleared
                    # announcement instead of hanging on a gone writer.
                    self._cond.notify_all()
            self._cond.notify_all()

    async def release_write(self) -> None:
        """Release exclusive write access."""
        async with self._cond:
            self._writer_active = False
            self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def read(self, timeout: float | None = 60.0) -> AsyncIterator[None]:
        """Hold shared read access for the enclosed fan-out.

        Args:
            timeout: Max seconds to wait for the exclusive side to clear
                (``None`` waits forever).

        Yields:
            None: Control to the fan-out body.

        Raises:
            TimeoutError: If the lock is not acquired within ``timeout``.
        """  # noqa: DOC502 -- TimeoutError surfaces from asyncio.wait_for on expiry
        await self.acquire_read(timeout)
        try:
            yield
        finally:
            await self.release_read()

    @contextlib.asynccontextmanager
    async def write(self, timeout: float | None = 60.0) -> AsyncIterator[None]:
        """Hold exclusive write access for the enclosed install.

        Args:
            timeout: Max seconds to wait for readers to drain
                (``None`` waits forever).

        Yields:
            None: Control to the install body.

        Raises:
            TimeoutError: If the lock is not acquired within ``timeout``.
        """  # noqa: DOC502 -- TimeoutError surfaces from asyncio.wait_for on expiry
        await self.acquire_write(timeout)
        try:
            yield
        finally:
            await self.release_write()


async def wait_out_worker[T](worker: asyncio.Future[T]) -> T:
    """Await a shielded worker, waiting it out when cancelled.

    ``asyncio.shield`` detaches the worker from outer cancellation, but a
    plain ``await shield(...)`` still exits at once when the awaiting
    coroutine is cancelled — releasing any guard the caller holds while
    the worker thread keeps mutating shared state (split exclusion).
    This helper re-waits (still shielded) so the caller keeps its guard
    until the worker finishes, then re-raises the cancellation.

    Args:
        worker: Executor future doing guarded mutation.

    Returns:
        The worker result when it completes without cancellation.

    Raises:
        asyncio.CancelledError: When the awaiting coroutine was
            cancelled (after the worker finished). A worker failure
            during the wait-out is swallowed: the scan is cancelling
            anyway and slot release is arranged separately.
    """
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Loop until the worker finishes: a second cancel during the
        # re-wait must not release the caller's guard early (double-cancel
        # would otherwise split exclusion while the worker thread still
        # mutates shared state). ``asyncio.wait`` never cancels the worker
        # itself, so worker failures surface only as completion here and
        # stay swallowed — the scan is cancelling anyway.
        while not worker.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({worker})
        raise


#: Bound on cached per-generation RW locks. The key space is
#: ``(session_id, core_version)`` — small per deployment, but client
#: session ids are untrusted input, so a bounded cap stops unbounded growth
#: from session-id churn. Least-recently-used IDLE entries are evicted
#: first; a lock that is still held or waited on is never evicted (the map
#: temporarily grows over cap instead of splitting mutual exclusion).
_MAX_GENERATION_LOCKS = 512

_GENERATION_LOCKS: BoundedCache[tuple[str, str], VenvGenerationLock] = BoundedCache(
    maxsize=_MAX_GENERATION_LOCKS,
    is_idle=lambda lock: lock.is_idle(),
)


def _generation_key(session_id: str, ansible_version: str) -> tuple[str, str]:
    """Normalise the per-generation lock key.

    Args:
        session_id: Raw session identifier.
        ansible_version: Raw ansible-core version constraint.

    Returns:
        Tuple of (sanitised session id, normalised version). Invalid
        inputs fall back to the raw strings so locking never raises.
    """
    try:
        sid = _sanitize_session_id(session_id)
    except ValueError:
        sid = session_id
    try:
        ver = _normalize_version(ansible_version)
    except ValueError:
        ver = ansible_version
    return (sid, ver)


def get_generation_lock(session_id: str, ansible_version: str) -> VenvGenerationLock:
    """Return the shared RW lock for a venv generation.

    The backing map is LRU-capped via :class:`BoundedCache`: hits refresh
    recency, and inserts evict the oldest IDLE entry only (all-busy maps
    grow temporarily over cap instead of splitting mutual exclusion).

    Args:
        session_id: Session identifier.
        ansible_version: Ansible-core version constraint.

    Returns:
        Shared ``VenvGenerationLock`` for ``(session_id, version)``.
    """
    key = _generation_key(session_id, ansible_version)
    lock = _GENERATION_LOCKS.get(key)
    if lock is None:
        lock = VenvGenerationLock()
        _GENERATION_LOCKS[key] = lock
    return lock


def venv_read_guard(
    session_id: str,
    ansible_version: str,
    timeout: float | None = 60.0,
) -> contextlib._AsyncGeneratorContextManager[None]:
    """Hold shared read access for validator fan-out.

    Args:
        session_id: Session identifier.
        ansible_version: Ansible-core version constraint.
        timeout: Max seconds to wait for the exclusive side to clear
            (``None`` waits forever).

    Returns:
        Async context manager holding the generation read lock.

    Raises:
        TimeoutError: If the lock is not acquired within ``timeout``.
    """  # noqa: DOC502 -- TimeoutError surfaces from the generation lock on expiry
    return get_generation_lock(session_id, ansible_version).read(timeout)


def venv_write_guard(
    session_id: str,
    ansible_version: str,
    timeout: float | None = 60.0,
) -> contextlib._AsyncGeneratorContextManager[None]:
    """Hold exclusive write access for venv installs.

    Args:
        session_id: Session identifier.
        ansible_version: Ansible-core version constraint.
        timeout: Max seconds to wait for readers to drain
            (``None`` waits forever).

    Returns:
        Async context manager holding the generation write lock.

    Raises:
        TimeoutError: If the lock is not acquired within ``timeout``.
    """  # noqa: DOC502 -- TimeoutError surfaces from the generation lock on expiry
    return get_generation_lock(session_id, ansible_version).write(timeout)


def clear_generation_locks() -> None:
    """Drop all cached generation locks (test helper)."""
    _GENERATION_LOCKS.clear()


def _galaxy_source_hash(galaxy_server_configs: list[dict[str, object]] | None) -> str:
    """Return a stable fingerprint of the Galaxy source configuration.

    The same ``session_id`` and collection spec can be requested against a
    different Galaxy source (``acme.widget:2.0.0`` from server A one moment,
    server B the next). A venv built from A must not warm-hit for B, so the
    source is recorded with the venv metadata and compared on every warm
    admission. Only the digest is persisted — the configuration itself may
    carry credentials.

    Args:
        galaxy_server_configs: Effective Galaxy server settings for the scan.

    Returns:
        Hex SHA-256 of the canonical configuration, or ``""`` when unset.
    """
    if not galaxy_server_configs:
        return ""
    canonical = json.dumps(galaxy_server_configs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class VenvSession:
    """Metadata for a session-scoped venv (one per core version within a session).

    Attributes:
        session_id: Client-provided session identifier.
        venv_root: Path to the venv directory.
        ansible_version: Normalised ansible-core version installed.
        installed_collections: Collection specifiers actually present in the venv.
        failed_collections: Collection specifiers that could not be installed.
        requirements_hash: Hash of the requested collection set (PE-32); a
            mismatch means missing collections still need install or a prior
            install round failed. Stale unrequested specs are retained
            append-only and are not removed during acquire.
        galaxy_source_hash: Fingerprint of the Galaxy source configuration that
            built the venv (see :func:`_galaxy_source_hash`). A mismatch is a
            warm-admission miss: the installed collections were resolved
            against a different source.
        created_at: Unix timestamp of venv creation.
        last_used_at: Unix timestamp of last acquire / touch.
    """

    session_id: str
    venv_root: Path
    ansible_version: str
    installed_collections: list[str] = field(default_factory=list)
    failed_collections: list[str] = field(default_factory=list)
    requirements_hash: str = ""
    galaxy_source_hash: str = ""
    created_at: float = 0.0
    last_used_at: float = 0.0


class VenvSessionManager:
    """Manage session-scoped venvs with locking, TTL, and reaping.

    Sessions support multiple ``ansible-core`` versions (tox-style matrix),
    capped at ``APME_SESSION_MAX_VENVS_PER_SESSION`` (default 8) sibling
    venvs — further versions are refused loudly rather than evicting live
    venvs out from under in-flight scans (PE-36). Collections are reconciled
    against the requested set — missing collections are installed, while
    unrequested collections are retained append-only (never uninstalled
    during acquire) so in-flight validators are not disturbed. A
    requirements hash tracks the requested set for warm-hit and retry
    semantics (PE-32). This supports use-cases like VSCode extensions
    where the workspace scope may change between scans.
    """

    def __init__(
        self,
        sessions_root: Path | None = None,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        """Initialise the session manager.

        Args:
            sessions_root: Directory under which session directories are
                created.  Defaults to ``$CACHE_ROOT/sessions/``.
            ttl_seconds: How long an unused venv persists before reaping.
        """
        self._root = sessions_root or (get_data_root() / "sessions")
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    @property
    def sessions_root(self) -> Path:
        """Root directory containing all session directories."""
        return self._root

    def _complete_warm_hit(
        self,
        *,
        meta_path: Path,
        existing: VenvSession,
        specs: list[str],
        desired_hash: str,
        pip_version: str,
        t0: float,
    ) -> VenvSession:
        """Persist warm-hit touch-ups and return the reused session.

        Refreshes ``last_used_at`` (TTL), converges the requirements hash,
        and clears stale collection failure records. Caller must hold the
        session lock.

        Args:
            meta_path: Path to the version's ``meta.json``.
            existing: Session record read from ``meta_path``.
            specs: Collection specifiers requested for the venv.
            desired_hash: Hash of the requested set.
            pip_version: Normalised ansible-core version (for metrics).
            t0: Monotonic start time from ``time.monotonic()``.

        Returns:
            The reused session record.
        """
        existing.last_used_at = time.time()
        existing.requirements_hash = desired_hash
        # All requested collections are installed on warm hit — clear stale failures.
        existing.failed_collections = []
        self._write_version_meta(meta_path, existing)
        dur = (time.monotonic() - t0) * 1000
        logger.info(
            "Venv: ready (%.0fms, warm hit, %d collections)",
            dur,
            len(existing.installed_collections),
        )
        self._record_acquire_metrics(
            t0,
            outcome="warm",
            ansible_version=pip_version,
            collections_requested=len(specs),
        )
        return existing

    def peek_warm(
        self,
        session_id: str,
        ansible_version: str,
        collection_specs: list[str] | None = None,
        galaxy_server_configs: list[dict[str, object]] | None = None,
    ) -> VenvSession | None:
        """Return the session venv on a warm hit without building anything.

        Semaphore-free fast path for the engine's venv admission (PE-20):
        a scan whose venv already satisfies the request reuses it instantly
        without taking a venv-build slot. Returns ``None`` when a build or
        incremental install is needed — the caller then takes the
        build-slot semaphore and runs the full :meth:`acquire`, which
        re-checks under the same lock (safe against races: the venv is
        never deleted out from under a peeked session).

        Args:
            session_id: Client-provided session identifier.
            ansible_version: e.g. ``"2.17.0"`` or ``"2.17"``.
            collection_specs: Collection specifiers to ensure are installed.
            galaxy_server_configs: Effective Galaxy server settings for this
                request; a venv built from a different source is not warm.

        Returns:
            The reused session on a warm hit, else ``None``.
        """
        session_id = _sanitize_session_id(session_id)
        specs = collection_specs or []
        pip_version = _normalize_version(ansible_version)
        desired_hash = _requirements_hash(specs)
        desired_source_hash = _galaxy_source_hash(galaxy_server_configs)

        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return None
        lock_path = session_dir / ".lock"
        version_dir = session_dir / pip_version
        meta_path = version_dir / "meta.json"
        venv_dir = version_dir / "venv"

        t0 = time.monotonic()
        try:
            with open(lock_path, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    existing = self._read_version_meta(meta_path)
                    if existing is None or not venv_dir.is_dir():
                        return None
                    if existing.galaxy_source_hash != desired_source_hash:
                        # Same session and specs, different Galaxy source: the
                        # installed collections were resolved elsewhere, so
                        # this is a build, not a warm hit.
                        logger.debug(
                            "Venv: peek_warm miss (session=%s core=%s): Galaxy source changed",
                            session_id,
                            pip_version,
                        )
                        return None
                    installed = set(existing.installed_collections)
                    if any(not _installed_satisfies_spec(installed, s) for s in specs):
                        return None
                    return self._complete_warm_hit(
                        meta_path=meta_path,
                        existing=existing,
                        specs=specs,
                        desired_hash=desired_hash,
                        pip_version=pip_version,
                        t0=t0,
                    )
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            # Session dir vanished (or lock unwritable) mid-peek — not warm.
            logger.debug("Venv: peek_warm miss (session=%s core=%s)", session_id, pip_version)
            return None

    def acquire(
        self,
        session_id: str,
        ansible_version: str,
        collection_specs: list[str] | None = None,
        galaxy_env: dict[str, str] | None = None,
        galaxy_server_configs: list[dict[str, object]] | None = None,
    ) -> VenvSession:
        """Get or create a session venv, reconciling collections with the request.

        If a venv for ``(session_id, ansible_version)`` exists and already
        contains all requested collections, it is reused instantly (warm
        hit). Otherwise only the *delta* (new collections) is installed
        (PE-32). A changed Galaxy source is never a warm hit: collections
        resolved against a different server are reinstalled, because the
        same spec can name different content per source. Collections no longer requested are deliberately *retained*:
        the fcntl lock is released before validator fan-out reads the venv,
        so uninstalling stale specs during acquire could remove a collection
        an in-flight scan is executing against (ENOENT / false
        unresolved-module findings plus install/uninstall thrash). Extra
        installed collections are harmless for resolution; disk growth is
        bounded by distinct specs ever requested. This matches the sibling
        policy below: never mutate or delete a venv that another scan may
        be reading (PE-36).

        Individual collection install failures are non-fatal — the session
        records only successfully installed collections and logs warnings
        for any that failed.  The ``failed_collections`` attribute lists
        specs that could not be installed.

          New core versions create sibling venvs under the same session
          directory — existing ones are never destroyed, up to
          ``APME_SESSION_MAX_VENVS_PER_SESSION`` (default 8) siblings, beyond
          which the request is refused with a clear error (PE-36).

        Args:
            session_id: Client-provided session identifier.
            ansible_version: e.g. ``"2.17.0"`` or ``"2.17"``.
            collection_specs: Collection specifiers to ensure are installed.
            galaxy_env: Explicit subprocess environment carrying the
                session-scoped Galaxy config (``ANSIBLE_CONFIG``). Passed
                per-call to install subprocesses so concurrent sessions
                never observe each other via global ``os.environ``.
            galaxy_server_configs: Session-scoped Galaxy settings forwarded to
                the authenticated proxy preparation request.

        Returns:
            A ``VenvSession`` with a ready-to-use venv.

        Raises:
            ValueError: If creating the new sibling would exceed
                ``APME_SESSION_MAX_VENVS_PER_SESSION`` — refused loudly
                instead of evicting live venvs.
            PipInstallTimeout: If a pip/uv install stalls past its bound —
                fails fast instead of walking the fallback chain.
            Exception: Re-raises unexpected acquire failures after recording
                error metrics.
        """  # noqa: DOC503 -- ValueError is raised by enforce_sibling_cap
        session_id = _sanitize_session_id(session_id)
        specs = collection_specs or []
        pip_version = _normalize_version(ansible_version)
        desired_hash = _requirements_hash(specs)
        desired_source_hash = _galaxy_source_hash(galaxy_server_configs)

        session_dir = self._root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        lock_path = session_dir / ".lock"
        version_dir = session_dir / pip_version
        meta_path = version_dir / "meta.json"
        venv_dir = version_dir / "venv"

        logger.info("Venv: acquiring session=%s core=%s", session_id, pip_version)
        t0 = time.monotonic()
        outcome = "create"

        def install_requested_collections(target_venv: Path, requested_specs: list[str]) -> list[str]:
            """Install collection specs with the legacy call shape when unscoped.

            Args:
                target_venv: Venv receiving the requested collections.
                requested_specs: Collection specs to install.

            Returns:
                Collection specs that failed to install.
            """
            if galaxy_server_configs is None:
                return install_collections_incremental(target_venv, requested_specs, galaxy_env)
            return install_collections_incremental(
                target_venv,
                requested_specs,
                galaxy_env,
                galaxy_server_configs,
            )

        try:
            with open(lock_path, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    self._touch_session(session_dir)

                    existing = self._read_version_meta(meta_path)
                    if existing is not None and venv_dir.is_dir():
                        installed = set(existing.installed_collections)
                        requested = set(specs)
                        source_matches = existing.galaxy_source_hash == desired_source_hash
                        if source_matches:
                            missing = {s for s in requested if not _installed_satisfies_spec(installed, s)}
                        else:
                            # The venv was built from a different Galaxy
                            # source, so presence alone does not prove the
                            # requested spec resolves to the same content.
                            # Reinstall every requested spec instead of
                            # trusting the warm admission.
                            missing = set(requested)
                            if installed:
                                logger.info(
                                    "Venv: Galaxy source changed; re-resolving %d collection(s) (session=%s core=%s)",
                                    len(missing),
                                    session_id,
                                    pip_version,
                                )
                        stale = installed - requested

                        if source_matches and not missing:
                            return self._complete_warm_hit(
                                meta_path=meta_path,
                                existing=existing,
                                specs=specs,
                                desired_hash=desired_hash,
                                pip_version=pip_version,
                                t0=t0,
                            )

                        if stale:
                            logger.info(
                                "Venv: retaining %d collection(s) no longer requested "
                                "(append-only; uninstall deferred to avoid removing "
                                "code an in-flight validator may be executing): %s",
                                len(stale),
                                ", ".join(sorted(stale)),
                            )

                        failed: list[str] = []
                        if missing:
                            logger.debug("Venv: installing %d missing collections", len(missing))
                            failed = install_requested_collections(venv_dir, sorted(missing))
                            # Every still-requested past failure was retried
                            # above, so this round's failures are the full set.
                            existing.failed_collections = sorted(failed)
                        else:
                            # No install ran; just prune failures that are no
                            # longer requested.
                            failed = [f for f in existing.failed_collections if f in requested]
                            existing.failed_collections = sorted(failed)
                        succeeded = requested - set(failed)
                        existing.installed_collections = sorted(_merge_installed_collections(installed, succeeded))
                        # Only persist the desired hash on full converge: failed
                        # installs must retain the old hash so the next acquire
                        # retries instead of warm-hitting. Retained stale specs
                        # are intentional (append-only) and do not block the
                        # hash update.
                        if not failed:
                            existing.requirements_hash = desired_hash
                            existing.galaxy_source_hash = desired_source_hash
                        else:
                            logger.warning(
                                "Venv: reconcile partial (%d failed); keeping requirements_hash for retry next acquire",
                                len(failed),
                            )
                        existing.last_used_at = time.time()
                        self._write_version_meta(meta_path, existing)
                        dur = (time.monotonic() - t0) * 1000
                        if failed:
                            logger.warning(
                                "Venv: ready with warnings (%.0fms, %d collections installed, %d failed: %s)",
                                dur,
                                len(existing.installed_collections),
                                len(failed),
                                ", ".join(failed),
                            )
                        else:
                            logger.info(
                                "Venv: ready (%.0fms, incremental, %d collections)",
                                dur,
                                len(existing.installed_collections),
                            )
                        outcome = "incremental"
                        self._record_acquire_metrics(
                            t0,
                            outcome=outcome,
                            ansible_version=pip_version,
                            collections_requested=len(specs),
                        )
                        return existing

                    enforce_sibling_cap(
                        session_dir,
                        pip_version,
                        session_id,
                        max_venvs=_MAX_VENVS_PER_SESSION,
                    )
                    version_dir.mkdir(parents=True, exist_ok=True)
                    if venv_dir.is_dir():
                        shutil.rmtree(venv_dir)

                    logger.info("Venv: cold start — creating venv core=%s", pip_version)
                    try:
                        create_base_venv(venv_dir, pip_version)
                        failed = []
                        if specs:
                            logger.debug("Venv: installing %d collections", len(specs))
                            failed = install_requested_collections(venv_dir, specs)
                    except Exception:
                        # Never leave a cap-counted orphan: a failed cold
                        # build removes its version dir before re-raising so
                        # repeated index outages cannot brick the session
                        # once the index recovers (PE-36).
                        shutil.rmtree(version_dir, ignore_errors=True)
                        raise

                    succeeded_specs = sorted(set(specs) - set(failed))
                    now = time.time()
                    session = VenvSession(
                        session_id=session_id,
                        venv_root=venv_dir,
                        ansible_version=pip_version,
                        installed_collections=succeeded_specs,
                        failed_collections=sorted(failed),
                        requirements_hash=desired_hash if not failed else "",
                        galaxy_source_hash="" if failed else desired_source_hash,
                        created_at=now,
                        last_used_at=now,
                    )
                    self._write_version_meta(meta_path, session)
                    dur = (time.monotonic() - t0) * 1000
                    if failed:
                        logger.warning(
                            "Venv: ready with warnings (%.0fms, cold start, %d collections installed, %d failed: %s)",
                            dur,
                            len(succeeded_specs),
                            len(failed),
                            ", ".join(failed),
                        )
                    else:
                        logger.info(
                            "Venv: ready (%.0fms, cold start, %d collections)",
                            dur,
                            len(succeeded_specs),
                        )
                    outcome = "create"
                    self._record_acquire_metrics(
                        t0,
                        outcome=outcome,
                        ansible_version=pip_version,
                        collections_requested=len(specs),
                    )
                    return session
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except PipInstallTimeout:
            # Fail fast: a stalled index must not serialize into the
            # exclude/no-build and per-spec fallback chain under the lock.
            self._record_acquire_metrics(
                t0,
                outcome="timeout",
                ansible_version=pip_version,
                collections_requested=len(specs),
                status="error",
            )
            raise
        except Exception:
            self._record_acquire_metrics(
                t0,
                outcome=outcome,
                ansible_version=pip_version,
                collections_requested=len(specs),
                status="error",
            )
            raise

    @staticmethod
    def _record_acquire_metrics(
        t0: float,
        *,
        outcome: str,
        ansible_version: str,
        collections_requested: int,
        status: str = "ok",
    ) -> None:
        """Emit OTel metrics for a completed (or failed) acquire.

        Args:
            t0: Monotonic start time from ``time.monotonic()``.
            outcome: ``warm``, ``incremental``, or ``create``.
            ansible_version: Normalised ansible-core version.
            collections_requested: Number of collection specs requested.
            status: ``ok`` or ``error``.
        """
        try:
            from apme_engine.observability import record_venv_acquire

            record_venv_acquire(
                time.monotonic() - t0,
                outcome=outcome,
                status=status,
                ansible_core_version=ansible_version,
                collections_requested=collections_requested,
            )
        except Exception:  # noqa: BLE001 — metrics must never break venv acquire
            logger.debug("Failed to record venv acquire metrics", exc_info=True)

    def touch(self, session_id: str) -> bool:
        """Update ``last_used_at`` on all venvs in the session to prevent expiry.

        Args:
            session_id: Session to touch.

        Returns:
            True if the session directory exists, False otherwise.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        now = time.time()
        for ver_dir in session_dir.iterdir():
            if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                continue
            meta_path = ver_dir / "meta.json"
            meta = self._read_version_meta(meta_path)
            if meta is not None:
                meta.last_used_at = now
                self._write_version_meta(meta_path, meta)
        self._touch_session(session_dir)
        return True

    def release(self, session_id: str) -> bool:
        """No-op for named sessions — TTL handles cleanup.

        Args:
            session_id: Session to release.

        Returns:
            True if the session directory exists, False otherwise.
        """
        session_id = _sanitize_session_id(session_id)
        return (self._root / session_id).is_dir()

    def get(
        self,
        session_id: str,
        ansible_version: str | None = None,
    ) -> VenvSession | None:
        """Look up a session by ID and optional core version.

        Without ``ansible_version``, returns the most recently used venv
        across all core versions in the session.

        Args:
            session_id: Session to look up.
            ansible_version: Optional core version to narrow the lookup.

        Returns:
            The matching ``VenvSession`` or ``None``.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return None

        if ansible_version:
            pip_version = _normalize_version(ansible_version)
            return self._read_version_meta(session_dir / pip_version / "meta.json")

        best: VenvSession | None = None
        for child in session_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = self._read_version_meta(child / "meta.json")
            if meta and (best is None or meta.last_used_at > best.last_used_at):
                best = meta
        return best

    def list_sessions(self) -> list[VenvSession]:
        """List all session venvs across all sessions and core versions.

        Returns:
            List of ``VenvSession`` objects sorted by ``last_used_at`` descending.
        """
        sessions: list[VenvSession] = []
        if not self._root.is_dir():
            return sessions
        for sid_dir in self._root.iterdir():
            if not sid_dir.is_dir():
                continue
            for ver_dir in sid_dir.iterdir():
                if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                    continue
                meta = self._read_version_meta(ver_dir / "meta.json")
                if meta is not None:
                    sessions.append(meta)
        sessions.sort(key=lambda s: s.last_used_at, reverse=True)
        return sessions

    def delete(self, session_id: str) -> bool:
        """Forcefully delete an entire session and all its core-version venvs.

        Args:
            session_id: Session to delete.

        Returns:
            True if the session directory existed and was removed.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        shutil.rmtree(session_dir, ignore_errors=True)
        return True

    def reap_expired(self) -> int:
        """Delete individual core-version venvs past their TTL.

        Each core-version venv inside a session can expire independently.
        If all venvs under a session are reaped, the session directory is
        removed as well.

        Returns:
            Count of individual venvs deleted.
        """
        now = time.time()
        reaped = 0
        if not self._root.is_dir():
            return reaped

        for sid_dir in self._root.iterdir():
            if not sid_dir.is_dir():
                continue
            versions_remain = False
            for ver_dir in list(sid_dir.iterdir()):
                if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                    continue
                if ver_dir.name == "session.json":
                    continue
                meta = self._read_version_meta(ver_dir / "meta.json")
                if meta is None:
                    shutil.rmtree(ver_dir, ignore_errors=True)
                    continue
                if now - meta.last_used_at > self._ttl:
                    shutil.rmtree(ver_dir, ignore_errors=True)
                    reaped += 1
                else:
                    versions_remain = True
            if not versions_remain:
                shutil.rmtree(sid_dir, ignore_errors=True)
        return reaped

    @staticmethod
    def _touch_session(session_dir: Path) -> None:
        """Update session-level metadata (``session.json``).

        Args:
            session_dir: Path to the session directory.
        """
        session_path = session_dir / "session.json"
        now = time.time()
        if session_path.is_file():
            try:
                data = json.loads(session_path.read_text(encoding="utf-8"))
                data["last_used_at"] = now
            except (json.JSONDecodeError, KeyError):
                data = {"created_at": now, "last_used_at": now}
        else:
            data = {"created_at": now, "last_used_at": now}
        tmp = session_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, session_path)

    @staticmethod
    def _read_version_meta(path: Path) -> VenvSession | None:
        """Read per-version metadata from JSON file.

        Args:
            path: Path to ``meta.json``.

        Returns:
            Parsed ``VenvSession`` or ``None`` if file is missing/corrupt.
        """
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return VenvSession(
                session_id=data["session_id"],
                venv_root=Path(data["venv_root"]),
                ansible_version=data["ansible_version"],
                installed_collections=data.get("installed_collections", []),
                failed_collections=data.get("failed_collections", []),
                requirements_hash=data.get("requirements_hash", ""),
                galaxy_source_hash=data.get("galaxy_source_hash", ""),
                created_at=data.get("created_at", 0.0),
                last_used_at=data.get("last_used_at", 0.0),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def _write_version_meta(path: Path, session: VenvSession) -> None:
        """Write per-version metadata to JSON atomically.

        Args:
            path: Path to ``meta.json``.
            session: Session to serialise.
        """
        data = asdict(session)
        data["venv_root"] = str(session.venv_root)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
