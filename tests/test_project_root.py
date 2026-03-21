"""Tests for CLI project root discovery and session ID derivation."""

from __future__ import annotations

from pathlib import Path

from apme_engine.cli._project_root import (
    _PROJECT_MARKERS,
    derive_session_id,
    discover_project_root,
)


class TestDiscoverProjectRoot:
    """Tests for discover_project_root()."""

    def test_git_root(self, tmp_path: Path) -> None:
        """Finds the .git directory as the project root.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "roles" / "web"
        subdir.mkdir(parents=True)

        assert discover_project_root(subdir) == tmp_path

    def test_galaxy_yml(self, tmp_path: Path) -> None:
        """Finds galaxy.yml as the project root.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / "galaxy.yml").touch()
        subdir = tmp_path / "plugins" / "modules"
        subdir.mkdir(parents=True)

        assert discover_project_root(subdir) == tmp_path

    def test_requirements_yml(self, tmp_path: Path) -> None:
        """Finds requirements.yml as the project root.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / "requirements.yml").touch()
        subdir = tmp_path / "playbooks"
        subdir.mkdir(parents=True)

        assert discover_project_root(subdir) == tmp_path

    def test_ansible_cfg(self, tmp_path: Path) -> None:
        """Finds ansible.cfg as the project root.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / "ansible.cfg").touch()
        subdir = tmp_path / "inventory"
        subdir.mkdir(parents=True)

        assert discover_project_root(subdir) == tmp_path

    def test_pyproject_toml(self, tmp_path: Path) -> None:
        """Finds pyproject.toml as the project root.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / "pyproject.toml").touch()
        subdir = tmp_path / "src" / "mypackage"
        subdir.mkdir(parents=True)

        assert discover_project_root(subdir) == tmp_path

    def test_closest_marker_wins(self, tmp_path: Path) -> None:
        """The nearest marker to the target wins, not the farthest.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / ".git").mkdir()
        inner = tmp_path / "collections" / "my_collection"
        inner.mkdir(parents=True)
        (inner / "galaxy.yml").touch()

        assert discover_project_root(inner) == inner

    def test_file_target_resolves_to_parent(self, tmp_path: Path) -> None:
        """When target is a file, walks from its parent directory.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / ".git").mkdir()
        playbook = tmp_path / "site.yml"
        playbook.touch()

        assert discover_project_root(playbook) == tmp_path

    def test_no_marker_returns_target(self, tmp_path: Path) -> None:
        """Falls back to the target directory when no marker is found.

        Args:
            tmp_path: Pytest temporary directory.
        """
        subdir = tmp_path / "orphan"
        subdir.mkdir()

        assert discover_project_root(subdir) == subdir

    def test_marker_priority_git_over_pyproject(self, tmp_path: Path) -> None:
        """When .git and pyproject.toml coexist, .git is found first (same dir).

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / ".git").mkdir()
        (tmp_path / "pyproject.toml").touch()

        assert discover_project_root(tmp_path) == tmp_path

    def test_all_markers_recognized(self) -> None:
        """Smoke test: the marker list hasn't accidentally been emptied."""
        assert len(_PROJECT_MARKERS) >= 5


class TestDeriveSessionId:
    """Tests for derive_session_id()."""

    def test_deterministic(self, tmp_path: Path) -> None:
        """Same path always produces the same session ID.

        Args:
            tmp_path: Pytest temporary directory.
        """
        sid1 = derive_session_id(tmp_path)
        sid2 = derive_session_id(tmp_path)
        assert sid1 == sid2

    def test_length(self, tmp_path: Path) -> None:
        """Session ID is exactly 16 hex characters.

        Args:
            tmp_path: Pytest temporary directory.
        """
        sid = derive_session_id(tmp_path)
        assert len(sid) == 16
        int(sid, 16)  # must be valid hex

    def test_different_paths_differ(self, tmp_path: Path) -> None:
        """Different project roots produce different session IDs.

        Args:
            tmp_path: Pytest temporary directory.
        """
        a = tmp_path / "project_a"
        b = tmp_path / "project_b"
        a.mkdir()
        b.mkdir()

        assert derive_session_id(a) != derive_session_id(b)

    def test_stable_across_subdirs(self, tmp_path: Path) -> None:
        """Scanning from different subdirs of the same project yields the same ID.

        Args:
            tmp_path: Pytest temporary directory.
        """
        (tmp_path / ".git").mkdir()
        sub_a = tmp_path / "roles"
        sub_b = tmp_path / "playbooks"
        sub_a.mkdir()
        sub_b.mkdir()

        root_a = discover_project_root(sub_a)
        root_b = discover_project_root(sub_b)
        assert root_a == root_b
        assert derive_session_id(root_a) == derive_session_id(root_b)
