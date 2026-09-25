"""Regression tests for venv reconcile retry semantics and session admission.

Covers findings #4/#5 (requirements_hash persisted only on full converge,
fail-closed uninstall and probe, sibling cap, redaction, hash stability) and
finding #12 (SessionStore admission plus venv-build semaphore).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from apme_engine.daemon import session as daemon_session
from apme_engine.daemon.session import (
    ResourceExhaustedError,
    SessionStore,
    _parse_float_env,
    get_venv_build_semaphore,
    limit_venv_builds,
)
from apme_engine.venv_manager import session as venv_session
from apme_engine.venv_manager.session import (
    VenvSessionManager,
    _redact_url_credentials,
    _requirements_hash,
)


def _fake_create_base_venv(venv_dir: Path, ansible_core_version: str, **_kw: object) -> None:
    """Create a minimal venv skeleton for testing.

    Args:
        venv_dir: Target directory.
        ansible_core_version: Version string written to pyvenv.cfg.
        **_kw: Absorbed extra kwargs.
    """
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(f"version = {ansible_core_version}\n", encoding="utf-8")
    lib = venv_dir / "lib" / "python3.12" / "site-packages"
    lib.mkdir(parents=True, exist_ok=True)
    bindir = venv_dir / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "python").touch()


@pytest.fixture()  # type: ignore[untyped-decorator]
def sessions_root(tmp_path: Path) -> Path:
    """Provide a temporary sessions root directory.

    Args:
        tmp_path: Pytest built-in temporary directory fixture.

    Returns:
        Path to a fresh ``sessions/`` directory.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    return root


@pytest.fixture()  # type: ignore[untyped-decorator]
def manager(sessions_root: Path) -> VenvSessionManager:
    """Provide a VenvSessionManager with a temporary root.

    Args:
        sessions_root: Temporary sessions root directory fixture.

    Returns:
        A VenvSessionManager configured for testing.
    """
    return VenvSessionManager(sessions_root=sessions_root, ttl_seconds=60)


class TestWarmHitExactMatch:
    """Warm-hit reconcile short-circuits without subprocess work."""

    def test_warm_hit_exact_match_no_subprocess(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exact spec match returns instantly without subprocess calls.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "create_base_venv", _fake_create_base_venv)
        install_calls: list[list[str]] = []

        def _ok_install(venv_dir: Path, specs: list[str]) -> list[str]:
            """Record install specs and report full success.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                Empty list signalling full install success.
            """
            install_calls.append(list(specs))
            return []

        monkeypatch.setattr(venv_session, "install_collections_incremental", _ok_install)
        specs = ["a.b"]
        first = manager.acquire("warm-sid", "2.17", collection_specs=specs)
        assert first.requirements_hash == _requirements_hash(specs)
        install_calls.clear()

        def _fail_run(cmd: list[str], *, timeout: float, check: bool = False) -> subprocess.CompletedProcess[str]:
            """Fail if any subprocess is invoked on a warm hit.

            Args:
                cmd: Command argv.
                timeout: Wall-clock bound.
                check: Whether to raise on non-zero exit.

            Returns:
                Never returns; always raises.

            Raises:
                AssertionError: Always, because a warm hit must not run subprocess.
            """
            raise AssertionError("warm hit must not run subprocess")

        def _fail_uninstall(venv_dir: Path, pip_packages: list[str]) -> list[str]:
            """Fail if uninstall is invoked on a warm hit.

            Args:
                venv_dir: Target venv directory.
                pip_packages: Packages to remove.

            Returns:
                Never returns; always raises.

            Raises:
                AssertionError: Always, because a warm hit must not uninstall.
            """
            raise AssertionError("warm hit must not uninstall")

        monkeypatch.setattr(venv_session, "_run_subprocess_timed", _fail_run)
        monkeypatch.setattr(venv_session, "uninstall_collections", _fail_uninstall)
        second = manager.acquire("warm-sid", "2.17", collection_specs=specs)
        assert second.venv_root == first.venv_root
        assert second.installed_collections == first.installed_collections
        assert install_calls == []


