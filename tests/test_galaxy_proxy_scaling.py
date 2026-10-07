"""Galaxy proxy scaling: lock timeout (N7) and off-loop I/O + hash cache (N8)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from galaxy_proxy.proxy import server as proxy_server
from galaxy_proxy.proxy.server import create_app


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _open_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt out of fail-closed admin auth for functional tests.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.delenv(proxy_server._ADMIN_TOKEN_ENV, raising=False)
    monkeypatch.setenv(proxy_server._ALLOW_UNAUTH_ADMIN_ENV, "1")


class _HangingLock:
    """Lock double whose acquire hangs past any test timeout."""

    async def acquire(self) -> bool:
        """Sleep longer than the lock timeout.

        Returns:
            Never returns normally.
        """
        await asyncio.sleep(10)
        return True

    def release(self) -> None:
        """Release the fake lock (no-op)."""


def test_lock_timeout_returns_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A contended per-collection lock fails fast with 503.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.setenv("APME_GALAXY_LOCK_TIMEOUT_S", "0.05")
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    lock_key = "ansible.posix:1.5.4"
    hanging = _HangingLock()
    application.state.download_locks[lock_key] = hanging
    with TestClient(application, raise_server_exceptions=False) as client:
        resp = client.get(f"/wheels/{whl_name}")
    assert resp.status_code == 503
    assert "lock timeout" in resp.json()["detail"]
    assert resp.headers.get("retry-after") is not None  # #68


def test_project_page_lock_timeout_returns_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The project-page on-demand path also fails fast with 503 on lock timeout.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.setenv("APME_GALAXY_LOCK_TIMEOUT_S", "0.05")
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    lock_key = "ansible.posix:latest"
    application.state.download_locks[lock_key] = _HangingLock()
    with (
        TestClient(application, raise_server_exceptions=False) as client,
        patch(
            "galaxy_proxy.proxy.server._fetch_galaxy_versions",
            AsyncMock(return_value=[]),
        ),
    ):
        resp = client.get("/simple/ansible-collection-ansible-posix/")
    assert resp.status_code == 503
    assert "lock timeout" in resp.json()["detail"]
    assert resp.headers.get("retry-after") is not None  # #68


def test_wheel_hash_cache_hit_avoids_rehash(tmp_path: Path) -> None:
    """The second project page serves hashes from memory without re-hashing.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cache_dir = tmp_path / "cache"
    wheels_dir = cache_dir / "wheels"
    wheels_dir.mkdir(parents=True)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    (wheels_dir / whl_name).write_bytes(b"fake-wheel-bytes")

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    calls: list[str] = []
    from galaxy_proxy.metadata import sha256_file_hex as real_hash

    def _counting(path: Path) -> str:
        calls.append(str(path))
        return real_hash(path)

    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", AsyncMock(return_value=[])),
        patch("galaxy_proxy.proxy.server.sha256_file_hex", side_effect=_counting),
    ):
        first = client.get("/simple/ansible-collection-ansible-posix/")
        second = client.get("/simple/ansible-collection-ansible-posix/")
    assert first.status_code == 200
    assert second.status_code == 200
    assert "#sha256=" in first.text
    # One unique wheel hashed once; the second page is a memory cache hit.
    assert len(calls) == 1
    expected = hashlib.sha256(b"fake-wheel-bytes").hexdigest()
    assert expected in second.text
    assert application.state.wheel_hashes[whl_name] == expected


def test_put_wheel_precomputes_hash(tmp_path: Path) -> None:
    """A download-and-convert miss caches the wheel hash from in-hand bytes.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    from galaxy_proxy.collection_downloader import DownloadResult

    cache_dir = tmp_path / "cache"
    application = create_app(cache_dir=cache_dir)
    fake_tarball = tmp_path / "ansible-posix-1.5.4.tar.gz"
    fake_tarball.touch()
    whl_data = b"PK\x03\x04converted-wheel"
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    mock_download = AsyncMock(return_value=DownloadResult(tarball_paths=[fake_tarball]))
    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server.download_collections", mock_download),
        patch(
            "galaxy_proxy.proxy.server.tarball_to_wheel",
            return_value=(whl_name, whl_data),
        ),
    ):
        resp = client.get(f"/wheels/{whl_name}")
    assert resp.status_code == 200
    expected = hashlib.sha256(whl_data).hexdigest()
    assert application.state.wheel_hashes[whl_name] == expected


def test_wheel_hash_revalidated_after_overwrite(tmp_path: Path) -> None:
    """Overwriting a wheel with different bytes serves the new SHA256.

    The mtime+size identity captured at hash time must not pin a stale
    digest: a replaced wheel is re-hashed on the next project page.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cache_dir = tmp_path / "cache"
    wheels_dir = cache_dir / "wheels"
    wheels_dir.mkdir(parents=True)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    v1 = b"fake-wheel-bytes-v1"
    v2 = b"completely-different-wheel-bytes-version-two"
    assert len(v1) != len(v2)  # Size change alone must flip the identity.
    (wheels_dir / whl_name).write_bytes(v1)

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", AsyncMock(return_value=[])),
    ):
        first = client.get("/simple/ansible-collection-ansible-posix/")
        assert first.status_code == 200
        hash_v1 = hashlib.sha256(v1).hexdigest()
        assert hash_v1 in first.text

        (wheels_dir / whl_name).write_bytes(v2)
        second = client.get("/simple/ansible-collection-ansible-posix/")

    assert second.status_code == 200
    hash_v2 = hashlib.sha256(v2).hexdigest()
    assert hash_v2 in second.text
    assert hash_v1 not in second.text
    assert application.state.wheel_hashes[whl_name] == hash_v2


