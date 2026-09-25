"""Tests for OPA circuit-breaker single-flight latch review fixes."""

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

import apme_engine.opa_client as opa_mod
from apme_engine.engine.models import YAMLDict
from apme_engine.opa_client import (
    OpaInfrastructureError,
    opa_eval_unavailable_reason,
    reset_opa_circuit_breaker,
    run_opa,
)


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _reset_breaker_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset breaker and pin OPA env for deterministic tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    reset_opa_circuit_breaker()
    monkeypatch.setenv("OPA_USE_PODMAN", "0")
    monkeypatch.delenv("APME_OPA_MAX_CONSECUTIVE_TIMEOUTS", raising=False)
    monkeypatch.delenv("APME_OPA_BREAKER_COOLDOWN", raising=False)


class _FakeClock:
    """Deterministic monotonic clock for breaker tests."""

    def __init__(self, start: float) -> None:
        """Initialise the clock.

        Args:
            start: Initial monotonic timestamp.
        """
        self._now = start

    def __call__(self) -> float:
        """Return the current fake time.

        Returns:
            Current fake monotonic timestamp.
        """
        return self._now

    def advance(self, seconds: float) -> None:
        """Advance the clock.

        Args:
            seconds: Seconds to advance.
        """
        self._now += seconds


def _install_fake_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> _FakeClock:
    """Install a deterministic monotonic clock for opa_client.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        start: Initial fake timestamp.

    Returns:
        Installed fake clock.
    """
    clock = _FakeClock(start)
    monkeypatch.setattr(time, "monotonic", clock)
    return clock


def _make_bundle(tmp_path: Path) -> Path:
    """Create an empty bundle directory (stubs bypass FS reads).

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the created bundle directory.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    return bundle


def _stub_runners_timeout(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """Stub OPA runners to raise TimeoutExpired and record calls.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        calls: List recording runner entrypoints.
    """

    def _raise_timeout(
        input_str: str, bundle_path: Path, entrypoint: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Raise TimeoutExpired for every OPA invocation.

        Args:
            input_str: JSON input (unused).
            bundle_path: Bundle path (unused).
            entrypoint: Rego entrypoint recorded in calls.
            timeout: Timeout value used for the exception.

        Returns:
            Never returns; always raises.

        Raises:
            subprocess.TimeoutExpired: Always raised to simulate OPA timeout.
        """
        calls.append(entrypoint)
        raise subprocess.TimeoutExpired(cmd="opa", timeout=timeout)

    monkeypatch.setattr(opa_mod, "_run_opa_local", _raise_timeout)
    monkeypatch.setattr(opa_mod, "_run_opa_podman", _raise_timeout)


