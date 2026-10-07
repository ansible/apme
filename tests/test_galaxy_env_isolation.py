"""Tests for per-session Galaxy env isolation (N3)."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import pytest


async def test_galaxy_env_snapshot_leaves_os_environ_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Session setup never mutates global ``os.environ``.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon.engine_server import EngineServicer

    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[galaxy]\nserver_list = hub\n")
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    servicer = EngineServicer()
    before = dict(os.environ)
    async with servicer._activate_galaxy_proxy_config(cfg) as env:
        assert env["ANSIBLE_CONFIG"] == str(cfg)
        assert "ANSIBLE_CONFIG" not in os.environ
    assert dict(os.environ) == before
    assert "ANSIBLE_CONFIG" not in os.environ


async def test_galaxy_locks_scoped_per_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrelated Galaxy configs do not share one process-wide lock.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon.engine_server import EngineServicer

    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    servicer = EngineServicer()
    cfg_a = tmp_path / "a.cfg"
    cfg_b = tmp_path / "b.cfg"
    cfg_a.write_text("[galaxy]\nserver_list = a\n")
    cfg_b.write_text("[galaxy]\nserver_list = b\n")
    lock_a = servicer._get_galaxy_proxy_cfg_lock_for(cfg_a)
    lock_b = servicer._get_galaxy_proxy_cfg_lock_for(cfg_b)
    assert lock_a is not lock_b
    assert servicer._get_galaxy_proxy_cfg_lock_for(cfg_a) is lock_a
    # Same-config sessions still serialize (one lock per identity).
    assert isinstance(lock_a, asyncio.Lock)


async def test_galaxy_same_content_shares_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Identical configs at different temp paths share one lock.

    Session configs live at unique temp paths, so the lock key is the
    content hash (sorted server names + URLs via file bytes), not the
    path — same credentials serialize even across sessions.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon.engine_server import EngineServicer, _galaxy_cfg_content_key

    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    servicer = EngineServicer()
    cfg_a = tmp_path / "sess-aaa.cfg"
    cfg_b = tmp_path / "sess-bbb.cfg"
    cfg_a.write_text("[galaxy]\nserver_list = hub\n")
    cfg_b.write_text("[galaxy]\nserver_list = hub\n")
    assert _galaxy_cfg_content_key(cfg_a) == _galaxy_cfg_content_key(cfg_b)
    assert servicer._get_galaxy_proxy_cfg_lock_for(cfg_a) is servicer._get_galaxy_proxy_cfg_lock_for(cfg_b)
    # Same servers in a different order still share (sorted names+urls).
    cfg_c = tmp_path / "sess-ccc.cfg"
    cfg_c.write_text(
        "[galaxy]\nserver_list = b,a\n"
        "[galaxy_server.b]\nurl = https://b.example\n"
        "[galaxy_server.a]\nurl = https://a.example\n"
    )
    cfg_d = tmp_path / "sess-ddd.cfg"
    cfg_d.write_text(
        "[galaxy]\nserver_list = a,b\n"
        "[galaxy_server.a]\nurl = https://a.example\n"
        "[galaxy_server.b]\nurl = https://b.example\n"
    )
    assert _galaxy_cfg_content_key(cfg_c) == _galaxy_cfg_content_key(cfg_d)
    assert servicer._get_galaxy_proxy_cfg_lock_for(cfg_c) is servicer._get_galaxy_proxy_cfg_lock_for(cfg_d)
    servicer._clear_galaxy_cfg_locks()


async def test_galaxy_none_config_yields_plain_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """No session config yields a plain env copy.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon.engine_server import EngineServicer

    monkeypatch.setenv("ANSIBLE_CONFIG", "/tmp/keep.cfg")
    servicer = EngineServicer()
    async with servicer._activate_galaxy_proxy_config(None) as env:
        assert env["ANSIBLE_CONFIG"] == "/tmp/keep.cfg"
        assert os.environ["ANSIBLE_CONFIG"] == "/tmp/keep.cfg"