def test_wheel_hash_missing_file_serves_empty_hash(tmp_path: Path) -> None:
    """A listed-but-missing wheel degrades to a plain link without raising.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", AsyncMock(return_value=[])),
        patch.object(proxy_server, "_list_cached_wheels", return_value=[whl_name]),
    ):
        resp = client.get("/simple/ansible-collection-ansible-posix/")
    assert resp.status_code == 200
    assert whl_name in resp.text
    assert "#sha256=" not in resp.text


def test_wheel_hash_unreadable_file_serves_empty_hash(tmp_path: Path) -> None:
    """A hash-read failure degrades to a plain link without raising.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cache_dir = tmp_path / "cache"
    wheels_dir = cache_dir / "wheels"
    wheels_dir.mkdir(parents=True)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    (wheels_dir / whl_name).write_bytes(b"fake-wheel-bytes")

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    application.state.wheel_hashes.clear()
    application.state.wheel_hash_stats.clear()
    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", AsyncMock(return_value=[])),
        patch("galaxy_proxy.proxy.server.sha256_file_hex", side_effect=OSError("unreadable")),
    ):
        resp = client.get("/simple/ansible-collection-ansible-posix/")
    assert resp.status_code == 200
    assert whl_name in resp.text
    assert "#sha256=" not in resp.text


def _make_tarball(files: dict[str, bytes]) -> bytes:
    """Build an in-memory .tar.gz with a galaxy.yml plus files.

    Args:
        files: Mapping of archive-relative path to bytes.

    Returns:
        Raw tarball bytes.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        galaxy_yml = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        info = tarfile.TarInfo("galaxy.yml")
        info.size = len(galaxy_yml)
        tf.addfile(info, io.BytesIO(galaxy_yml))
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_tarball_aggregate_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction beyond APME_GALAXY_TARBALL_MAX_BYTES raises ValueError.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_BYTES", "100")
    tarball = _make_tarball({"a.txt": b"x" * 60, "b.txt": b"y" * 60})
    with pytest.raises(ValueError, match="size limit exceeded"):
        _extract_tarball(tarball)


def test_tarball_file_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction beyond APME_GALAXY_TARBALL_MAX_FILES raises ValueError.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILES", "2")
    tarball = _make_tarball({f"{i}.txt": b"x" for i in range(5)})
    with pytest.raises(ValueError, match="file limit exceeded"):
        _extract_tarball(tarball)


def test_tarball_per_file_cap_skips_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """Members over APME_GALAXY_TARBALL_MAX_FILE_BYTES are skipped unread.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    # galaxy.yml itself is 40 bytes, so the cap must clear it while
    # catching the 100-byte member.
    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILE_BYTES", "50")
    tarball = _make_tarball({"big.txt": b"x" * 100, "small.txt": b"y"})
    _galaxy, contents = _extract_tarball(tarball)
    assert "small.txt" in contents
    assert "big.txt" not in contents


def test_tarball_files_json_counts_toward_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """FILES.json is excluded from wheel content but counts toward caps.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    import io as _io
    import tarfile as _tarfile

    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILES", "1")
    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in {
            "galaxy.yml": b"namespace: ns\nname: coll\nversion: 1.0.0\n",
            "FILES.json": b"{}",
        }.items():
            info = _tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, _io.BytesIO(data))
    with pytest.raises(ValueError, match="file limit exceeded"):
        _extract_tarball(buf.getvalue())


async def test_download_lock_eviction_skips_held_locks(tmp_path: Path) -> None:
    """Eviction drops the oldest idle download lock, never a held one.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    import asyncio as _asyncio

    from galaxy_proxy.proxy.server import CollectionResolutionError

    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    locks = application.state.download_locks
    held_key = "zz-held.a:1.0.0"
    held = _asyncio.Lock()
    await held.acquire()
    locks[held_key] = held
    second_key = "zz-idle.b:1.0.0"
    locks[second_key] = _asyncio.Lock()
    for i in range(proxy_server._MAX_DOWNLOAD_LOCKS - 2):
        locks[f"zz-fill.{i}:1.0.0"] = _asyncio.Lock()
    assert len(locks) == proxy_server._MAX_DOWNLOAD_LOCKS

    with (
        TestClient(application, raise_server_exceptions=False) as client,
        patch("galaxy_proxy.proxy.server._fetch_galaxy_versions", AsyncMock(return_value=[])),
        patch(
            "galaxy_proxy.proxy.server._download_and_convert",
            AsyncMock(side_effect=CollectionResolutionError("evict.probe")),
        ),
    ):
        resp = client.get("/simple/ansible-collection-evict-probe/")

    assert resp.status_code == 404
    try:
        # The held lock survives; the oldest idle lock was evicted instead.
        assert locks[held_key] is held
        assert second_key not in locks
        assert "evict.probe:latest" in locks
        assert len(locks) == proxy_server._MAX_DOWNLOAD_LOCKS
    finally:
        held.release()
        locks.clear()
