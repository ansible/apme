"""Tests for session-scoped venv manager (multi-version layout)."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.venv_manager.session import VenvSessionManager

_PATCH_CREATE = "apme_engine.venv_manager.session.create_base_venv"
_PATCH_INSTALL = "apme_engine.venv_manager.session.install_collections_incremental"
_INSTALL_RETURN: list[str] = []


def _fake_create_base_venv(venv_dir: Path, ansible_core_version: str, **_kw: object) -> None:
    """Create a minimal venv skeleton for testing.

    Args:
        venv_dir: Target directory.
        ansible_core_version: Version string written to pyvenv.cfg.
        **_kw: Absorbed extra kwargs.
    """
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(f"version = {ansible_core_version}\n")
    lib = venv_dir / "lib" / "python3.12" / "site-packages" / "ansible_collections"
    lib.mkdir(parents=True)
    bindir = venv_dir / "bin"
    bindir.mkdir()
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


class TestAcquireColdStart:
    """Tests for first-time venv creation."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_creates_venv_with_version_layout(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Cold start creates ``<sid>/<version>/venv/`` layout.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        session = manager.acquire("my-project", "2.17")
        assert session.session_id == "my-project"
        assert session.ansible_version == "2.17.0"
        assert session.venv_root.is_dir()
        assert session.venv_root == manager.sessions_root / "my-project" / "2.17.0" / "venv"
        mock_create.assert_called_once()

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_installs_collections_on_create(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Cold start installs requested collections.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        specs = ["community.general", "ansible.posix"]
        session = manager.acquire("sid", "2.17", collection_specs=specs)
        mock_install.assert_called_once()
        assert sorted(session.installed_collections) == sorted(specs)

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_no_install_when_no_specs(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """No install_collections_incremental call when specs are empty.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        mock_install.assert_not_called()


class TestWarmHit:
    """Tests for reusing an existing venv (all collections already installed)."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_reuses_existing_venv(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Second acquire with same specs reuses venv without rebuilding.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        specs = ["ansible.posix"]
        s1 = manager.acquire("sid", "2.17", collection_specs=specs)
        s2 = manager.acquire("sid", "2.17", collection_specs=specs)
        assert s1.venv_root == s2.venv_root
        assert mock_create.call_count == 1
        mock_install.assert_called_once()

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_warm_hit_updates_last_used(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Warm hit updates last_used_at timestamp.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        s1 = manager.acquire("sid", "2.17")
        t1 = s1.last_used_at
        time.sleep(0.05)
        s2 = manager.acquire("sid", "2.17")
        assert s2.last_used_at > t1


class TestIncrementalInstall:
    """Tests for additive collection installs."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_installs_only_delta(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Second acquire with new specs installs only the missing ones.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17", collection_specs=["a.b"])
        mock_install.reset_mock()

        session = manager.acquire("sid", "2.17", collection_specs=["a.b", "c.d"])
        args = mock_install.call_args
        installed_specs = args[0][1]
        assert "c.d" in installed_specs
        assert "a.b" not in installed_specs
        assert sorted(session.installed_collections) == ["a.b", "c.d"]

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_no_install_when_subset(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """No incremental install when requested specs are a subset of installed.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17", collection_specs=["a.b", "c.d"])
        mock_install.reset_mock()

        manager.acquire("sid", "2.17", collection_specs=["a.b"])
        mock_install.assert_not_called()


class TestMultiVersion:
    """Tests for tox-style multi-version coexistence."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_different_versions_coexist(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Different core versions create sibling venvs, both remain.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        s1 = manager.acquire("sid", "2.17")
        s2 = manager.acquire("sid", "2.18")
        assert s1.venv_root != s2.venv_root
        assert s1.venv_root.is_dir()
        assert s2.venv_root.is_dir()
        assert mock_create.call_count == 2

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_get_specific_version(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """get(sid, version) returns the correct core-version venv.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        manager.acquire("sid", "2.18")
        v17 = manager.get("sid", "2.17")
        v18 = manager.get("sid", "2.18")
        assert v17 is not None
        assert v18 is not None
        assert v17.ansible_version == "2.17.0"
        assert v18.ansible_version == "2.18.0"

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_get_most_recent(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """get(sid) without version returns the most recently used.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        time.sleep(0.05)
        manager.acquire("sid", "2.18")
        latest = manager.get("sid")
        assert latest is not None
        assert latest.ansible_version == "2.18.0"


class TestTTLReaping:
    """Tests for per-version TTL reaping."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_reap_expired_version(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        sessions_root: Path,
    ) -> None:
        """Expired venvs are reaped; session dir removed when empty.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            sessions_root: Temporary sessions root fixture.
        """
        mgr = VenvSessionManager(sessions_root=sessions_root, ttl_seconds=0)
        mgr.acquire("sid", "2.17")
        time.sleep(0.05)
        count = mgr.reap_expired()
        assert count == 1
        assert mgr.get("sid", "2.17") is None
        assert not (sessions_root / "sid").exists()

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_reap_keeps_fresh(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Fresh venvs are not reaped.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        count = manager.reap_expired()
        assert count == 0
        assert manager.get("sid", "2.17") is not None

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_reap_independent_versions(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        sessions_root: Path,
    ) -> None:
        """Individual versions expire independently within a session.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            sessions_root: Temporary sessions root fixture.
        """
        mgr = VenvSessionManager(sessions_root=sessions_root, ttl_seconds=1)
        mgr.acquire("sid", "2.17")
        time.sleep(0.05)
        mgr.acquire("sid", "2.18")

        meta_path = sessions_root / "sid" / "2.17.0" / "meta.json"
        data = json.loads(meta_path.read_text())
        data["last_used_at"] = time.time() - 10
        meta_path.write_text(json.dumps(data))

        count = mgr.reap_expired()
        assert count == 1
        assert mgr.get("sid", "2.17") is None
        assert mgr.get("sid", "2.18") is not None
        assert (sessions_root / "sid").is_dir()


class TestTouchAndRelease:
    """Tests for touch and release operations."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_touch_updates_all_versions(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Touch updates last_used_at on all core-version venvs.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        manager.acquire("sid", "2.18")
        time.sleep(0.05)
        assert manager.touch("sid") is True
        v17 = manager.get("sid", "2.17")
        v18 = manager.get("sid", "2.18")
        assert v17 is not None and v18 is not None
        assert abs(v17.last_used_at - v18.last_used_at) < 0.1

    def test_touch_nonexistent(self, manager: VenvSessionManager) -> None:
        """Touch nonexistent session returns False.

        Args:
            manager: VenvSessionManager fixture.
        """
        assert manager.touch("nope") is False

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_release_is_noop(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Release is a no-op for named sessions (TTL handles cleanup).

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        assert manager.release("sid") is True
        assert manager.get("sid", "2.17") is not None


class TestListAndDelete:
    """Tests for list_sessions and delete."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_list_all_versions(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """list_sessions returns entries across all sessions and versions.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("a", "2.17")
        time.sleep(0.05)
        manager.acquire("a", "2.18")
        time.sleep(0.05)
        manager.acquire("b", "2.17")
        sessions = manager.list_sessions()
        assert len(sessions) == 3
        assert sessions[0].session_id == "b"

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_delete_removes_all_versions(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Delete removes the entire session directory (all versions).

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        manager.acquire("sid", "2.18")
        assert manager.delete("sid") is True
        assert manager.get("sid") is None

    def test_delete_nonexistent(self, manager: VenvSessionManager) -> None:
        """Delete nonexistent session returns False.

        Args:
            manager: VenvSessionManager fixture.
        """
        assert manager.delete("nope") is False


class TestMetadata:
    """Tests for metadata persistence."""

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_version_meta_file(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Per-version meta.json is written with correct fields.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17", collection_specs=["ansible.posix"])
        meta = manager.sessions_root / "sid" / "2.17.0" / "meta.json"
        assert meta.is_file()
        data = json.loads(meta.read_text())
        assert data["session_id"] == "sid"
        assert data["ansible_version"] == "2.17.0"
        assert "ansible.posix" in data["installed_collections"]

    @patch(_PATCH_INSTALL, return_value=_INSTALL_RETURN)
    @patch(_PATCH_CREATE, side_effect=_fake_create_base_venv)
    def test_session_json_exists(
        self,
        mock_create: MagicMock,
        mock_install: MagicMock,
        manager: VenvSessionManager,
    ) -> None:
        """Session-level session.json is created on first acquire.

        Args:
            mock_create: Patched create_base_venv.
            mock_install: Patched install_collections_incremental.
            manager: VenvSessionManager fixture.
        """
        manager.acquire("sid", "2.17")
        session_json = manager.sessions_root / "sid" / "session.json"
        assert session_json.is_file()
        data = json.loads(session_json.read_text())
        assert "created_at" in data
        assert "last_used_at" in data

    def test_corrupt_metadata_returns_none(self, manager: VenvSessionManager) -> None:
        """Corrupt meta.json is handled gracefully.

        Args:
            manager: VenvSessionManager fixture.
        """
        ver_dir = manager.sessions_root / "corrupt" / "2.17.0"
        ver_dir.mkdir(parents=True)
        (ver_dir / "meta.json").write_text("not json")
        assert manager.get("corrupt", "2.17") is None


class TestProtoRoundTrip:
    """Verify proto fields carry session_id and venv_path."""

    def test_validate_request_venv_path(self) -> None:
        """ValidateRequest carries venv_path field."""
        from apme.v1.validate_pb2 import ValidateRequest

        req = ValidateRequest(
            request_id="r1",
            session_id="ws-abc",
            venv_path="/sessions/ws-abc/2.17.0/venv",
        )
        assert req.session_id == "ws-abc"
        assert req.venv_path == "/sessions/ws-abc/2.17.0/venv"

    def test_scan_options_session_id(self) -> None:
        """ScanOptions carries session_id field."""
        from apme.v1.primary_pb2 import ScanOptions

        opts = ScanOptions(session_id="ws-abc", ansible_core_version="2.17.0")
        assert opts.session_id == "ws-abc"

    def test_fix_options_session_id(self) -> None:
        """FixOptions carries session_id field."""
        from apme.v1.primary_pb2 import FixOptions

        opts = FixOptions(session_id="ci-job-42", ansible_core_version="2.17.0")
        assert opts.session_id == "ci-job-42"


class TestListInstalledPackages:
    """Tests for list_installed_packages (pip list enumeration)."""

    def test_parses_json_output(self, tmp_path: Path) -> None:
        """Parses importlib.metadata JSON output into (name, version, license, supplier) tuples.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_packages

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        pkg_output = json.dumps(
            [
                {
                    "name": "ansible-core",
                    "version": "2.20.0",
                    "license": "GPL-3.0-or-later",
                    "supplier": "Ansible Project",
                },
                {"name": "jinja2", "version": "3.1.4", "license": "BSD-3-Clause", "supplier": "Pallets"},
            ]
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = pkg_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            pkgs = list_installed_packages(venv)

        assert pkgs == [
            ("ansible-core", "2.20.0", "GPL-3.0-or-later", "Ansible Project"),
            ("jinja2", "3.1.4", "BSD-3-Clause", "Pallets"),
        ]

    def test_returns_empty_on_missing_venv(self, tmp_path: Path) -> None:
        """Returns empty list if venv has no python executable.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_packages

        pkgs = list_installed_packages(tmp_path / "nonexistent")
        assert pkgs == []

    def test_returns_empty_on_subprocess_failure(self, tmp_path: Path) -> None:
        """Returns empty list when pip list returns non-zero.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_packages

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "pip not found"

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            pkgs = list_installed_packages(venv)

        assert pkgs == []


class TestGetDependencyTree:
    """Tests for get_dependency_tree (uv pip tree enumeration)."""

    def test_returns_tree_output(self, tmp_path: Path) -> None:
        """Returns stripped stdout from uv pip tree.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import get_dependency_tree

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        tree_text = "ansible-core v2.20.0\n├── jinja2 v3.1.4\n└── pyyaml v6.0.3\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = tree_text

        with (
            patch("apme_engine.venv_manager.session._uv_available", return_value=True),
            patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result),
        ):
            tree = get_dependency_tree(venv)

        assert "ansible-core v2.20.0" in tree
        assert "jinja2 v3.1.4" in tree

    def test_returns_empty_when_uv_unavailable(self, tmp_path: Path) -> None:
        """Returns empty string when uv is not on PATH.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import get_dependency_tree

        with patch("apme_engine.venv_manager.session._uv_available", return_value=False):
            tree = get_dependency_tree(tmp_path / "venv")

        assert tree == ""

    def test_returns_empty_on_failure(self, tmp_path: Path) -> None:
        """Returns empty string when uv pip tree returns non-zero.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import get_dependency_tree

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with (
            patch("apme_engine.venv_manager.session._uv_available", return_value=True),
            patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result),
        ):
            tree = get_dependency_tree(venv)

        assert tree == ""


class TestListInstalledCollections:
    """Tests for list_installed_collections (ansible-galaxy collection list)."""

    def test_parses_json_with_manifest_metadata(self, tmp_path: Path) -> None:
        """Parses collection list and reads license/supplier from MANIFEST.json.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        coll_path = tmp_path / "collections"
        cg_dir = coll_path / "community" / "general"
        cg_dir.mkdir(parents=True)
        (cg_dir / "MANIFEST.json").write_text(
            json.dumps(
                {
                    "collection_info": {
                        "license": ["GPL-3.0-or-later"],
                        "authors": ["Ansible Project"],
                    }
                }
            )
        )
        ap_dir = coll_path / "ansible" / "posix"
        ap_dir.mkdir(parents=True)
        (ap_dir / "MANIFEST.json").write_text(
            json.dumps(
                {
                    "collection_info": {
                        "license": ["GPL-3.0-or-later"],
                        "namespace": "ansible",
                    }
                }
            )
        )

        galaxy_output = json.dumps(
            {
                str(coll_path): {
                    "community.general": {"version": "8.0.0"},
                    "ansible.posix": {"version": "1.5.4"},
                },
            }
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert ("ansible.posix", "1.5.4", "GPL-3.0-or-later", "ansible") in colls
        assert ("community.general", "8.0.0", "GPL-3.0-or-later", "Ansible Project") in colls

    def test_falls_back_to_galaxy_yml(self, tmp_path: Path) -> None:
        """Reads license/supplier from galaxy.yml when MANIFEST.json is absent.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        coll_path = tmp_path / "collections"
        cg_dir = coll_path / "community" / "general"
        cg_dir.mkdir(parents=True)
        (cg_dir / "galaxy.yml").write_text(
            "license:\n  - GPL-3.0-or-later\nauthors:\n  - Community\nnamespace: community\n"
        )

        galaxy_output = json.dumps({str(coll_path): {"community.general": {"version": "8.0.0"}}})
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert colls == [("community.general", "8.0.0", "GPL-3.0-or-later", "Community")]

    def test_missing_metadata_returns_empty_strings(self, tmp_path: Path) -> None:
        """Returns empty license/supplier when no metadata files exist.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        coll_path = tmp_path / "collections"
        cg_dir = coll_path / "community" / "general"
        cg_dir.mkdir(parents=True)

        galaxy_output = json.dumps({str(coll_path): {"community.general": {"version": "8.0.0"}}})
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert colls == [("community.general", "8.0.0", "", "")]

    def test_star_version_becomes_empty(self, tmp_path: Path) -> None:
        """Version '*' (missing MANIFEST.json) is normalized to empty string.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        galaxy_output = json.dumps(
            {"/p": {"community.general": {"version": "*"}}},
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert colls[0][0] == "community.general"
        assert colls[0][1] == ""

    def test_skips_internal_collections(self, tmp_path: Path) -> None:
        """Internal ansible._vendor-like collections are excluded.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        galaxy_output = json.dumps(
            {"/p": {"ansible._vendor_x": {"version": "1.0.0"}, "real.coll": {"version": "2.0.0"}}},
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert len(colls) == 1
        assert colls[0][0] == "real.coll"
        assert colls[0][1] == "2.0.0"

    def test_returns_empty_on_missing_venv(self, tmp_path: Path) -> None:
        """Returns empty list if venv has no python executable.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        colls = list_installed_collections(tmp_path / "nonexistent")
        assert colls == []

    def test_returns_empty_on_subprocess_failure(self, tmp_path: Path) -> None:
        """Returns empty list when ansible-galaxy returns non-zero.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ansible not found"

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        assert colls == []

    def test_sorted_output(self, tmp_path: Path) -> None:
        """Output is deterministically sorted by FQCN.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from apme_engine.venv_manager.session import list_installed_collections

        venv = tmp_path / "venv"
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").write_text("#!/bin/sh\n")
        (bindir / "python").chmod(0o755)

        galaxy_output = json.dumps(
            {"/p": {"z.z": {"version": "1.0"}, "a.a": {"version": "2.0"}, "m.m": {"version": "3.0"}}},
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = galaxy_output

        with patch("apme_engine.venv_manager.session.subprocess.run", return_value=mock_result):
            colls = list_installed_collections(venv)

        fqcns = [c[0] for c in colls]
        assert fqcns == ["a.a", "m.m", "z.z"]
