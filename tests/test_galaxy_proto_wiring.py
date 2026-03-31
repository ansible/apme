"""Tests for Galaxy server proto wiring (ADR-045 PR2).

Verifies that GalaxyServerDef flows through ScanOptions, FixOptions,
and the Primary's session-scoped config writer.
"""

from __future__ import annotations

import configparser

from apme.v1.common_pb2 import GalaxyServerDef
from apme.v1.primary_pb2 import FixOptions, ScanChunk, ScanOptions
from apme_engine.daemon.primary_server import _write_session_galaxy_cfg


class TestGalaxyServerDefProto:
    """Tests for the GalaxyServerDef proto message."""

    def test_roundtrip_fields(self) -> None:
        """All fields survive serialization."""
        msg = GalaxyServerDef(
            name="hub",
            url="https://hub.example.com/api/galaxy/",
            token="secret-token",
            auth_url="https://sso.example.com/token",
        )
        data = msg.SerializeToString()
        parsed = GalaxyServerDef()
        parsed.ParseFromString(data)

        assert parsed.name == "hub"
        assert parsed.url == "https://hub.example.com/api/galaxy/"
        assert parsed.token == "secret-token"
        assert parsed.auth_url == "https://sso.example.com/token"

    def test_optional_fields_default_empty(self) -> None:
        """Token and auth_url default to empty strings."""
        msg = GalaxyServerDef(name="public", url="https://galaxy.ansible.com/")
        assert msg.token == ""
        assert msg.auth_url == ""


class TestScanOptionsGalaxyServers:
    """Tests for galaxy_servers on ScanOptions."""

    def test_populate_galaxy_servers(self) -> None:
        """Galaxy servers are set on ScanOptions and survive ScanChunk."""
        servers = [
            GalaxyServerDef(name="hub", url="https://hub.example.com/"),
            GalaxyServerDef(name="galaxy", url="https://galaxy.ansible.com/"),
        ]
        opts = ScanOptions(ansible_core_version="2.18")
        opts.galaxy_servers.extend(servers)

        assert len(opts.galaxy_servers) == 2
        assert opts.galaxy_servers[0].name == "hub"
        assert opts.galaxy_servers[1].name == "galaxy"

        chunk = ScanChunk(scan_id="test", options=opts, last=True)
        assert chunk.options is not None
        assert len(chunk.options.galaxy_servers) == 2

    def test_empty_galaxy_servers(self) -> None:
        """ScanOptions with no galaxy_servers has empty repeated field."""
        opts = ScanOptions()
        assert len(opts.galaxy_servers) == 0


class TestFixOptionsGalaxyServers:
    """Tests for galaxy_servers on FixOptions."""

    def test_populate_galaxy_servers(self) -> None:
        """Galaxy servers are set on FixOptions and survive ScanChunk."""
        servers = [
            GalaxyServerDef(
                name="hub",
                url="https://hub.example.com/",
                token="tok",
                auth_url="https://sso.example.com/token",
            ),
        ]
        fix = FixOptions(max_passes=3, galaxy_servers=servers)

        assert len(fix.galaxy_servers) == 1
        assert fix.galaxy_servers[0].token == "tok"
        assert fix.galaxy_servers[0].auth_url == "https://sso.example.com/token"

        chunk = ScanChunk(scan_id="test", fix_options=fix, last=True)
        assert chunk.fix_options is not None
        assert len(chunk.fix_options.galaxy_servers) == 1


class TestWriteSessionGalaxyCfg:
    """Tests for _write_session_galaxy_cfg helper."""

    def test_writes_valid_ansible_cfg(self) -> None:
        """Writes a temp ansible.cfg from proto GalaxyServerDef messages."""
        servers = [
            GalaxyServerDef(
                name="hub",
                url="https://hub.example.com/api/galaxy/",
                token="my-token",
                auth_url="https://sso.example.com/token",
            ),
            GalaxyServerDef(
                name="public",
                url="https://galaxy.ansible.com/api/",
            ),
        ]

        cfg_path = _write_session_galaxy_cfg(servers)
        assert cfg_path is not None
        assert cfg_path.is_file()
        assert cfg_path.name == "ansible.cfg"

        parser = configparser.ConfigParser()
        parser.read(str(cfg_path))

        assert parser.get("galaxy", "server_list") == "hub,public"
        assert parser.get("galaxy_server.hub", "url") == "https://hub.example.com/api/galaxy/"
        assert parser.get("galaxy_server.hub", "token") == "my-token"
        assert parser.get("galaxy_server.hub", "auth_url") == "https://sso.example.com/token"
        assert parser.get("galaxy_server.public", "url") == "https://galaxy.ansible.com/api/"
        assert not parser.has_option("galaxy_server.public", "token")

        # Cleanup
        import shutil

        shutil.rmtree(cfg_path.parent)

    def test_returns_none_for_empty(self) -> None:
        """Returns None when no servers are provided."""
        assert _write_session_galaxy_cfg([]) is None
        assert _write_session_galaxy_cfg(()) is None

    def test_skips_servers_without_url(self) -> None:
        """Servers missing a url are filtered out."""
        servers = [GalaxyServerDef(name="broken", url="")]
        assert _write_session_galaxy_cfg(servers) is None

    def test_strips_whitespace_urls(self) -> None:
        """Whitespace-only URLs are treated as empty and skipped."""
        servers = [GalaxyServerDef(name="ws", url="   ")]
        assert _write_session_galaxy_cfg(servers) is None

    def test_deduplicates_server_names(self) -> None:
        """Duplicate server names get a suffix to avoid ValueError."""
        import shutil

        servers = [
            GalaxyServerDef(name="hub", url="https://hub1.example.com/"),
            GalaxyServerDef(name="hub", url="https://hub2.example.com/"),
        ]
        cfg_path = _write_session_galaxy_cfg(servers)
        assert cfg_path is not None

        parser = configparser.ConfigParser()
        parser.read(str(cfg_path))

        server_list = parser.get("galaxy", "server_list")
        assert "hub" in server_list
        assert "hub_1" in server_list
        assert parser.get("galaxy_server.hub", "url") == "https://hub1.example.com/"
        assert parser.get("galaxy_server.hub_1", "url") == "https://hub2.example.com/"

        shutil.rmtree(cfg_path.parent)
