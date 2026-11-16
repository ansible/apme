"""Real kernel quota tests; require permission to mount a private tmpfs."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from apme_gateway.scm.safe_fetch import _run, _storage_root


@pytest.mark.parametrize("compressible", [False, True])  # type: ignore[untyped-decorator]
async def test_git_fetch_and_checkout_cannot_exceed_shared_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compressible: bool,
) -> None:
    """Packed transfer and expanded checkout are bounded across concurrent Git writers.

    Args:
        tmp_path: Sources and private mountpoint.
        monkeypatch: Fetch root environment.
        compressible: Whether checkout rather than pack transfer exceeds the quota.
    """
    mount = tmp_path / "quota"
    mount.mkdir()
    result = subprocess.run(
        ["mount", "-t", "tmpfs", "-o", "size=4m,nr_inodes=8192", "tmpfs", str(mount)], capture_output=True
    )
    if result.returncode:
        pytest.skip("Private tmpfs mount requires CAP_SYS_ADMIN")
    try:
        monkeypatch.setenv("APME_GIT_FETCH_ROOT", str(mount))
        assert _storage_root() == mount
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        (source / "large").write_bytes(b"x" * (6 * 1024 * 1024) if compressible else os.urandom(6 * 1024 * 1024))
        subprocess.run(["git", "-C", str(source), "add", "large"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "large",
            ],
            check=True,
            capture_output=True,
        )
        env = dict(os.environ, TMPDIR=str(mount))
        results = await asyncio.gather(
            *(_run(["git", "clone", "--no-local", str(source), str(mount / str(n))], env) for n in range(2)),
            return_exceptions=True,
        )
        assert all(isinstance(value, RuntimeError) for value in results)
        stats = os.statvfs(mount)
        assert stats.f_blocks * stats.f_frsize <= 4 * 1024 * 1024
        for child in mount.iterdir():
            await asyncio.to_thread(shutil.rmtree, child)
        assert not list(mount.iterdir())
    finally:
        subprocess.run(["umount", str(mount)], check=True, capture_output=True)
