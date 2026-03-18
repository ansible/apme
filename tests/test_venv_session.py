"""Tests for session-scoped venv manager."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.collection_cache.venv_session import VenvSessionManager


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


def _mock_build_venv(
    ansible_core_version: str,
    collection_specs: list[str],
    venvs_root: Path | None = None,
    **kwargs: object,
) -> Path:
    """Create a minimal venv directory structure for testing.

    Args:
        ansible_core_version: Version string written to pyvenv.cfg.
        collection_specs: Ignored (present for API compat with build_venv).
        venvs_root: Directory in which to create the fake venv.
        **kwargs: Absorbed additional keyword arguments.

    Returns:
        Path to the created fake venv directory.
    """
    assert venvs_root is not None
    venv_dir = venvs_root / "fakehash"
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(f"version = {ansible_core_version}\n")
    lib = venv_dir / "lib" / "python3.12" / "site-packages" / "ansible_collections"
    lib.mkdir(parents=True)
    bindir = venv_dir / "bin"
    bindir.mkdir()
    (bindir / "python").touch()
    (bindir / "ansible-doc").touch()
    return venv_dir


class TestAcquireEphemeral:
    """Tests for ephemeral (no session_id) venv acquisition."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_creates_venv(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Creates venv.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        session = manager.acquire(ansible_version="2.20")
        assert session.venv_root.is_dir()
        assert session.ansible_version == "2.20.0"
        assert session.ephemeral is True
        assert session.session_id  # non-empty

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_each_call_gets_unique_id(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Each call gets unique id.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        s1 = manager.acquire(ansible_version="2.20")
        s2 = manager.acquire(ansible_version="2.20")
        assert s1.session_id != s2.session_id

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_release_deletes_ephemeral(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Release deletes ephemeral.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        session = manager.acquire(ansible_version="2.20")
        session_dir = manager.sessions_root / session.session_id
        assert session_dir.is_dir()
        manager.release(session.session_id)
        assert not session_dir.exists()


class TestAcquireNamed:
    """Tests for named (session_id provided) venv acquisition."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_creates_named_session(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Creates named session.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        session = manager.acquire(ansible_version="2.20", session_id="my-project")
        assert session.session_id == "my-project"
        assert session.ephemeral is False
        assert session.venv_root.is_dir()

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_reuses_existing_session(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Reuses existing session.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        s1 = manager.acquire(ansible_version="2.20", session_id="reuse-me")
        s2 = manager.acquire(ansible_version="2.20", session_id="reuse-me")
        assert s1.venv_root == s2.venv_root
        assert mock_bv.call_count == 1  # only built once

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_rebuilds_on_version_change(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Rebuilds on version change.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="version-test")
        manager.acquire(ansible_version="2.18", session_id="version-test")
        assert mock_bv.call_count == 2

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_release_keeps_named(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Release keeps named.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="keep-me")
        session_dir = manager.sessions_root / "keep-me"
        manager.release("keep-me")
        assert session_dir.is_dir()


class TestTouch:
    """Tests for session touch (TTL refresh)."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_touch_updates_last_used(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Touch updates last used.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        session = manager.acquire(ansible_version="2.20", session_id="touch-test")
        old_ts = session.last_used_at
        time.sleep(0.05)
        manager.touch("touch-test")
        updated = manager.get("touch-test")
        assert updated is not None
        assert updated.last_used_at > old_ts

    def test_touch_nonexistent_returns_false(self, manager: VenvSessionManager) -> None:
        """Verify Touch nonexistent returns false.

        Args:
            manager: VenvSessionManager fixture.
        """
        assert manager.touch("nonexistent") is False


class TestListAndGet:
    """Tests for listing and getting sessions."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_list_sessions(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify List sessions.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="first")
        time.sleep(0.05)
        manager.acquire(ansible_version="2.20", session_id="second")
        sessions = manager.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].session_id == "second"
        assert sessions[1].session_id == "first"

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_get_existing(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Get existing.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="get-me")
        session = manager.get("get-me")
        assert session is not None
        assert session.session_id == "get-me"

    def test_get_nonexistent(self, manager: VenvSessionManager) -> None:
        """Verify Get nonexistent.

        Args:
            manager: VenvSessionManager fixture.
        """
        assert manager.get("nope") is None


class TestDeleteAndReap:
    """Tests for session deletion and expiry reaping."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_delete(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Delete.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="delete-me")
        assert manager.delete("delete-me") is True
        assert manager.get("delete-me") is None

    def test_delete_nonexistent(self, manager: VenvSessionManager) -> None:
        """Verify Delete nonexistent.

        Args:
            manager: VenvSessionManager fixture.
        """
        assert manager.delete("nope") is False

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_reap_expired(self, mock_bv: MagicMock, sessions_root: Path) -> None:
        """Verify Reap expired.

        Args:
            mock_bv: Patched build_venv function.
            sessions_root: Temporary sessions root fixture.
        """
        mgr = VenvSessionManager(sessions_root=sessions_root, ttl_seconds=0)
        mgr.acquire(ansible_version="2.20", session_id="old-session")
        time.sleep(0.05)
        count = mgr.reap_expired()
        assert count == 1
        assert mgr.get("old-session") is None

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_reap_keeps_fresh(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Reap keeps fresh.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="fresh-session")
        count = manager.reap_expired()
        assert count == 0
        assert manager.get("fresh-session") is not None


class TestCollectionSpecs:
    """Tests for collection spec handling."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_specs_stored_in_metadata(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Specs stored in metadata.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        specs = ["community.general:9.0.0", "amazon.aws"]
        manager.acquire(ansible_version="2.20", collection_specs=specs, session_id="with-colls")
        session = manager.get("with-colls")
        assert session is not None
        assert sorted(session.collection_specs) == sorted(specs)

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_spec_change_triggers_rebuild(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Spec change triggers rebuild.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", collection_specs=["a.b"], session_id="spec-test")
        manager.acquire(ansible_version="2.20", collection_specs=["a.b", "c.d"], session_id="spec-test")
        assert mock_bv.call_count == 2


class TestMetadataPersistence:
    """Tests for JSON metadata read/write."""

    @patch("apme_engine.collection_cache.venv_session.build_venv", side_effect=_mock_build_venv)
    def test_metadata_file_exists(self, mock_bv: MagicMock, manager: VenvSessionManager) -> None:
        """Verify Metadata file exists.

        Args:
            mock_bv: Patched build_venv function.
            manager: VenvSessionManager fixture.
        """
        manager.acquire(ansible_version="2.20", session_id="meta-test")
        meta_path = manager.sessions_root / "meta-test" / "session.json"
        assert meta_path.is_file()
        data = json.loads(meta_path.read_text())
        assert data["session_id"] == "meta-test"
        assert data["ansible_version"] == "2.20.0"

    def test_corrupt_metadata_returns_none(self, manager: VenvSessionManager) -> None:
        """Verify Corrupt metadata returns none.

        Args:
            manager: VenvSessionManager fixture.
        """
        session_dir = manager.sessions_root / "corrupt"
        session_dir.mkdir()
        (session_dir / "session.json").write_text("not json")
        assert manager.get("corrupt") is None