class TestStaleOnlyRemoval:
    """Stale-only reconcile prunes and converges the hash."""

    def test_stale_only_removal_prunes_and_updates_hash(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removing stale specs prunes installed and updates the hash.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "create_base_venv", _fake_create_base_venv)
        install_calls: list[list[str]] = []

        def _ok_install(venv_dir: Path, specs: list[str]) -> list[str]:
            """Record install specs and report full success.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                Empty list signalling full install success.
            """
            install_calls.append(list(specs))
            return []

        monkeypatch.setattr(venv_session, "install_collections_incremental", _ok_install)
        uninstall_calls: list[list[str]] = []

        def _uninstall_ok(venv_dir: Path, pip_packages: list[str]) -> list[str]:
            """Confirm every requested package as absent.

            Args:
                venv_dir: Target venv directory.
                pip_packages: Bare pip package names.

            Returns:
                List of confirmed-absent package names.
            """
            uninstall_calls.append(list(pip_packages))
            return list(pip_packages)

        monkeypatch.setattr(venv_session, "uninstall_collections", _uninstall_ok)
        manager.acquire("stale-sid", "2.17", collection_specs=["a.b", "c.d"])
        install_calls.clear()
        uninstall_calls.clear()
        second = manager.acquire("stale-sid", "2.17", collection_specs=["a.b"])
        assert len(uninstall_calls) == 1
        assert "ansible-collection-c-d" in uninstall_calls[0]
        assert install_calls == []
        assert second.installed_collections == ["a.b"]
        assert second.requirements_hash == _requirements_hash(["a.b"])


class TestUninstallFailure:
    """Uninstall failure keeps stale entries and the old hash for retry."""

    def test_uninstall_failure_keeps_stale_and_old_hash(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing confirmed absent keeps stale listed and hash unchanged.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "create_base_venv", _fake_create_base_venv)

        def _ok_install(venv_dir: Path, specs: list[str]) -> list[str]:
            """Report full install success.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                Empty list signalling full install success.
            """
            return []

        monkeypatch.setattr(venv_session, "install_collections_incremental", _ok_install)

        def _uninstall_none(venv_dir: Path, pip_packages: list[str]) -> list[str]:
            """Confirm nothing absent to simulate uninstall failure.

            Args:
                venv_dir: Target venv directory.
                pip_packages: Bare pip package names.

            Returns:
                Empty list simulating uninstall failure.
            """
            return []

        monkeypatch.setattr(venv_session, "uninstall_collections", _uninstall_none)
        first = manager.acquire("uninstall-fail-sid", "2.17", collection_specs=["a.b", "c.d"])
        old_hash = first.requirements_hash
        assert old_hash == _requirements_hash(["a.b", "c.d"])
        second = manager.acquire("uninstall-fail-sid", "2.17", collection_specs=["a.b"])
        assert "c.d" in second.installed_collections
        assert second.requirements_hash == old_hash
        assert second.requirements_hash != _requirements_hash(["a.b"])


class TestInstallFailure:
    """Install failure keeps failed entries and the old hash for retry."""

    def test_install_failure_keeps_failed_and_old_hash(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failed installs stay listed and the hash stays stale.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "create_base_venv", _fake_create_base_venv)

        def _ok_install(venv_dir: Path, specs: list[str]) -> list[str]:
            """Report full install success for the seed acquire.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                Empty list signalling full install success.
            """
            return []

        monkeypatch.setattr(venv_session, "install_collections_incremental", _ok_install)
        first = manager.acquire("install-fail-sid", "2.17", collection_specs=["a.b"])
        old_hash = first.requirements_hash

        def _fail_all(venv_dir: Path, specs: list[str]) -> list[str]:
            """Report every requested spec as failed.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                All requested specs reported as failed.
            """
            return list(specs)

        monkeypatch.setattr(venv_session, "install_collections_incremental", _fail_all)

        def _fail_uninstall(venv_dir: Path, pip_packages: list[str]) -> list[str]:
            """Fail if uninstall runs when nothing is stale.

            Args:
                venv_dir: Target venv directory.
                pip_packages: Bare pip package names.

            Returns:
                Never returns; always raises.

            Raises:
                AssertionError: Always, because nothing is stale so uninstall must not run.
            """
            raise AssertionError("no stale specs so uninstall must not run")

        monkeypatch.setattr(venv_session, "uninstall_collections", _fail_uninstall)
        second = manager.acquire("install-fail-sid", "2.17", collection_specs=["a.b", "c.d"])
        assert second.failed_collections == ["c.d"]
        assert second.installed_collections == ["a.b"]
        assert second.requirements_hash == old_hash
        assert second.requirements_hash != _requirements_hash(["a.b", "c.d"])


