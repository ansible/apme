"""Tests for CLI Galaxy server config parsing (ADR-045 PR2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.cli._galaxy_config import (
    discover_galaxy_servers,
    parse_galaxy_servers,
    resolve_ansible_cfg,
)


class TestResolveAnsibleCfg:
    """Tests for resolve_ansible_cfg()."""

    def test_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANSIBLE_CONFIG env var takes priority.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        cfg = tmp_path / "custom.cfg"
        cfg.write_text("[defaults]\n")
        monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))

        assert resolve_ansible_cfg() == cfg

    def test_env_var_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANSIBLE_CONFIG pointing to nonexistent file is skipped.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setenv("ANSIBLE_CONFIG", str(tmp_path / "nope.cfg"))
        assert resolve_ansible_cfg() is None

    def test_project_root_ansible_cfg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Finds ansible.cfg in the project root.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[defaults]\n")

        assert resolve_ansible_cfg(project_root=tmp_path) == cfg

    def test_home_ansible_cfg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to ~/.ansible.cfg.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        home_cfg = tmp_path / ".ansible.cfg"
        home_cfg.write_text("[defaults]\n")

        assert resolve_ansible_cfg() == home_cfg

    def test_no_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when no config exists.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        assert resolve_ansible_cfg(project_root=tmp_path) is None


class TestParseGalaxyServers:
    """Tests for parse_galaxy_servers()."""

    def test_single_server(self, tmp_path: Path) -> None:
        """Parses a single Galaxy server with token.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(
            "[galaxy]\n"
            "server_list = my_galaxy\n"
            "\n"
            "[galaxy_server.my_galaxy]\n"
            "url = https://galaxy.ansible.com/api/\n"
            "token = my-secret-token\n"
        )

        servers = parse_galaxy_servers(cfg)
        assert len(servers) == 1
        assert servers[0].name == "my_galaxy"
        assert servers[0].url == "https://galaxy.ansible.com/api/"
        assert servers[0].token == "my-secret-token"
        assert servers[0].auth_url == ""

    def test_multiple_servers(self, tmp_path: Path) -> None:
        """Parses multiple Galaxy servers in order.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(
            "[galaxy]\n"
            "server_list = hub, public_galaxy\n"
            "\n"
            "[galaxy_server.hub]\n"
            "url = https://hub.example.com/api/galaxy/\n"
            "token = hub-token\n"
            "auth_url = https://sso.example.com/token\n"
            "\n"
            "[galaxy_server.public_galaxy]\n"
            "url = https://galaxy.ansible.com/api/\n"
        )

        servers = parse_galaxy_servers(cfg)
        assert len(servers) == 2
        assert servers[0].name == "hub"
        assert servers[0].url == "https://hub.example.com/api/galaxy/"
        assert servers[0].token == "hub-token"
        assert servers[0].auth_url == "https://sso.example.com/token"
        assert servers[1].name == "public_galaxy"
        assert servers[1].url == "https://galaxy.ansible.com/api/"
        assert servers[1].token == ""

    def test_no_galaxy_section(self, tmp_path: Path) -> None:
        """Returns empty list when [galaxy] section is absent.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[defaults]\nhost_key_checking = False\n")

        assert parse_galaxy_servers(cfg) == []

    def test_empty_server_list(self, tmp_path: Path) -> None:
        """Returns empty list when server_list is empty.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\nserver_list = \n")

        assert parse_galaxy_servers(cfg) == []

    def test_missing_section(self, tmp_path: Path) -> None:
        """Skips servers whose section is missing.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(
            "[galaxy]\nserver_list = exists, missing\n\n[galaxy_server.exists]\nurl = https://galaxy.example.com/\n"
        )

        servers = parse_galaxy_servers(cfg)
        assert len(servers) == 1
        assert servers[0].name == "exists"

    def test_server_without_url(self, tmp_path: Path) -> None:
        """Skips servers that have no url defined.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\nserver_list = no_url\n\n[galaxy_server.no_url]\ntoken = some-token\n")

        assert parse_galaxy_servers(cfg) == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Returns empty list for a non-existent config path.

        Args:
            tmp_path: Pytest temporary directory.
        """
        assert parse_galaxy_servers(tmp_path / "nope.cfg") == []

    def test_returns_proto_messages(self, tmp_path: Path) -> None:
        """Returned objects are GalaxyServerDef proto messages.

        Args:
            tmp_path: Pytest temporary directory.
        """
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\nserver_list = test\n\n[galaxy_server.test]\nurl = https://galaxy.example.com/\n")

        servers = parse_galaxy_servers(cfg)
        assert len(servers) == 1
        assert isinstance(servers[0], GalaxyServerDef)


class TestDiscoverGalaxyServers:
    """Tests for discover_galaxy_servers()."""

    def test_discovers_from_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Discovers servers from ansible.cfg in the project root.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
        cfg = tmp_path / "ansible.cfg"
        cfg.write_text(
            "[galaxy]\nserver_list = galaxy\n\n[galaxy_server.galaxy]\nurl = https://galaxy.ansible.com/api/\n"
        )

        servers = discover_galaxy_servers(project_root=tmp_path)
        assert len(servers) == 1
        assert servers[0].url == "https://galaxy.ansible.com/api/"

    def test_empty_when_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns empty list when no ansible.cfg exists.

        Args:
            tmp_path: Pytest temporary directory.
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        assert discover_galaxy_servers(project_root=tmp_path) == []
