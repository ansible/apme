"""Tests for galaxy_proxy.collection_downloader (ADR-045)."""

from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from galaxy_proxy.collection_downloader import (
    DownloadResult,
    GalaxyServerConfig,
    _compute_failed_specs,
    _find_tarballs,
    _spec_fqcn,
    convert_tarballs_in_dir,
    download_collections,
    write_temp_ansible_cfg,
)


class TestGalaxyServerConfig:
    """Tests for the GalaxyServerConfig dataclass."""

    def test_minimal_config(self) -> None:
        """Config with only name and url."""
        cfg = GalaxyServerConfig(name="public", url="https://galaxy.ansible.com")
        assert cfg.name == "public"
        assert cfg.url == "https://galaxy.ansible.com"
        assert cfg.token is None
        assert cfg.auth_url is None

    def test_full_config(self) -> None:
        """Config with all fields populated."""
        cfg = GalaxyServerConfig(
            name="hub",
            url="https://hub.example.com/api/galaxy/",
            token="secret",
            auth_url="https://sso.example.com/token",
        )
        assert cfg.token == "secret"
        assert cfg.auth_url == "https://sso.example.com/token"


class TestWriteTempAnsibleCfg:
    """Tests for write_temp_ansible_cfg."""

    def test_single_server(self, tmp_path: Path) -> None:
        """Writes a valid ansible.cfg for a single server.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        servers = [GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com")]
        cfg_path = write_temp_ansible_cfg(servers, tmp_path)

        assert cfg_path.is_file()
        assert oct(cfg_path.stat().st_mode & 0o777) == oct(0o600)
        parser = configparser.ConfigParser()
        parser.read(cfg_path)
        assert parser.get("galaxy", "server_list") == "galaxy"
        assert parser.get("galaxy_server.galaxy", "url") == "https://galaxy.ansible.com"

    def test_multiple_servers_with_auth(self, tmp_path: Path) -> None:
        """Writes ordered multi-server config with tokens and auth_url.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        servers = [
            GalaxyServerConfig(
                name="hub",
                url="https://hub.example.com/api/galaxy/",
                token="tok",
                auth_url="https://sso.example.com/token",
            ),
            GalaxyServerConfig(name="public", url="https://galaxy.ansible.com"),
        ]
        cfg_path = write_temp_ansible_cfg(servers, tmp_path)

        parser = configparser.ConfigParser()
        parser.read(cfg_path)
        assert parser.get("galaxy", "server_list") == "hub,public"
        assert parser.get("galaxy_server.hub", "token") == "tok"
        assert parser.get("galaxy_server.hub", "auth_url") == "https://sso.example.com/token"
        assert not parser.has_option("galaxy_server.public", "token")

    def test_no_token_section(self, tmp_path: Path) -> None:
        """Server without token omits the token key entirely.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        servers = [GalaxyServerConfig(name="pub", url="https://galaxy.ansible.com")]
        cfg_path = write_temp_ansible_cfg(servers, tmp_path)

        parser = configparser.ConfigParser()
        parser.read(cfg_path)
        assert not parser.has_option("galaxy_server.pub", "token")
        assert not parser.has_option("galaxy_server.pub", "auth_url")

    def test_duplicate_server_names_rejected(self, tmp_path: Path) -> None:
        """Duplicate server names raise ValueError.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        servers = [
            GalaxyServerConfig(name="galaxy", url="https://galaxy.ansible.com"),
            GalaxyServerConfig(name="galaxy", url="https://other.example.com"),
        ]
        with pytest.raises(ValueError, match="Duplicate Galaxy server name"):
            write_temp_ansible_cfg(servers, tmp_path)

    def test_empty_server_name_rejected(self, tmp_path: Path) -> None:
        """Empty server name raises ValueError.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        servers = [GalaxyServerConfig(name="", url="https://galaxy.ansible.com")]
        with pytest.raises(ValueError, match="must be non-empty"):
            write_temp_ansible_cfg(servers, tmp_path)


class TestFindTarballs:
    """Tests for _find_tarballs."""

    def test_finds_tarballs(self, tmp_path: Path) -> None:
        """Finds .tar.gz files in a directory.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "ansible-posix-1.5.4.tar.gz").touch()
        (tmp_path / "community-general-9.0.0.tar.gz").touch()
        (tmp_path / "readme.txt").touch()

        results = _find_tarballs(tmp_path)
        assert len(results) == 2
        assert all(p.name.endswith(".tar.gz") for p in results)

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Returns empty list for directory with no tarballs.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        assert _find_tarballs(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns empty list for nonexistent directory.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        assert _find_tarballs(tmp_path / "nope") == []


class TestSpecFqcn:
    """Tests for _spec_fqcn."""

    def test_plain_fqcn(self) -> None:
        """Extracts FQCN from a plain spec."""
        assert _spec_fqcn("community.general") == "community.general"

    def test_versioned_spec(self) -> None:
        """Extracts FQCN from a versioned spec."""
        assert _spec_fqcn("ansible.posix:1.5.4") == "ansible.posix"

    def test_constraint_spec(self) -> None:
        """Extracts FQCN from a spec with version constraint."""
        assert _spec_fqcn("community.general:>=9.0") == "community.general"


class TestComputeFailedSpecs:
    """Tests for _compute_failed_specs."""

    def test_all_downloaded(self, tmp_path: Path) -> None:
        """No failures when all specs have matching tarballs.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarballs = [
            tmp_path / "ansible-posix-1.5.4.tar.gz",
            tmp_path / "community-general-9.0.0.tar.gz",
        ]
        specs = ["ansible.posix", "community.general:>=9.0"]
        assert _compute_failed_specs(specs, tarballs) == []

    def test_partial_failure(self, tmp_path: Path) -> None:
        """Identifies specs missing from downloaded tarballs.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarballs = [tmp_path / "ansible-posix-1.5.4.tar.gz"]
        specs = ["ansible.posix", "community.general"]
        assert _compute_failed_specs(specs, tarballs) == ["community.general"]

    def test_underscored_namespace(self, tmp_path: Path) -> None:
        """Handles namespaces containing underscores correctly.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarballs = [tmp_path / "redhat_cop-controller_configuration-4.0.0.tar.gz"]
        specs = ["redhat_cop.controller_configuration"]
        assert _compute_failed_specs(specs, tarballs) == []

    def test_empty_inputs(self) -> None:
        """Returns empty list for no specs."""
        assert _compute_failed_specs([], []) == []


class TestDownloadCollections:
    """Tests for download_collections (async)."""

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_empty_specs_returns_empty(self) -> None:
        """Empty collection_specs returns immediately with empty result."""
        result = await download_collections([], Path("/tmp/dl"))
        assert result == DownloadResult()
        assert result.tarball_paths == []
        assert result.failed_specs == []

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_successful_download(self, tmp_path: Path) -> None:
        """Successful subprocess produces tarballs in download_dir.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        download_dir = tmp_path / "downloads"

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"OK", b""))

        tarball = download_dir / "community-general-9.0.0.tar.gz"

        async def fake_exec(*_args: object, **_kwargs: object) -> AsyncMock:
            download_dir.mkdir(parents=True, exist_ok=True)
            tarball.touch()
            return mock_process

        with patch("galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await download_collections(
                ["community.general:9.0.0"],
                download_dir,
            )

        assert len(result.tarball_paths) == 1
        assert result.tarball_paths[0].name == "community-general-9.0.0.tar.gz"
        assert result.failed_specs == []

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_binary_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError when ansible-galaxy is not on PATH.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        with patch(
            "galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            result = await download_collections(
                ["ansible.posix"],
                tmp_path / "dl",
                ansible_galaxy_bin="/nonexistent/ansible-galaxy",
            )

        assert result.failed_specs == ["ansible.posix"]
        assert "not found" in result.stderr

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_timeout(self, tmp_path: Path) -> None:
        """TimeoutError produces failed_specs.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=TimeoutError)

        with (
            patch(
                "galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
            patch(
                "galaxy_proxy.collection_downloader.asyncio.wait_for",
                side_effect=TimeoutError,
            ),
        ):
            result = await download_collections(
                ["ansible.posix"],
                tmp_path / "dl",
                timeout=0.1,
            )

        assert result.failed_specs == ["ansible.posix"]

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_uses_ansible_cfg_path(self, tmp_path: Path) -> None:
        """Passes ANSIBLE_CONFIG env when ansible_cfg_path is set.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cfg_path = tmp_path / "ansible.cfg"
        cfg_path.touch()
        download_dir = tmp_path / "dl"

        captured_env: dict[str, str] = {}

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"OK", b""))

        async def capture_exec(*_args: object, **kwargs: object) -> AsyncMock:
            download_dir.mkdir(parents=True, exist_ok=True)
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return mock_process

        with patch("galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec", side_effect=capture_exec):
            await download_collections(
                ["a.b"],
                download_dir,
                ansible_cfg_path=cfg_path,
            )

        assert captured_env.get("ANSIBLE_CONFIG") == str(cfg_path)

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_uses_servers_generates_cfg(self, tmp_path: Path) -> None:
        """Generates temp ansible.cfg when servers are provided.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        download_dir = tmp_path / "dl"
        servers = [GalaxyServerConfig(name="hub", url="https://hub.example.com", token="tok")]

        captured_env: dict[str, str] = {}

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"OK", b""))

        async def capture_exec(*_args: object, **kwargs: object) -> AsyncMock:
            download_dir.mkdir(parents=True, exist_ok=True)
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return mock_process

        with patch("galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec", side_effect=capture_exec):
            await download_collections(
                ["a.b"],
                download_dir,
                servers=servers,
            )

        assert "ANSIBLE_CONFIG" in captured_env

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_partial_failure(self, tmp_path: Path) -> None:
        """Non-zero rc with some tarballs returns partial results.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        download_dir = tmp_path / "dl"

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error for b.c"))

        async def fake_exec(*_args: object, **_kwargs: object) -> AsyncMock:
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / "a-b-1.0.0.tar.gz").touch()
            return mock_process

        with patch("galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await download_collections(
                ["a.b:1.0.0", "b.c:2.0.0"],
                download_dir,
            )

        assert len(result.tarball_paths) == 1
        assert "b.c:2.0.0" in result.failed_specs
        assert "a.b:1.0.0" not in result.failed_specs


