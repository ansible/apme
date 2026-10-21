"""Tests for Galaxy collection spec validation and option-injection guard (N2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from galaxy_proxy.collection_downloader import download_collections, validate_collection_spec


def test_validate_collection_spec_accepts_plain_and_versioned() -> None:
    """Plain and versioned specs pass validation."""
    assert validate_collection_spec("community.general") == "community.general"
    assert validate_collection_spec("ansible.posix:1.5.4") == "ansible.posix:1.5.4"
    assert validate_collection_spec("community.general:>=9.0") == "community.general:>=9.0"


def test_validate_collection_spec_rejects_empty() -> None:
    """Empty specs fail closed."""
    with pytest.raises(ValueError, match="must not be empty"):
        validate_collection_spec("")
    with pytest.raises(ValueError, match="must not be empty"):
        validate_collection_spec("   ")


def test_validate_collection_spec_rejects_leading_dash() -> None:
    """Leading dashes (option injection) are rejected."""
    with pytest.raises(ValueError, match="must not start with"):
        validate_collection_spec("--help")
    with pytest.raises(ValueError, match="must not start with"):
        validate_collection_spec("-v")


def test_validate_collection_spec_rejects_shell_metachars() -> None:
    """Shell metacharacters and missing dots fail the allowlist."""
    for bad in ["evil;rm -rf", "a|b", "a&b", "$(id)", "`id`", "nodot", "a.b;evil"]:
        with pytest.raises(ValueError, match="Invalid collection spec"):
            validate_collection_spec(bad)


def test_validate_collection_spec_rejects_embedded_star() -> None:
    """'*' is only valid as the entire version, never inside a range."""
    for bad in ["community.general:>=1.0.*", "ansible.posix:1.0.*", "a.b:**"]:
        with pytest.raises(ValueError, match="Invalid collection spec"):
            validate_collection_spec(bad)
    assert validate_collection_spec("community.general:*") == "community.general:*"


def test_validate_collection_spec_accepts_compatible_release() -> None:
    """PEP 440 '~=' constraints pass validation."""
    assert validate_collection_spec("community.general:~=9.0") == "community.general:~=9.0"


def test_validate_collection_spec_normalizes_spaces() -> None:
    """Spaces are stripped before validation, identically on every path."""
    assert validate_collection_spec("community.general : >= 9.0") == "community.general:>=9.0"
    assert validate_collection_spec("  ansible.posix  ") == "ansible.posix"


def test_spec_to_pip_validates_spec() -> None:
    """The venv pip path rejects option-injection specs before reaching pip."""
    from apme_engine.venv_manager.venv_collections import _spec_to_pip

    with pytest.raises(ValueError, match="must not start with|Invalid collection spec"):
        _spec_to_pip("--evil")
    with pytest.raises(ValueError, match="Invalid collection spec"):
        _spec_to_pip("evil;rm -rf")
    assert _spec_to_pip("community.general: >= 9.0") == "ansible-collection-community-general>=9.0"


async def test_download_collections_inserts_dash_dash(tmp_path: Path) -> None:
    """Positional specs are separated from flags by ``--``.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    download_dir = tmp_path / "dl"
    captured_cmd: list[str] = []
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"OK", b""))

    async def _capture(*args: object, **kwargs: object) -> AsyncMock:
        captured_cmd.extend([str(a) for a in args])
        download_dir.mkdir(parents=True, exist_ok=True)
        return mock_process

    with patch(
        "galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec",
        side_effect=_capture,
    ):
        await download_collections(["ansible.posix"], download_dir)

    assert "--" in captured_cmd
    dash_idx = captured_cmd.index("--")
    assert captured_cmd[dash_idx + 1] == "ansible.posix"


async def test_download_collections_rejects_option_injection(tmp_path: Path) -> None:
    """Option-like specs fail as failed_specs before any subprocess starts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    with patch(
        "galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec",
        side_effect=AssertionError("must not spawn subprocess"),
    ):
        result = await download_collections(["--no-deps"], tmp_path / "dl")
    assert result.tarball_paths == []
    assert result.failed_specs == ["--no-deps"]


async def test_download_collections_mixed_specs_continue_batch(tmp_path: Path) -> None:
    """One invalid spec does not abort valid companions (#N8 batch rule).

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    from galaxy_proxy.collection_downloader import DownloadResult

    async def _fake_subprocess(*args: object, **kwargs: object) -> object:
        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    with (
        patch(
            "galaxy_proxy.collection_downloader.asyncio.create_subprocess_exec",
            side_effect=_fake_subprocess,
        ),
        patch(
            "galaxy_proxy.collection_downloader._find_tarballs",
            return_value=[],
        ),
    ):
        result = await download_collections(["ansible.posix", "--no-deps"], tmp_path / "dl2")
    assert isinstance(result, DownloadResult)
    assert result.failed_specs == ["--no-deps"]
