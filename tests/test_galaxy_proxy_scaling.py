"""Galaxy proxy scaling: lock timeout (N7) and off-loop I/O + hash cache (N8)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import os
import tarfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
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


def _seed_cached_wheel(application: FastAPI, cache_dir: Path, filename: str, data: bytes) -> Path:
    """Seed a wheel after binding the test cache to the app's source identity.

    Args:
        application: Proxy app whose cache configuration should be mirrored.
        cache_dir: Cache root shared with the app.
        filename: Wheel filename to seed.
        data: Wheel bytes.

    Returns:
        Path to the seeded wheel.
    """
    from galaxy_proxy.proxy.cache import ProxyCache

    cache = ProxyCache(cache_dir)
    cache.bind_config(
        application.state.galaxy_source_fingerprint,
        managed=application.state.galaxy_config_managed,
    )
    return cache.put_wheel(filename, data)


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


def test_release_session_scope_removes_wheels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The authenticated cleanup route removes a completed install's scope.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest fixture for modifying the shared scope store.
    """
    scope_id = "R" * 32
    filename = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    monkeypatch.setitem(
        application.state.session_wheels,
        scope_id,
        {filename: (time.monotonic() + 60, b"wheel-data")},
    )
    with TestClient(application) as client:
        response = client.delete(f"/admin/session-scopes/{scope_id}")

    assert response.status_code == 200
    assert response.json() == {"released": True}
    assert scope_id not in application.state.session_wheels


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
    with TestClient(application, raise_server_exceptions=False) as client:
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
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    _seed_cached_wheel(application, cache_dir, whl_name, b"fake-wheel-bytes")
    calls: list[str] = []
    from galaxy_proxy.metadata import sha256_file_hex as real_hash

    def _counting(path: Path) -> str:
        calls.append(str(path))
        return real_hash(path)

    with (
        TestClient(application) as client,
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

    The file identity captured at hash time must not pin a stale digest:
    a replaced wheel is re-hashed on the next project page.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cache_dir = tmp_path / "cache"
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    v1 = b"fake-wheel-bytes-v1"
    v2 = b"completely-different-wheel-bytes-version-two"
    assert len(v1) != len(v2)  # Size change alone must flip the identity.

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    wheel_path = _seed_cached_wheel(application, cache_dir, whl_name, v1)
    with TestClient(application) as client:
        first = client.get("/simple/ansible-collection-ansible-posix/")
        assert first.status_code == 200
        hash_v1 = hashlib.sha256(v1).hexdigest()
        assert hash_v1 in first.text

        wheel_path.write_bytes(v2)
        second = client.get("/simple/ansible-collection-ansible-posix/")

    assert second.status_code == 200
    hash_v2 = hashlib.sha256(v2).hexdigest()
    assert hash_v2 in second.text
    assert hash_v1 not in second.text
    assert application.state.wheel_hashes[whl_name] == hash_v2


def test_wheel_hash_retries_when_file_is_replaced_during_hash(tmp_path: Path) -> None:
    """A hash is never cached with the identity of a replacement wheel.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cache_dir = tmp_path / "cache"
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    v1 = b"first-wheel-content"
    v2 = b"other-wheel-content"
    assert len(v1) == len(v2)

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    wheel_path = _seed_cached_wheel(application, cache_dir, whl_name, v1)
    from galaxy_proxy.metadata import sha256_file_hex as real_hash

    calls = 0

    def _replace_after_hash(path: Path) -> str:
        nonlocal calls
        calls += 1
        digest = real_hash(path)
        if calls == 1:
            before = path.stat()
            replacement = path.with_name("replacement.whl")
            replacement.write_bytes(v2)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            replacement.replace(path)
        return digest

    with (
        TestClient(application) as client,
        patch("galaxy_proxy.proxy.server.sha256_file_hex", side_effect=_replace_after_hash),
    ):
        response = client.get("/simple/ansible-collection-ansible-posix/")

    expected = hashlib.sha256(v2).hexdigest()
    assert response.status_code == 200
    assert f"#sha256={expected}" in response.text
    assert hashlib.sha256(v1).hexdigest() not in response.text
    assert calls == 2
    assert application.state.wheel_hashes[whl_name] == expected
    assert application.state.wheel_hash_stats[whl_name][1] == wheel_path.stat().st_ino


def test_wheel_hash_missing_file_serves_empty_hash(tmp_path: Path) -> None:
    """A listed-but-missing wheel degrades to a plain link without raising.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    application = create_app(cache_dir=tmp_path / "cache", enable_passthrough=False)
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"
    with (
        TestClient(application) as client,
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
    whl_name = "ansible_collection_ansible_posix-1.5.4-py3-none-any.whl"

    application = create_app(cache_dir=cache_dir, enable_passthrough=False)
    _seed_cached_wheel(application, cache_dir, whl_name, b"fake-wheel-bytes")
    application.state.wheel_hashes.clear()
    application.state.wheel_hash_stats.clear()
    with (
        TestClient(application) as client,
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


def test_tarball_prefix_detects_bare_top_level_directory() -> None:
    """A bare top-level directory member still marks a prefixed archive."""
    from galaxy_proxy.converter import _extract_tarball

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        directory = tarfile.TarInfo("ns-coll-1.0.0")
        directory.type = tarfile.DIRTYPE
        tf.addfile(directory)

        metadata = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        galaxy_yml = tarfile.TarInfo("ns-coll-1.0.0/galaxy.yml")
        galaxy_yml.size = len(metadata)
        tf.addfile(galaxy_yml, io.BytesIO(metadata))

        content = b"---\n- hosts: all\n"
        playbook = tarfile.TarInfo("ns-coll-1.0.0/playbooks/site.yml")
        playbook.size = len(content)
        tf.addfile(playbook, io.BytesIO(content))

    galaxy, files = _extract_tarball(buf.getvalue())
    assert galaxy["namespace"] == "ns"
    assert galaxy["name"] == "coll"
    assert "playbooks/site.yml" in files


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


def test_tarball_directory_headers_count_toward_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directory headers count toward the cap in layout and extraction passes.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    import io as _io
    import tarfile as _tarfile

    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILES", "2")

    # A prefixed archive requires a full layout scan, which must stop at
    # the cap even when all observed entries are directories.
    prefixed = _io.BytesIO()
    with _tarfile.open(fileobj=prefixed, mode="w:gz") as tf:
        for index in range(3):
            info = _tarfile.TarInfo(f"ns-coll-1.0.0/dir-{index}")
            info.type = _tarfile.DIRTYPE
            tf.addfile(info)
    with pytest.raises(ValueError, match="file limit exceeded"):
        _extract_tarball(prefixed.getvalue())

    # A flat archive exits layout detection at its first bare file, so the
    # extraction pass itself must count subsequent non-file headers.
    flat = _io.BytesIO()
    with _tarfile.open(fileobj=flat, mode="w:gz") as tf:
        metadata = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        info = _tarfile.TarInfo("galaxy.yml")
        info.size = len(metadata)
        tf.addfile(info, _io.BytesIO(metadata))
        for index in range(2):
            directory = _tarfile.TarInfo(f"dir-{index}")
            directory.type = _tarfile.DIRTYPE
            tf.addfile(directory)
    with pytest.raises(ValueError, match="file limit exceeded"):
        _extract_tarball(flat.getvalue())


def test_tarball_per_file_cap_rejects_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """Members over APME_GALAXY_TARBALL_MAX_FILE_BYTES fail the conversion.

    Skipping an installable member would publish a wheel whose RECORD omits
    it, so the install succeeds with a file silently missing and fails later
    with no trace of the cap. Only Galaxy metadata (MANIFEST.json,
    FILES.json, galaxy.yml) may be dropped; it is covered by
    ``test_tarball_oversized_galaxy_metadata_is_skipped``.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    # galaxy.yml itself is 40 bytes, so the cap must clear it while
    # catching the 100-byte member.
    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILE_BYTES", "50")
    tarball = _make_tarball({"big.txt": b"x" * 100, "small.txt": b"y"})
    with pytest.raises(ValueError, match="exceeds per-file cap: big.txt"):
        _extract_tarball(tarball)


def test_tarball_oversized_galaxy_metadata_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized Galaxy metadata is dropped instead of failing the conversion.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILE_BYTES", "50")
    tarball = _make_tarball({"FILES.json": b"{}" + b" " * 100, "small.txt": b"y"})
    _galaxy, contents = _extract_tarball(tarball)
    assert "small.txt" in contents
    assert "FILES.json" not in contents


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


def test_tarball_oversized_pax_header_rejected_before_parsing() -> None:
    """A PAX header declaring a huge payload is rejected before it is buffered.

    ``tarfile`` allocates the extension-header payload at its declared size
    while iterating, before any member-level cap runs, so the bound has to
    happen on the raw header blocks.
    """
    from galaxy_proxy.converter import _extract_tarball

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        data = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        info = tarfile.TarInfo("galaxy.yml")
        info.size = len(data)
        info.pax_headers = {"comment": "x" * (2 * 1024 * 1024)}
        tf.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="extension header too large"):
        _extract_tarball(buf.getvalue())


@pytest.mark.parametrize("fmt", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])  # type: ignore[untyped-decorator]
def test_tarball_long_names_still_convert(fmt: int) -> None:
    """Legitimate long-name extension headers stay below the bound.

    Args:
        fmt: Tar format that encodes the long path as an extension header.
    """
    from galaxy_proxy.converter import _extract_tarball

    long_dir = "/".join(["d" * 60] * 4)
    long_path = f"{long_dir}/notes.txt"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=fmt) as tf:
        galaxy_yml = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        info = tarfile.TarInfo("galaxy.yml")
        info.size = len(galaxy_yml)
        tf.addfile(info, io.BytesIO(galaxy_yml))

        payload = b"notes"
        info = tarfile.TarInfo(long_path)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    galaxy, files = _extract_tarball(buf.getvalue())

    assert galaxy["name"] == "coll"
    assert files[long_path] == b"notes"


def test_tarball_member_cap_trips_before_tarfile_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The member bound trips in the pre-parse walk, before any tarfile pass.

    A directory-only archive of hundreds of thousands of empty entries fits
    easily under the aggregate byte cap, so the header walk must charge its own
    work budget: it counts members against the same cap and stops there
    instead of letting ``tarfile`` parse (and cache) every header first.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    import io as _io
    import tarfile as _tarfile

    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILES", "10")

    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # All members share one prefix, so the layout pass never breaks out.
        for index in range(50):
            info = _tarfile.TarInfo(f"ns-coll-1.0.0/empty-{index}/")
            info.type = _tarfile.DIRTYPE
            tf.addfile(info)
    tarball = buf.getvalue()

    with (
        patch.object(_tarfile, "open", wraps=_tarfile.open) as counting,
        pytest.raises(ValueError, match="file limit exceeded"),
    ):
        _extract_tarball(tarball)
    # The header walk alone trips the cap: neither tarfile pass starts.
    assert counting.call_count == 0


def test_tarball_byte_cap_still_trips_in_the_first_tarfile_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aggregate byte cap no longer competes with header overhead.

    ``APME_GALAXY_TARBALL_MAX_BYTES`` bounds *extracted* content, while the raw
    walk has its own budget, so small members under a tight cap are rejected by
    the byte cap itself rather than by header blocks charged against it.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_BYTES", "100")
    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_FILES", "1000")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for index in range(10):
            data = b"x" * 40
            info = tarfile.TarInfo(f"ns-coll-1.0.0/file-{index}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="size limit exceeded"):
        _extract_tarball(buf.getvalue())


def test_small_collection_fits_a_tight_extracted_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Header blocks and padding are not charged against the extracted cap.

    ``APME_GALAXY_TARBALL_MAX_BYTES`` documents the extracted-content limit; a
    collection whose files fit it must convert even though its raw stream needs
    a few header blocks more (a 1000-byte cap with two members needs 2048 raw
    bytes).

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    from galaxy_proxy.converter import _extract_tarball

    # ``_make_tarball`` writes galaxy.yml (40 bytes); the only other member is
    # notes.txt, so the cap covers exactly the extracted content.
    monkeypatch.setenv("APME_GALAXY_TARBALL_MAX_BYTES", "80")

    galaxy, files = _extract_tarball(_make_tarball({"notes.txt": b"n" * 40}))

    assert galaxy["name"] == "coll"
    assert files["notes.txt"] == b"n" * 40


def test_base256_size_field_keeps_low_order_zero_bytes() -> None:
    r"""A base-256 size is decoded from all 12 bytes, not a stripped field.

    ``b"\\x80" + b"\\0" * 7 + b"\\x40\\0\\0\\0"`` declares 1 GiB. Stripping the
    trailing NUL bytes before testing the GNU marker truncates that to 64, so
    the walk would skip a single block and let ``tarfile`` buffer the whole
    payload.
    """
    from galaxy_proxy.converter import _header_declared_size

    header = bytearray(512)
    header[124:136] = b"\x80" + b"\0" * 7 + b"\x40\0\0\0"

    assert _header_declared_size(bytes(header)) == 1024**3


def test_base256_extension_size_is_bounded_before_parsing() -> None:
    """A GNU long-name header using a large base-256 size stays bounded.

    The raw walk must see the full declared size even when the low-order size
    bytes are zero, otherwise the following block (a valid header, so the walk
    keeps going) hides the oversized payload from the bound.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
        info = tarfile.TarInfo("/".join(["n" * 90] * 8))
        info.size = 0
        tf.addfile(info, io.BytesIO(b""))
    raw = bytearray(buf.getvalue())

    # Rewrite the long-name header's size field as base-256 1 GiB.
    assert raw[156:157] == b"L"
    raw[124:136] = b"\x80" + b"\0" * 7 + b"\x40\0\0\0"
    _recompute_checksum(raw, 0)

    with pytest.raises(ValueError, match="extension header too large"):
        _walk(bytes(raw))


def _recompute_checksum(block: bytearray, offset: int) -> None:
    """Write a valid tar header checksum into one 512-byte block.

    Args:
        block: Raw archive bytes.
        offset: Byte offset of the header block inside ``block``.
    """
    header = block[offset : offset + 512]
    header[148:156] = b" " * 8
    block[offset : offset + 512] = header
    checksum = f"{sum(header):06o}\0 "
    block[offset + 148 : offset + 156] = checksum.encode()


def test_octal_fields_stop_at_first_nul_like_tarfile() -> None:
    r"""Numeric fields truncate at the first NUL, matching ``tarfile.nti``.

    ``tarfile`` decodes a field with ``nts`` (bytes up to the first NUL) and only
    then strips padding, so ``0000100\\0x`` is a valid size of 64. Stripping
    trailing padding instead returned ``None``, which stopped the raw walk
    before the bytes ``tarfile`` still parses — the extension-header bound and
    the aggregate byte cap were both bypassed that way.
    """
    from galaxy_proxy.converter import _header_checksum_ok, _header_declared_size

    header = bytearray(512)
    header[124:136] = b"0000100\0x\0\0\0"
    assert _header_declared_size(bytes(header)) == 64

    # A checksum field with a NUL followed by a non-padding byte is still read
    # by tarfile: it takes the digits before the NUL.
    header = bytearray(512)
    header[0:10] = b"galaxy.yml"
    header[100:108] = b"0000644\0"
    header[108:116] = b"0000000\0"
    header[116:124] = b"0000000\0"
    header[124:136] = b"00000000004\0"
    header[136:148] = b"00000000000\0"
    header[148:156] = b" " * 8
    header[156:157] = b"0"
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}".encode() + b"\0x"

    assert _header_checksum_ok(bytes(header)) is True


def _walk(archive: bytes) -> None:
    """Run the pre-parse header walk over a raw (uncompressed) archive.

    Args:
        archive: Raw tar bytes to walk.
    """
    from galaxy_proxy.converter import _assert_extension_headers_bounded

    _assert_extension_headers_bounded(
        gzip.compress(archive),
        max_bytes=64 * 1024 * 1024,
        max_members=10000,
        max_extension_headers=20000,
    )


def _pax_record(keyword: bytes, value: bytes) -> bytes:
    r"""Frame one PAX record the way ``tarfile`` parses it.

    Args:
        keyword: Record keyword bytes.
        value: Record value bytes.

    Returns:
        A ``"<length> <keyword>=<value>\\n"`` record.
    """
    body = keyword + b"=" + value + b"\n"
    length = len(body) + 2
    while len(str(length).encode()) + 1 + len(body) != length:
        length = len(str(length).encode()) + 1 + len(body)
    return str(length).encode() + b" " + body


def _raw_tar_header(name: bytes, typeflag: bytes, size: int) -> bytearray:
    """Build one valid 512-byte tar header block.

    Args:
        name: Member name bytes (at most 100 bytes).
        typeflag: One-byte member type.
        size: Declared payload size.

    Returns:
        A header block with a valid checksum.
    """
    header = bytearray(512)
    header[0 : len(name)] = name
    header[100:108] = b"0000644\0"
    header[108:116] = b"0000000\0"
    header[116:124] = b"0000000\0"
    header[124:136] = f"{size:011o}\0".encode()
    header[136:148] = b"00000000000\0"
    header[156:157] = typeflag
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    _recompute_checksum(header, 0)
    return header


def test_pax_size_override_cannot_hide_an_oversized_header() -> None:
    """The walk advances by the size ``tarfile`` uses after a PAX ``size`` record.

    A crafted ``x`` header can declare ``size=0`` for the next member, so
    ``tarfile`` reads the next header immediately after that member's header
    block. Advancing by the member's raw declared size instead steps over a GNU
    ``L`` header that ``tarfile`` does read, and that header can declare a
    1 GiB payload which bypasses both the extension-header bound and the
    aggregate byte cap.
    """
    pax_record = b"10 size=0\n"
    pax_header = _raw_tar_header(b"./PaxHeaders/payload.bin", b"x", len(pax_record))
    member_header = _raw_tar_header(b"payload.bin", b"0", 512)
    # ``tarfile`` lands here after applying size=0 to the member above.
    hidden_header = _raw_tar_header(b"././@LongLink", b"L", 1024**3)
    archive = (
        bytes(pax_header) + pax_record.ljust(512, b"\0") + bytes(member_header) + bytes(hidden_header) + b"\0" * 1024
    )

    with pytest.raises(ValueError, match="extension header too large"):
        _walk(archive)


def test_tarball_pax_size_record_still_converts() -> None:
    """A legitimate PAX ``size`` record that matches the member still converts."""
    from galaxy_proxy.converter import _extract_tarball

    payload = b"note"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        galaxy_yml = b"namespace: ns\nname: coll\nversion: 1.0.0\n"
        info = tarfile.TarInfo("galaxy.yml")
        info.size = len(galaxy_yml)
        tf.addfile(info, io.BytesIO(galaxy_yml))

        info = tarfile.TarInfo("notes.txt")
        info.size = len(payload)
        info.pax_headers = {"size": str(len(payload))}
        tf.addfile(info, io.BytesIO(payload))

    galaxy, files = _extract_tarball(buf.getvalue())

    assert galaxy["name"] == "coll"
    assert files["notes.txt"] == payload


def test_tarball_gnu_sparse_member_is_rejected_before_parsing() -> None:
    """A GNU sparse member is rejected instead of walked with guessed offsets.

    ``tarfile`` reads sparse members through their own block layout, so a flat
    header walk cannot mirror the byte stream it protects.
    """
    archive = bytes(_raw_tar_header(b"sparse.bin", b"S", 0)) + b"\0" * 1024

    with pytest.raises(ValueError, match="GNU sparse member"):
        _walk(archive)


def test_tarball_gnu_sparse_pax_metadata_is_rejected_before_parsing() -> None:
    """PAX GNU-sparse metadata is rejected before ``tarfile`` reads the map.

    Decoding GNU sparse 1.0 makes ``tarfile`` read ``fields * 2`` numbers from
    the stream, where ``fields`` comes from the archive; those reads are outside
    both the member's declared size and this walk's byte budget. The keys can
    arrive in a per-file (``x``) or a global (``g``) PAX header.
    """
    for typeflag in (b"x", b"g"):
        record = _pax_record(b"GNU.sparse.major", b"1") + _pax_record(b"GNU.sparse.minor", b"0")
        header = _raw_tar_header(b"./PaxHeaders/sparse.bin", typeflag, len(record))
        archive = (
            bytes(header) + record.ljust(512, b"\0") + bytes(_raw_tar_header(b"sparse.bin", b"0", 0)) + b"\0" * 1024
        )

        with pytest.raises(ValueError, match="GNU sparse metadata"):
            _walk(archive)


def test_tarball_negative_pax_size_is_rejected() -> None:
    """A negative PAX ``size`` cannot shrink the walk's byte budget.

    ``total`` would decrease on the next member while ``_skip`` returns without
    reading, so a repeating ``size=-512`` record plus an empty member keeps the
    accounting flat and the scan reads the rest of the stream.
    """
    record = _pax_record(b"size", b"-512")
    header = _raw_tar_header(b"./PaxHeaders/payload.bin", b"x", len(record))
    archive = bytes(header) + record.ljust(512, b"\0") + bytes(_raw_tar_header(b"payload.bin", b"0", 0)) + b"\0" * 1024

    with pytest.raises(ValueError, match="negative size record"):
        _walk(archive)


def test_tarball_long_extension_header_run_is_rejected() -> None:
    """A run of extension headers cannot nest ``tarfile``'s recursion unboundedly.

    ``tarfile`` resolves a long name by parsing the following header, so a chain
    of ``L`` headers nests one call per header and retains each payload. The
    walk rejects the chain before ``tarfile`` recurses.
    """
    from galaxy_proxy.converter import _MAX_CONSECUTIVE_EXTENSION_HEADERS

    chain = b"".join(
        bytes(_raw_tar_header(b"././@LongLink", b"L", 4)) + b"name" + b"\0" * 508
        for _ in range(_MAX_CONSECUTIVE_EXTENSION_HEADERS + 1)
    )
    archive = chain + bytes(_raw_tar_header(b"payload.bin", b"0", 0)) + b"\0" * 1024

    with pytest.raises(ValueError, match="consecutive extension headers"):
        _walk(archive)


def test_tarball_oversized_global_pax_header_is_rejected() -> None:
    """A global PAX header is copied onto every member, so its size is capped.

    ``TarInfo.pax_headers`` holds a copy per parsed member, so the raw walk
    charges a global header once while ``tarfile`` retains it
    ``members`` times.
    """
    from galaxy_proxy.converter import _MAX_GLOBAL_PAX_BYTES

    record = _pax_record(b"comment", b"c" * (_MAX_GLOBAL_PAX_BYTES + 1))
    header = _raw_tar_header(b"./PaxHeaders/global", b"g", len(record))
    archive = bytes(header) + record + b"\0" * (-len(record) % 512) + b"\0" * 1024

    with pytest.raises(ValueError, match="global PAX header too large"):
        _walk(archive)


def test_tarball_global_pax_attribute_count_is_bounded() -> None:
    """Global PAX attributes also multiply by the member count."""
    from galaxy_proxy.converter import _MAX_GLOBAL_PAX_ATTRS

    record = b"".join(_pax_record(f"key{index}".encode(), b"v") for index in range(_MAX_GLOBAL_PAX_ATTRS + 1))
    header = _raw_tar_header(b"./PaxHeaders/global", b"g", len(record))
    archive = bytes(header) + record + b"\0" * (-len(record) % 512) + b"\0" * 1024

    with pytest.raises(ValueError, match="too many attributes"):
        _walk(archive)