def _stub_runners_success(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """Stub OPA runners to return empty-violations success.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        calls: List recording runner entrypoints.
    """

    def _return_success(
        input_str: str, bundle_path: Path, entrypoint: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Return a successful empty OPA eval result.

        Args:
            input_str: JSON input (unused).
            bundle_path: Bundle path (unused).
            entrypoint: Rego entrypoint recorded in calls.
            timeout: Timeout value (unused).

        Returns:
            CompletedProcess with empty violations payload.
        """
        calls.append(entrypoint)
        stdout = json.dumps({"result": [{"expressions": [{"value": []}]}]})
        return subprocess.CompletedProcess(args=["opa"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(opa_mod, "_run_opa_local", _return_success)
    monkeypatch.setattr(opa_mod, "_run_opa_podman", _return_success)


def _stub_runners_nonzero(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """Stub OPA runners to return a non-zero exit code.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        calls: List recording runner entrypoints.
    """

    def _return_nonzero(
        input_str: str, bundle_path: Path, entrypoint: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Return a failed OPA eval result.

        Args:
            input_str: JSON input (unused).
            bundle_path: Bundle path (unused).
            entrypoint: Rego entrypoint recorded in calls.
            timeout: Timeout value (unused).

        Returns:
            CompletedProcess with returncode 1.
        """
        calls.append(entrypoint)
        return subprocess.CompletedProcess(args=["opa"], returncode=1, stdout="", stderr="policy error")

    monkeypatch.setattr(opa_mod, "_run_opa_local", _return_nonzero)
    monkeypatch.setattr(opa_mod, "_run_opa_podman", _return_nonzero)


def _stub_runners_invalid_json(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """Stub OPA runners to return invalid JSON stdout.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        calls: List recording runner entrypoints.
    """

    def _return_invalid_json(
        input_str: str, bundle_path: Path, entrypoint: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Return a zero-exit OPA result with non-JSON stdout.

        Args:
            input_str: JSON input (unused).
            bundle_path: Bundle path (unused).
            entrypoint: Rego entrypoint recorded in calls.
            timeout: Timeout value (unused).

        Returns:
            CompletedProcess with invalid JSON stdout.
        """
        calls.append(entrypoint)
        return subprocess.CompletedProcess(args=["opa"], returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr(opa_mod, "_run_opa_local", _return_invalid_json)
    monkeypatch.setattr(opa_mod, "_run_opa_podman", _return_invalid_json)


def _latch_breaker(bundle: Path) -> None:
    """Drive the breaker to latched state with two timeouts (max=2).

    Args:
        bundle: Bundle directory path.
    """
    for _ in range(2):
        with pytest.raises(OpaInfrastructureError, match="timed out"):
            run_opa({"hierarchy": []}, str(bundle))


class TestBreakerLatch:
    """Latch and pre-cooldown disabled behaviour."""

    def test_latch_after_n_timeouts_disables_breaker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Breaker latches after N timeouts; next call raises disabled.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        assert opa_mod._opa_disabled is True
        assert opa_mod._consecutive_timeouts == 2
        assert opa_mod._disabled_at == clock()
        calls.clear()
        with patch("sys.stderr.write"), pytest.raises(OpaInfrastructureError, match="disabled"):
            run_opa({"hierarchy": []}, str(bundle))
        assert calls == []

    def test_disabled_before_cooldown_skips_runner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Immediate post-latch call raises disabled without invoking runner.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        with opa_mod._breaker_lock:
            assert opa_mod._breaker_cooled_locked() is False
        calls.clear()
        with pytest.raises(OpaInfrastructureError, match="disabled"):
            run_opa({"hierarchy": []}, str(bundle))
        assert calls == []


class TestBreakerTrial:
    """Half-open trial success and failure behaviour."""

    def test_cooled_trial_success_resets_breaker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cooled trial success clears the latch and returns violations.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        assert opa_mod._opa_disabled is True
        clock.advance(61.0)
        trial_calls: list[str] = []
        _stub_runners_success(monkeypatch, trial_calls)
        result = run_opa({"hierarchy": []}, str(bundle))
        assert result == []
        assert trial_calls != []
        assert opa_mod._opa_disabled is False
        assert opa_mod._consecutive_timeouts == 0
        assert opa_mod._disabled_at is None
        assert opa_mod._trial_in_flight is False

    def test_cooled_trial_timeout_relatches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cooled trial timeout re-latches the breaker.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        old_disabled_at = opa_mod._disabled_at
        assert old_disabled_at is not None
        clock.advance(61.0)
        with patch("sys.stderr.write"), pytest.raises(OpaInfrastructureError, match="timed out"):
            run_opa({"hierarchy": []}, str(bundle))
        assert opa_mod._opa_disabled is True
        assert opa_mod._trial_in_flight is False
        assert opa_mod._disabled_at == clock()
        assert opa_mod._disabled_at != old_disabled_at

    def test_cooled_trial_nonzero_exit_relatches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cooled trial non-zero exit re-latches the breaker.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        clock.advance(61.0)
        trial_calls: list[str] = []
        _stub_runners_nonzero(monkeypatch, trial_calls)
        with patch("sys.stderr.write"), pytest.raises(OpaInfrastructureError, match="non-zero"):
            run_opa({"hierarchy": []}, str(bundle))
        assert trial_calls != []
        assert opa_mod._opa_disabled is True
        assert opa_mod._trial_in_flight is False
        assert opa_mod._disabled_at == clock()

    def test_cooled_trial_invalid_json_relatches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cooled trial invalid JSON re-latches the breaker.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        clock.advance(61.0)
        trial_calls: list[str] = []
        _stub_runners_invalid_json(monkeypatch, trial_calls)
        with patch("sys.stderr.write"), pytest.raises(OpaInfrastructureError, match="invalid JSON"):
            run_opa({"hierarchy": []}, str(bundle))
        assert trial_calls != []
        assert opa_mod._opa_disabled is True
        assert opa_mod._trial_in_flight is False
        assert opa_mod._disabled_at == clock()


class TestBreakerSingleFlight:
    """Half-open trial is single-flight across threads."""

    def test_concurrent_trial_single_flight(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Holder thread owns trial; second claim and run_opa see disabled.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        latch_calls: list[str] = []
        _stub_runners_timeout(monkeypatch, latch_calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        # Simulate cooldown elapsed with the real clock so Event.wait timeouts work.
        cooldown = opa_mod._breaker_cooldown()
        opa_mod._disabled_at = time.monotonic() - (cooldown + 1.0)
        trial_calls: list[str] = []
        _stub_runners_success(monkeypatch, trial_calls)
        claimed = threading.Event()
        release = threading.Event()
        holder_errors: list[str] = []

        def _holder() -> None:
            """Claim the trial slot and hold it until released."""
            if not opa_mod._claim_breaker_trial():
                holder_errors.append("holder could not claim trial")
                claimed.set()
                return
            claimed.set()
            release.wait(timeout=5.0)
            opa_mod._settle_breaker_trial(succeeded=True)

        holder = threading.Thread(target=_holder, name="opa-trial-holder")
        holder.start()
        assert claimed.wait(timeout=5.0), "holder thread did not claim trial"
        try:
            assert opa_mod._claim_breaker_trial() is False
            with pytest.raises(OpaInfrastructureError, match="disabled"):
                run_opa({"hierarchy": []}, str(bundle))
            assert trial_calls == []
        finally:
            release.set()
            holder.join(timeout=5.0)
        assert not holder.is_alive()
        assert holder_errors == []


class TestBreakerInputValidation:
    """Non-serializable input never consumes the trial slot."""

    def test_non_serializable_input_does_not_consume_trial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bad input raises without touching breaker or trial slot.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_success(monkeypatch, calls)
        bad_input = cast(YAMLDict, {"hierarchy": [{"unserializable": {1, 2, 3}}]})
        with pytest.raises(OpaInfrastructureError, match="not JSON-serializable"):
            run_opa(bad_input, str(bundle))
        assert calls == []
        assert opa_mod._opa_disabled is False
        assert opa_mod._consecutive_timeouts == 0
        # Latch, cool down, then bad input must not consume the trial slot.
        timeout_calls: list[str] = []
        _stub_runners_timeout(monkeypatch, timeout_calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        assert opa_mod._opa_disabled is True
        clock.advance(61.0)
        trial_calls: list[str] = []
        _stub_runners_success(monkeypatch, trial_calls)
        with pytest.raises(OpaInfrastructureError, match="not JSON-serializable"):
            run_opa(bad_input, str(bundle))
        assert trial_calls == []
        assert opa_mod._opa_disabled is True
        assert opa_mod._trial_in_flight is False
        assert opa_mod._claim_breaker_trial() is True
        opa_mod._settle_breaker_trial(succeeded=True)


class TestBreakerCooledPredicate:
    """Cooled predicate agrees with the unavailable-reason probe."""

    def test_cooled_predicate_matches_reason_when_cooled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cooled breaker reports claimable and reason None.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        clock.advance(61.0)
        probe_calls: list[str] = []
        _stub_runners_success(monkeypatch, probe_calls)
        with opa_mod._breaker_lock:
            cooled = opa_mod._breaker_cooled_locked()
        assert cooled is True
        assert opa_eval_unavailable_reason(str(bundle)) is None
        assert probe_calls == []

    def test_cooled_predicate_matches_reason_when_in_flight(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-flight trial reports not-cooled and disabled reason.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        bundle = _make_bundle(tmp_path)
        clock = _install_fake_clock(monkeypatch)
        monkeypatch.setattr(opa_mod, "_max_consecutive_timeouts", lambda: 2)
        monkeypatch.setenv("APME_OPA_BREAKER_COOLDOWN", "60")
        calls: list[str] = []
        _stub_runners_timeout(monkeypatch, calls)
        with patch("sys.stderr.write"):
            _latch_breaker(bundle)
        clock.advance(61.0)
        assert opa_mod._claim_breaker_trial() is True
        try:
            with opa_mod._breaker_lock:
                cooled = opa_mod._breaker_cooled_locked()
            assert cooled is False
            probe_calls: list[str] = []
            _stub_runners_success(monkeypatch, probe_calls)
            reason = opa_eval_unavailable_reason(str(bundle))
            assert reason is not None
            assert "disabled" in reason
            assert probe_calls == []
        finally:
            opa_mod._settle_breaker_trial(succeeded=True)
