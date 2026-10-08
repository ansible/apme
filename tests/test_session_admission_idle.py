"""Session admission rate-limit and never-sealed idle expiry tests (N17)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from apme_engine.daemon import session as daemon_session
from apme_engine.daemon.session import ResourceExhaustedError, SessionState, SessionStore


def _make_idle(seconds: float, *, sealed: bool = False) -> SessionState:
    """Build a session idle for ``seconds`` on both wall and monotonic clocks.

    Args:
        seconds: Idle age to simulate.
        sealed: Whether the session sealed its upload.

    Returns:
        SessionState with backdated activity markers.
    """
    state = SessionState(session_id="idle-test")
    state.upload_sealed = sealed
    state.last_activity_at = datetime.now(UTC) - timedelta(seconds=seconds)
    state._last_activity_mono = time.monotonic() - seconds
    return state


def test_create_interval_default_enforces_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default create interval (1.0s) rejects an immediate second create.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_CREATE_INTERVAL_S", 1.0)
    monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 0.0)
    monkeypatch.delenv("APME_SESSION_CREATE_INTERVAL_S", raising=False)
    monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
    store = SessionStore()
    store.create()
    with pytest.raises(ResourceExhaustedError, match="rate limited"):
        store.create()


def test_create_interval_zero_disables_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero interval disables the creation rate limit.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_CREATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
    store = SessionStore()
    first = store.create()
    second = store.create()
    assert first.session_id != second.session_id


def test_legacy_min_interval_honored_when_new_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy MIN var still rate-limits when the new var is unset.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_SESSION_CREATE_INTERVAL_S", raising=False)
    monkeypatch.setattr(daemon_session, "_CREATE_INTERVAL_S", 1.0)
    monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 60.0)
    monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
    store = SessionStore()
    store.create()
    with pytest.raises(ResourceExhaustedError, match="rate limited"):
        store.create()


def test_never_sealed_idle_session_expires_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """A never-sealed idle session counts as expired after the idle age.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_IDLE_EXPIRE_NEVER_SEALED_S", 600.0)
    state = _make_idle(601)
    assert state.upload_sealed is False
    assert state.never_sealed_idle_expired is True
    assert state.expired is True


def test_sealed_session_uses_full_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sealed sessions are exempt from the never-sealed early reap.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_IDLE_EXPIRE_NEVER_SEALED_S", 600.0)
    state = _make_idle(601, sealed=True)
    assert state.never_sealed_idle_expired is False
    # Full TTL (default 1800s) still applies, so 601s idle is not expired.
    assert state.expired is False


def test_idle_expire_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero idle-expire disables the never-sealed early reap.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_IDLE_EXPIRE_NEVER_SEALED_S", 0.0)
    state = _make_idle(3600)
    # TTL expiry (1800s default) still applies, but the never-sealed flag is off.
    assert state.never_sealed_idle_expired is False


def test_reaper_expires_never_sealed_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reaper sweep removes never-sealed idle sessions but keeps sealed ones.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session, "_CREATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(daemon_session, "_MIN_CREATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(daemon_session, "_IDLE_EXPIRE_NEVER_SEALED_S", 600.0)
    monkeypatch.setattr(daemon_session, "_MAX_SESSIONS", 10)
    store = SessionStore()
    stale = store.create()
    stale.last_activity_at = datetime.now(UTC) - timedelta(seconds=700)
    stale._last_activity_mono = time.monotonic() - 700
    fresh = store.create()
    fresh.upload_sealed = True
    fresh.last_activity_at = datetime.now(UTC) - timedelta(seconds=700)
    fresh._last_activity_mono = time.monotonic() - 700

    expired = [sid for sid, st in store._sessions.items() if st.expired]
    for sid in expired:
        store._remove(sid)

    assert store.get(stale.session_id) is None
    assert store.get(fresh.session_id) is fresh