class TestProbeFailClosed:
    """Absence-probe failure is fail-closed and retries next acquire."""

    def test_probe_failure_fail_closed_keeps_stale(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty probe result keeps stale listed and hash unchanged.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "create_base_venv", _fake_create_base_venv)

        def _ok_install(venv_dir: Path, specs: list[str]) -> list[str]:
            """Report full install success.

            Args:
                venv_dir: Target venv directory.
                specs: Requested collection specs.

            Returns:
                Empty list signalling full install success.
            """
            return []

        monkeypatch.setattr(venv_session, "install_collections_incremental", _ok_install)
        first = manager.acquire("probe-fail-sid", "2.17", collection_specs=["a.b", "c.d"])
        old_hash = first.requirements_hash

        def _ok_run(cmd: list[str], *, timeout: float, check: bool = False) -> subprocess.CompletedProcess[str]:
            """Return success without running a real subprocess.

            Args:
                cmd: Command argv.
                timeout: Wall-clock bound.
                check: Whether to raise on non-zero exit.

            Returns:
                Successful completed process with empty output.
            """
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def _no_uv() -> bool:
            """Force the pip code path for determinism.

            Returns:
                False to force the pip code path.
            """
            return False

        def _confirm_none(pip_python: Path, pip_packages: list[str]) -> list[str]:
            """Confirm nothing absent to simulate probe failure.

            Args:
                pip_python: Venv python binary.
                pip_packages: Bare pip package names.

            Returns:
                Empty list simulating probe failure.
            """
            return []

        monkeypatch.setattr(venv_session, "_run_subprocess_timed", _ok_run)
        monkeypatch.setattr(venv_session, "_uv_available", _no_uv)
        monkeypatch.setattr(venv_session, "_confirm_absent_packages", _confirm_none)
        second = manager.acquire("probe-fail-sid", "2.17", collection_specs=["a.b"])
        assert "c.d" in second.installed_collections
        assert second.requirements_hash == old_hash
        assert second.requirements_hash != _requirements_hash(["a.b"])


