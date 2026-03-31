"""End-to-end tests: session venv → ARI scan → validators.

Exercises the full pipeline:
    1. Galaxy proxy started as a background process
    2. VenvSessionManager.acquire() — cold start installs ansible-core + collections
    3. run_scan(dependency_dir=<venv site-packages>) — ARI resolves collections
    4. GraphRules / OpaValidator consume the scan context

All tests share a single session_id so only the first test pays the cold-start
cost.  Subsequent tests get warm hits (metadata check only).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    graph_report_to_violations,
    load_graph_rules,
)
from apme_engine.engine.graph_scanner import scan as graph_scan
from apme_engine.runner import run_scan
from apme_engine.validators.opa import OpaValidator
from apme_engine.venv_manager.session import VenvSession, VenvSessionManager, _venv_site_packages

_SESSION_ID = "e2e-integration-shared"
_CORE_VERSION = "2.18.0"
_COLLECTIONS = ["ansible.posix", "community.general"]

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "terrible-playbook"
_OPA_BUNDLE = Path(__file__).resolve().parent.parent / "src" / "apme_engine" / "validators" / "opa" / "bundle"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def galaxy_proxy_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """Start the galaxy proxy and yield its URL.

    Args:
        tmp_path_factory: Pytest temp path factory for stderr log.

    Yields:
        str: Base URL of the running proxy.
    """
    port = _free_port()
    stderr_log = tmp_path_factory.mktemp("proxy") / "stderr.log"
    stderr_fh = open(stderr_log, "w")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "galaxy_proxy.cli",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
    )
    try:
        if not _wait_for_port(port):
            proc.terminate()
            proc.wait(timeout=5)
            stderr_fh.close()
            pytest.fail(f"Galaxy proxy did not start on port {port}: {stderr_log.read_text()[:2000]}")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_fh.close()


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def session_mgr(tmp_path_factory: pytest.TempPathFactory) -> VenvSessionManager:
    """Create a VenvSessionManager with a temp sessions root.

    Args:
        tmp_path_factory: Pytest temp path factory.

    Returns:
        VenvSessionManager instance.
    """
    sessions_root = tmp_path_factory.mktemp("sessions")
    return VenvSessionManager(sessions_root=sessions_root, ttl_seconds=3600)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def session_venv(
    session_mgr: VenvSessionManager,
    galaxy_proxy_url: str,
) -> VenvSession:
    """Acquire a session venv once for the entire module (cold start).

    Args:
        session_mgr: Shared VenvSessionManager.
        galaxy_proxy_url: URL of the running galaxy proxy.

    Returns:
        VenvSession with ansible-core and collections installed.
    """
    old_env = os.environ.get("APME_GALAXY_PROXY_URL", "")
    os.environ["APME_GALAXY_PROXY_URL"] = galaxy_proxy_url
    try:
        t0 = time.monotonic()
        session = session_mgr.acquire(_SESSION_ID, _CORE_VERSION, _COLLECTIONS)
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"\n[e2e] Cold start venv acquire: {elapsed:.1f}s\n")
        sys.stderr.flush()
    finally:
        if old_env:
            os.environ["APME_GALAXY_PROXY_URL"] = old_env
        else:
            os.environ.pop("APME_GALAXY_PROXY_URL", None)

    assert session.venv_root.is_dir()
    assert session.installed_collections == sorted(_COLLECTIONS)
    return session


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def dep_dir(session_venv: VenvSession) -> str:
    """Site-packages path from the session venv for use as ARI dependency_dir.

    Args:
        session_venv: The session venv fixture.

    Returns:
        String path to the venv's site-packages directory.
    """
    return str(_venv_site_packages(session_venv.venv_root))


# ---------------------------------------------------------------------------
# Test 1: warm hit is fast
# ---------------------------------------------------------------------------


def test_warm_acquire_is_fast(
    session_venv: VenvSession,
    session_mgr: VenvSessionManager,
    galaxy_proxy_url: str,
) -> None:
    """Re-acquiring the same session+version+collections is near-instant.

    Args:
        session_venv: Ensures cold start happened first.
        session_mgr: Shared VenvSessionManager.
        galaxy_proxy_url: URL of the running galaxy proxy.
    """
    old_env = os.environ.get("APME_GALAXY_PROXY_URL", "")
    os.environ["APME_GALAXY_PROXY_URL"] = galaxy_proxy_url
    try:
        t0 = time.monotonic()
        warm = session_mgr.acquire(_SESSION_ID, _CORE_VERSION, _COLLECTIONS)
        elapsed_ms = (time.monotonic() - t0) * 1000
        sys.stderr.write(f"[e2e] Warm acquire: {elapsed_ms:.1f}ms\n")
        sys.stderr.flush()
    finally:
        if old_env:
            os.environ["APME_GALAXY_PROXY_URL"] = old_env
        else:
            os.environ.pop("APME_GALAXY_PROXY_URL", None)

    assert warm.venv_root.is_dir()
    assert set(warm.installed_collections) >= set(_COLLECTIONS), (
        f"Warm hit missing collections: {set(_COLLECTIONS) - set(warm.installed_collections)}"
    )


# ---------------------------------------------------------------------------
# Test 2: ARI resolves collections from session venv
# ---------------------------------------------------------------------------


def test_scan_with_session_venv(dep_dir: str) -> None:
    """ARI resolves collections from session venv site-packages.

    Args:
        dep_dir: Path to session venv site-packages.
    """
    if not _FIXTURE.is_dir():
        pytest.skip("terrible-playbook fixture not found")

    t0 = time.monotonic()
    ctx = run_scan(str(_FIXTURE / "site.yml"), str(_FIXTURE), include_scandata=True, dependency_dir=dep_dir)
    elapsed_ms = (time.monotonic() - t0) * 1000
    sys.stderr.write(f"[e2e] Scan site.yml with dep_dir: {elapsed_ms:.0f}ms\n")
    sys.stderr.flush()

    assert ctx.hierarchy_payload, "Engine should produce a hierarchy payload"
    raw = ctx.hierarchy_payload.get("collection_set", [])
    collection_set = raw if isinstance(raw, list) else []
    assert "ansible.posix" in collection_set, f"collection_set should include ansible.posix: {collection_set}"


# ---------------------------------------------------------------------------
# Test 3: native validator fires rules on session-backed scan
# ---------------------------------------------------------------------------


def test_native_violations_with_session_venv(dep_dir: str) -> None:
    """Graph rules produce violations when scanning with session venv.

    Args:
        dep_dir: Path to session venv site-packages.
    """
    if not _FIXTURE.is_dir():
        pytest.skip("terrible-playbook fixture not found")

    ctx = run_scan(str(_FIXTURE / "site.yml"), str(_FIXTURE), include_scandata=True, dependency_dir=dep_dir)

    graph: ContentGraph | None = None
    if ctx.scandata and hasattr(ctx.scandata, "content_graph"):
        graph = ctx.scandata.content_graph
    assert graph is not None, "ContentGraph not built — cannot run graph rules"

    import apme_engine.validators.native.rules as _rules_pkg

    rules_dir = str(Path(_rules_pkg.__file__).parent)
    rules = load_graph_rules(rules_dir=rules_dir)
    report = graph_scan(graph, rules)
    violations = cast(list[dict[str, object]], graph_report_to_violations(report))

    rule_ids = {str(v.get("rule_id", "")) for v in violations}
    assert len(violations) >= 5, f"Expected at least 5 graph-rule violations, got {len(violations)}"
    always_expected = {"L043", "L044", "L051"}
    missing = always_expected - rule_ids
    assert not missing, (
        f"Expected rules {sorted(always_expected)} to fire, missing {sorted(missing)}; got {sorted(rule_ids)}"
    )


# ---------------------------------------------------------------------------
# Test 4: OPA validator fires rules on session-backed scan
# ---------------------------------------------------------------------------


def test_opa_violations_with_session_venv(dep_dir: str) -> None:
    """OPA validator produces violations when scanning with session venv.

    Args:
        dep_dir: Path to session venv site-packages.
    """
    if not _FIXTURE.is_dir():
        pytest.skip("terrible-playbook fixture not found")
    if not _OPA_BUNDLE.is_dir():
        pytest.skip("OPA bundle not found")

    ctx = run_scan(str(_FIXTURE / "site.yml"), str(_FIXTURE), include_scandata=True, dependency_dir=dep_dir)
    opa = OpaValidator(str(_OPA_BUNDLE))
    violations = cast(list[dict[str, object]], opa.run(ctx))

    rule_ids = {str(v.get("rule_id", "")) for v in violations}
    assert len(violations) >= 10, f"Expected at least 10 OPA violations, got {len(violations)}"
    assert "L003" in rule_ids, f"Expected L003 (play without name), got {sorted(rule_ids)}"


# ---------------------------------------------------------------------------
# Test 5: incremental collection install via proxy
# ---------------------------------------------------------------------------


def test_incremental_collection_install(
    session_mgr: VenvSessionManager,
    galaxy_proxy_url: str,
) -> None:
    """Adding a new collection to an existing session does a delta install.

    Collections that fail to install (e.g. due to missing native deps)
    are recorded in ``failed_collections`` but do not block the session.

    Args:
        session_mgr: Shared VenvSessionManager.
        galaxy_proxy_url: URL of the running galaxy proxy.
    """
    extended_specs = [*_COLLECTIONS, "cisco.ios"]

    old_env = os.environ.get("APME_GALAXY_PROXY_URL", "")
    os.environ["APME_GALAXY_PROXY_URL"] = galaxy_proxy_url
    try:
        t0 = time.monotonic()
        session = session_mgr.acquire(_SESSION_ID, _CORE_VERSION, extended_specs)
        elapsed_ms = (time.monotonic() - t0) * 1000
        sys.stderr.write(f"[e2e] Incremental install (cisco.ios): {elapsed_ms:.0f}ms\n")
        sys.stderr.flush()
    finally:
        if old_env:
            os.environ["APME_GALAXY_PROXY_URL"] = old_env
        else:
            os.environ.pop("APME_GALAXY_PROXY_URL", None)

    all_known = set(session.installed_collections) | set(session.failed_collections)
    assert "cisco.ios" in all_known, (
        f"cisco.ios should be in installed or failed collections, got: "
        f"installed={session.installed_collections}, failed={session.failed_collections}"
    )
    assert set(_COLLECTIONS).issubset(set(session.installed_collections))


# ---------------------------------------------------------------------------
# Test 6: scan the L057 wrong-module playbook
# ---------------------------------------------------------------------------


def test_scan_wrong_module_playbook(dep_dir: str) -> None:
    """Scan playbook-l057-wrong-module.yml with session venv.

    Args:
        dep_dir: Path to session venv site-packages.
    """
    playbook = _FIXTURE / "playbook-l057-wrong-module.yml"
    if not playbook.is_file():
        pytest.skip("playbook-l057-wrong-module.yml not found")

    t0 = time.monotonic()
    ctx = run_scan(str(playbook), str(_FIXTURE), include_scandata=True, dependency_dir=dep_dir)
    elapsed_ms = (time.monotonic() - t0) * 1000
    sys.stderr.write(f"[e2e] Scan l057 playbook: {elapsed_ms:.0f}ms\n")
    sys.stderr.flush()

    assert ctx.hierarchy_payload, "Engine should produce a hierarchy payload"


# ---------------------------------------------------------------------------
# Test 7: second core version creates sibling venv
# ---------------------------------------------------------------------------


def test_sibling_core_version(
    session_mgr: VenvSessionManager,
    galaxy_proxy_url: str,
) -> None:
    """A different ansible-core version creates a sibling venv.

    Args:
        session_mgr: Shared VenvSessionManager.
        galaxy_proxy_url: URL of the running galaxy proxy.
    """
    original = session_mgr.get(_SESSION_ID, _CORE_VERSION)
    assert original is not None

    old_env = os.environ.get("APME_GALAXY_PROXY_URL", "")
    os.environ["APME_GALAXY_PROXY_URL"] = galaxy_proxy_url
    try:
        t0 = time.monotonic()
        sibling = session_mgr.acquire(_SESSION_ID, "2.17.0", ["ansible.posix"])
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"[e2e] Sibling core version (2.17.0): {elapsed:.1f}s\n")
        sys.stderr.flush()
    finally:
        if old_env:
            os.environ["APME_GALAXY_PROXY_URL"] = old_env
        else:
            os.environ.pop("APME_GALAXY_PROXY_URL", None)

    assert sibling.ansible_version == "2.17.0"
    assert sibling.venv_root != original.venv_root

    still_there = session_mgr.get(_SESSION_ID, _CORE_VERSION)
    assert still_there is not None, "Original core version venv should still exist"