class TestConvertTarballsInDir:
    """Tests for convert_tarballs_in_dir."""

    def test_converts_valid_tarball(self, tmp_path: Path) -> None:
        """Converts a tarball to a wheel and writes to cache_dir.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        cache_dir = tmp_path / "cache"

        fake_tarball = tarball_dir / "ansible-posix-1.5.4.tar.gz"
        fake_tarball.write_bytes(b"fake")

        mock_wheel_data = b"PK\x03\x04fake-wheel"
        with patch(
            "galaxy_proxy.converter.tarball_to_wheel",
            return_value=("ansible_collection_ansible_posix-1.5.4-py3-none-any.whl", mock_wheel_data),
        ):
            results = convert_tarballs_in_dir(tarball_dir, cache_dir)

        assert len(results) == 1
        whl_name, whl_path = results[0]
        assert whl_name == "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
        assert whl_path.read_bytes() == mock_wheel_data

    def test_skips_failed_conversion(self, tmp_path: Path) -> None:
        """Failed conversion is logged but does not crash.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        cache_dir = tmp_path / "cache"

        (tarball_dir / "bad-1.0.0.tar.gz").write_bytes(b"not a tarball")

        with patch(
            "galaxy_proxy.converter.tarball_to_wheel",
            side_effect=ValueError("bad tarball"),
        ):
            results = convert_tarballs_in_dir(tarball_dir, cache_dir)

        assert results == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Empty tarball directory returns empty results.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        tarball_dir = tmp_path / "tarballs"
        tarball_dir.mkdir()
        cache_dir = tmp_path / "cache"

        results = convert_tarballs_in_dir(tarball_dir, cache_dir)
        assert results == []