class TestSiblingCap:
    """Sibling cap refuses growth past the limit."""

    def test_sibling_cap_refuses_ninth_version(
        self, manager: VenvSessionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ninth sibling venv is refused with a cap-naming ValueError.

        Args:
            manager: VenvSessionManager fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(venv_session, "_MAX_VENVS_PER_SESSION", 8)
        session_id = "cap-sid"
        session_dir = manager.sessions_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        for i in range(8):
            (session_dir / f"2.{i}.0").mkdir(parents=True, exist_ok=True)
        create_calls: list[Path] = []

        def _fail_create(venv_dir: Path, ansible_core_version: str, **_kw: object) -> None:
            """Record creation attempts and fail if the cap leaks.

            Args:
                venv_dir: Target directory.
                ansible_core_version: Version string.
                **_kw: Absorbed extra kwargs.

            Raises:
                AssertionError: Always, because the sibling cap must refuse before creating.
            """
            create_calls.append(venv_dir)
            raise AssertionError("sibling cap must refuse before creating venv")

        monkeypatch.setattr(venv_session, "create_base_venv", _fail_create)
        with pytest.raises(ValueError, match="APME_SESSION_MAX_VENVS_PER_SESSION") as excinfo:
            manager.acquire(session_id, "2.99", collection_specs=[])
        assert "8" in str(excinfo.value)
        assert create_calls == []


class TestRedactCredentials:
    """URL credential redaction."""

    def test_redact_url_credentials_scrubs_userinfo(self) -> None:
        """Userinfo in URLs is replaced with stars."""
        text = "https://user:pass@proxy.example.com/simple/ and http://token123@other.example/x failed"
        redacted = _redact_url_credentials(text)
        assert "user" not in redacted
        assert "pass" not in redacted
        assert "token123" not in redacted
        assert "***@" in redacted
        assert "proxy.example.com" in redacted
        assert "other.example" in redacted


class TestRequirementsHash:
    """Hash stability and sensitivity."""

    def test_requirements_hash_order_stable(self) -> None:
        """Same set in different order yields the same hash."""
        assert _requirements_hash(["b.b", "a.a"]) == _requirements_hash(["a.a", "b.b"])

    def test_requirements_hash_changes_on_set_change(self) -> None:
        """Different collection sets yield different hashes."""
        assert _requirements_hash(["a.a"]) != _requirements_hash(["a.a", "b.b"])
        assert _requirements_hash([]) != _requirements_hash(["a.a"])


class TestSessionAdmission:
    """SessionStore cap and minimum-interval admission."""

    def test_create_cap_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Creating past the cap raises ResourceExhaustedError.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 2)
        monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 0.0)
        store = SessionStore()
        store.create()
        store.create()
        with pytest.raises(ResourceExhaustedError):
            store.create()

    def test_min_interval_disabled_allows_burst(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Disabled interval allows rapid successive creates.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 0.0)
        monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
        store = SessionStore()
        first = store.create()
        second = store.create()
        third = store.create()
        assert len({first.session_id, second.session_id, third.session_id}) == 3

    def test_min_interval_enabled_rejects_immediate_second_create(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Enabled interval rejects an immediate second create.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 60.0)
        monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
        store = SessionStore()
        store.create()
        with pytest.raises(ResourceExhaustedError, match="rate limited"):
            store.create()


class TestParseFloatEnv:
    """Float env parsing falls back on invalid or non-finite values."""

    def test_parse_float_env_invalid_and_inf_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid and non-finite values fall back to the default.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setenv("APME_TEST_FLOAT_X", "not-a-float")
        assert _parse_float_env("APME_TEST_FLOAT_X", 1.5) == 1.5
        monkeypatch.setenv("APME_TEST_FLOAT_X", "inf")
        assert _parse_float_env("APME_TEST_FLOAT_X", 1.5) == 1.5
        monkeypatch.setenv("APME_TEST_FLOAT_X", "nan")
        assert _parse_float_env("APME_TEST_FLOAT_X", 1.5) == 1.5
        monkeypatch.setenv("APME_TEST_FLOAT_X", "2.5")
        assert _parse_float_env("APME_TEST_FLOAT_X", 1.5) == 2.5


class TestVenvBuildSemaphore:
    """Venv-build semaphore sharing, sizing, and bounded wait."""

    def test_semaphore_shared_and_sized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Semaphore is process-wide and sized by configuration.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(daemon_session, "_MAX_CONCURRENT_VENV_BUILDS", 2)
        monkeypatch.setattr(daemon_session, "_venv_build_semaphore", None)
        first = get_venv_build_semaphore()
        second = get_venv_build_semaphore()
        assert first is second
        assert first._value == 2

    async def test_limit_venv_builds_timeout_when_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exhausted slots raise TimeoutError within the wait bound.

        Args:
            monkeypatch: Pytest monkeypatch fixture.

        Raises:
            AssertionError: If the exhausted slot were acquired (unreachable).
        """
        monkeypatch.setattr(daemon_session, "_MAX_CONCURRENT_VENV_BUILDS", 1)
        monkeypatch.setattr(daemon_session, "_venv_build_semaphore", None)
        monkeypatch.setattr(daemon_session, "_VENV_BUILD_WAIT_S", 0.05)
        sem = get_venv_build_semaphore()
        await sem.acquire()
        try:
            with pytest.raises(TimeoutError, match="No venv build slot"):
                async with limit_venv_builds():
                    raise AssertionError("must not acquire exhausted slot")
        finally:
            sem.release()
        async with limit_venv_builds():
            pass