async def test_galaxy_cfg_lock_eviction_skips_held_locks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LRU eviction drops the oldest UNLOCKED config lock, never a held one.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.daemon.engine_server as engine_server

    monkeypatch.setattr(engine_server, "_MAX_GALAXY_CFG_LOCKS", 2)
    servicer = engine_server.EngineServicer()
    servicer._clear_galaxy_cfg_locks()
    cfg_held = tmp_path / "held.cfg"
    cfg_idle = tmp_path / "idle.cfg"
    cfg_new = tmp_path / "new.cfg"
    cfg_held.write_text("[galaxy]\nserver_list = held\n")
    cfg_idle.write_text("[galaxy]\nserver_list = idle\n")
    cfg_new.write_text("[galaxy]\nserver_list = new\n")
    held = servicer._get_galaxy_proxy_cfg_lock_for(cfg_held)
    idle = servicer._get_galaxy_proxy_cfg_lock_for(cfg_idle)
    await held.acquire()
    try:
        new_lock = servicer._get_galaxy_proxy_cfg_lock_for(cfg_new)
        assert new_lock is not held
        assert new_lock is not idle
        # The held lock survives; the idle one was evicted instead.
        assert servicer._get_galaxy_proxy_cfg_lock_for(cfg_held) is held
    finally:
        held.release()
        servicer._clear_galaxy_cfg_locks()


async def test_galaxy_cfg_lock_all_held_grows_over_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When every config lock is held the map grows over cap (no live drop).

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.daemon.engine_server as engine_server

    monkeypatch.setattr(engine_server, "_MAX_GALAXY_CFG_LOCKS", 2)
    servicer = engine_server.EngineServicer()
    servicer._clear_galaxy_cfg_locks()
    cfgs = []
    for name in ("o1", "o2", "o3"):
        cfg = tmp_path / f"{name}.cfg"
        cfg.write_text(f"[galaxy]\nserver_list = {name}\n")
        cfgs.append(cfg)
    first = servicer._get_galaxy_proxy_cfg_lock_for(cfgs[0])
    second = servicer._get_galaxy_proxy_cfg_lock_for(cfgs[1])
    await first.acquire()
    await second.acquire()
    try:
        third = servicer._get_galaxy_proxy_cfg_lock_for(cfgs[2])
        assert servicer._galaxy_cfg_locks is not None
        assert len(servicer._galaxy_cfg_locks) == 3
        assert servicer._get_galaxy_proxy_cfg_lock_for(cfgs[0]) is first
        assert servicer._get_galaxy_proxy_cfg_lock_for(cfgs[1]) is second
        assert servicer._get_galaxy_proxy_cfg_lock_for(cfgs[2]) is third
    finally:
        first.release()
        second.release()
        servicer._clear_galaxy_cfg_locks()


async def test_concurrent_public_galaxy_no_read_wedge() -> None:
    """A slow public-Galaxy build for one sid never wedges another sid's fan-out.

    Evidence for the #7 verdict (refuted): generation guards are keyed per
    ``(session_id, core_version)``
    (``venv_manager/session.py::get_generation_lock``), and the pipeline
    releases the venv-acquire write guard before the validator fan-out
    takes the read guard (``engine_server.py::_execute_scan_pipeline`` —
    sequential ``async with`` blocks, never nested). Public-Galaxy sessions
    share the ``"__none__"`` config lock only during venv acquisition,
    never during fan-out. Both guards already carry bounded timeouts (the
    read side below uses the real 60s default), so even a hung peer fails
    fast instead of wedging.
    """
    from apme_engine.daemon.engine_server import EngineServicer
    from apme_engine.venv_manager.session import (
        clear_generation_locks,
        venv_read_guard,
        venv_write_guard,
    )

    clear_generation_locks()
    servicer = EngineServicer()
    servicer._clear_galaxy_cfg_locks()
    try:
        other_read_done = asyncio.Event()
        same_read_done = asyncio.Event()

        async def _other_sid_fanout() -> None:
            # Default 60s guard timeout: must NOT fire.
            async with venv_read_guard("wedge-fanout-sid", "2.17"):
                other_read_done.set()

        async def _same_sid_fanout() -> None:
            async with venv_read_guard("wedge-build-sid", "2.17", timeout=5.0):
                same_read_done.set()

        async with (
            venv_write_guard("wedge-build-sid", "2.17"),
            servicer._get_galaxy_proxy_cfg_lock_for(None),
        ):
            # Simulate a slow public-Galaxy download: holds this sid's
            # write guard plus the shared "__none__" config lock.
            other = asyncio.create_task(_other_sid_fanout())
            same = asyncio.create_task(_same_sid_fanout())
            try:
                await asyncio.wait_for(other_read_done.wait(), timeout=5.0)
                # The write guard really is held for the same key (a
                # same-sid reader would wedge): it must NOT finish while
                # the build holds the guard.
                await asyncio.sleep(0.3)
                assert not same_read_done.is_set()
            finally:
                same.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await same
                await other
        # Once the build releases the write guard, the same-sid reader
        # proceeds (sequential acquire/release, no wedge).
        await asyncio.wait_for(_same_sid_fanout(), timeout=5.0)
        assert same_read_done.is_set()
    finally:
        servicer._clear_galaxy_cfg_locks()
        clear_generation_locks()
