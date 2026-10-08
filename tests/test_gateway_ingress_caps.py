"""Gateway ingress caps: upload timeout/limits (N5) and scan caps (N6)."""

from __future__ import annotations

import asyncio
import base64
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import grpc.aio
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from apme.v1 import engine_pb2, engine_pb2_grpc
from apme.v1.common_pb2 import File as ProtoFile
from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import Project, Scan, Session, Violation


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _FakeUploadWS:
    """Minimal WebSocket double for _collect_uploads tests."""

    def __init__(self, messages: list[dict[str, object]] | None = None) -> None:
        """Store queued messages and sent frames.

        Args:
            messages: Messages to serve from ``receive_json``.
        """
        self._incoming: list[dict[str, object]] = list(messages or [])
        self.sent: list[dict[str, object]] = []

    async def receive_json(self) -> dict[str, object]:
        """Pop the next queued message.

        Returns:
            Next queued message dict.
        """
        assert self._incoming, "No more messages"
        return self._incoming.pop(0)

    async def send_json(self, data: dict[str, object]) -> None:
        """Record a message sent to the client.

        Args:
            data: JSON-serializable payload.
        """
        self.sent.append(data)


class _HangingWS(_FakeUploadWS):
    """WebSocket double whose receive hangs to trigger idle timeout."""

    async def receive_json(self) -> dict[str, object]:
        """Sleep longer than any test timeout.

        Returns:
            Never returns normally.

        Raises:
            AssertionError: Unreachable fallback if sleep returns.
        """
        await asyncio.sleep(10)
        raise AssertionError("unreachable")


def _b64(data: bytes) -> str:
    """Encode bytes as base64 text.

    Args:
        data: Raw bytes.

    Returns:
        Base64-encoded string.
    """
    return base64.b64encode(data).decode()


def _ws_msg(msg_type: str, **fields: object) -> dict[str, object]:
    """Build one fake upload WebSocket message with object-typed values.

    Args:
        msg_type: Value for the ``type`` frame field.
        **fields: Additional frame fields.

    Returns:
        Message dict directly passable to ``_FakeUploadWS``.
    """
    return {"type": msg_type, **fields}


async def test_collect_uploads_happy_path(tmp_path: Path) -> None:
    """A small upload within caps is collected and written to disk.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_gateway.session_client import _collect_uploads

    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {"enable_ai": False}},
            {"type": "file", "path": "a.yml", "content": _b64(b"---\n")},
            {"type": "files_done"},
        ]
    )
    options = await _collect_uploads(ws, tmp_path)
    assert options == {"enable_ai": False}
    assert (tmp_path / "a.yml").read_bytes() == b"---\n"


async def test_collect_uploads_per_file_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files larger than APME_UPLOAD_MAX_FILE_BYTES fail with error + ValueError.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILE_BYTES", "5")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "big.yml", "content": _b64(b"x" * 16)},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="per-file limit"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_total_bytes_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aggregate bytes beyond APME_UPLOAD_MAX_TOTAL_BYTES fail fast.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_TOTAL_BYTES", "10")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"x" * 6)},
            {"type": "file", "path": "b.yml", "content": _b64(b"y" * 6)},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="size limit exceeded"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_max_files_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """More files than APME_UPLOAD_MAX_FILES fail fast.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILES", "1")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"x")},
            {"type": "file", "path": "b.yml", "content": _b64(b"y")},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="file limit exceeded"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_idle_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung client hits APME_UPLOAD_IDLE_TIMEOUT_S with error + ValueError.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_IDLE_TIMEOUT_S", "0.05")
    ws = _HangingWS()
    with pytest.raises(ValueError, match="idle timeout"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_exact_max_files_reachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly max_files files succeed: start/files_done are outside the message cap.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILES", "3")
    monkeypatch.setenv("APME_UPLOAD_MAX_MESSAGES", "3")
    messages = (
        [_ws_msg("start", options={})]
        + [_ws_msg("file", path=f"f{i}.yml", content=_b64(b"x")) for i in range(3)]
        + [_ws_msg("files_done")]
    )
    ws = _FakeUploadWS(messages)
    await _collect_uploads(ws, tmp_path)
    for i in range(3):
        assert (tmp_path / f"f{i}.yml").read_bytes() == b"x"


async def test_collect_uploads_message_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown-type frames count toward APME_UPLOAD_MAX_MESSAGES (start/files_done excluded).

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_MESSAGES", "3")
    messages = [_ws_msg("start", options={})] + [_ws_msg("bogus", n=i) for i in range(4)] + [_ws_msg("files_done")]
    ws = _FakeUploadWS(messages)
    with pytest.raises(ValueError, match="message limit"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_duration_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chatty sender exceeding APME_UPLOAD_MAX_DURATION_S fails with error + ValueError.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import time as time_mod

    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_DURATION_S", "10")
    ticks = iter([1000.0, 1000.0, 1011.0])
    monkeypatch.setattr(time_mod, "monotonic", lambda: next(ticks, 1011.0))
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"x")},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="time limit"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_collect_uploads_invalid_base64_surfaced_without_write(tmp_path: Path) -> None:
    """Invalid base64 surfaces an error frame and is skipped; valid files still land.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_gateway.session_client import _collect_uploads

    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "bad.yml", "content": "!!!not-base64!!!"},
            {"type": "file", "path": "good.yml", "content": _b64(b"ok")},
            {"type": "files_done"},
        ]
    )
    await _collect_uploads(ws, tmp_path)
    assert (tmp_path / "good.yml").read_bytes() == b"ok"
    assert not (tmp_path / "bad.yml").exists()
    assert any(s.get("type") == "error" and "Invalid base64" in str(s.get("message", "")) for s in ws.sent)


async def test_collect_uploads_invalid_base64_counts_toward_message_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid-base64 file frames count toward the message cap.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_MESSAGES", "1")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "bad.yml", "content": "!!!not-base64!!!"},
            {"type": "file", "path": "good.yml", "content": _b64(b"ok")},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="message limit"):
        await _collect_uploads(ws, tmp_path)


async def test_collect_uploads_encoded_size_fast_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An oversized base64 frame trips the pre-decode gate without decoding.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILE_BYTES", "10")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "big.yml", "content": "A" * 2000},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="per-file limit"):
        await _collect_uploads(ws, tmp_path)
    assert ws.sent and ws.sent[-1]["type"] == "error"
    assert "encoded" in str(ws.sent[-1].get("message", ""))


async def test_collect_uploads_offloads_decode_and_write(tmp_path: Path) -> None:
    """Decode and file writes run via asyncio.to_thread (event loop never blocks).

    Args:
        tmp_path: Pytest temporary directory.
    """
    import asyncio as asyncio_mod

    from apme_gateway.session_client import _collect_uploads

    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"---\n")},
            {"type": "files_done"},
        ]
    )
    original = asyncio_mod.to_thread
    seen: list[str] = []

    async def _spy(func: object, *args: object, **kwargs: object) -> object:
        """Record offloaded helpers then delegate to the real to_thread.

        Args:
            func: Blocking callable under test.
            *args: Positional arguments for *func*.
            **kwargs: Keyword arguments for *func*.

        Returns:
            Result of *func* executed in a worker thread.
        """
        name = getattr(func, "__name__", str(func))
        seen.append(str(name))
        return await original(func, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(asyncio_mod, "to_thread", new=_spy):
        await _collect_uploads(ws, tmp_path)
    assert "_decode_upload_b64" in seen
    assert "_write_upload_file" in seen
    assert (tmp_path / "a.yml").read_bytes() == b"---\n"


async def test_collect_uploads_missing_path_surfaced(tmp_path: Path) -> None:
    """A file frame without a path surfaces an error frame instead of raising KeyError.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_gateway.session_client import _collect_uploads

    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "content": _b64(b"x")},
            {"type": "file", "path": "good.yml", "content": _b64(b"ok")},
            {"type": "files_done"},
        ]
    )
    await _collect_uploads(ws, tmp_path)
    assert (tmp_path / "good.yml").read_bytes() == b"ok"
    assert any(s.get("type") == "error" and "Invalid file path" in str(s.get("message", "")) for s in ws.sent)


async def test_collect_uploads_traversal_path_surfaced(tmp_path: Path) -> None:
    """A traversal path surfaces an error frame instead of escaping the temp dir.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_gateway.session_client import _collect_uploads

    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "../evil.yml", "content": _b64(b"x")},
            {"type": "file", "path": "good.yml", "content": _b64(b"ok")},
            {"type": "files_done"},
        ]
    )
    await _collect_uploads(ws, tmp_path)
    assert (tmp_path / "good.yml").read_bytes() == b"ok"
    assert not (tmp_path.parent / "evil.yml").exists()
    assert any(s.get("type") == "error" for s in ws.sent)


async def test_collect_uploads_duplicate_canonical_path_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeat canonical paths overwrite without consuming another file slot.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILES", "1")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"one")},
            {"type": "file", "path": "./a.yml", "content": _b64(b"two")},
            {"type": "files_done"},
        ]
    )
    await _collect_uploads(ws, tmp_path)
    assert (tmp_path / "a.yml").read_bytes() == b"two"


async def test_collect_uploads_backslash_collision_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backslash-united distinct raws error instead of dropping a file (#17).

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_FILES", "10")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a\\b.yml", "content": _b64(b"one")},
            {"type": "file", "path": "a/b.yml", "content": _b64(b"two")},
            {"type": "files_done"},
        ]
    )
    with pytest.raises(ValueError, match="collide after canonicalization"):
        await _collect_uploads(ws, tmp_path)
    assert any(s.get("type") == "error" for s in ws.sent)


async def test_collect_uploads_duplicate_bytes_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Overwrites replace prior byte accounting instead of double-counting.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_TOTAL_BYTES", "10")
    ws = _FakeUploadWS(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "a.yml", "content": _b64(b"x" * 8)},
            {"type": "file", "path": "./a.yml", "content": _b64(b"y" * 8)},
            {"type": "files_done"},
        ]
    )
    await _collect_uploads(ws, tmp_path)
    assert (tmp_path / "a.yml").read_bytes() == b"y" * 8


async def test_collect_uploads_duplicates_count_toward_message_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate file frames still count per-frame toward the message cap.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.session_client import _collect_uploads

    monkeypatch.setenv("APME_UPLOAD_MAX_MESSAGES", "2")
    monkeypatch.setenv("APME_UPLOAD_MAX_FILES", "10")
    dup_messages = (
        [_ws_msg("start", options={})]
        + [_ws_msg("file", path="a.yml", content=_b64(b"x")) for _ in range(3)]
        + [_ws_msg("files_done")]
    )
    ws = _FakeUploadWS(dup_messages)
    with pytest.raises(ValueError, match="message limit"):
        await _collect_uploads(ws, tmp_path)


def _scan_chunk_with_files(*names_and_sizes: tuple[str, int]) -> engine_pb2.ScanChunk:
    """Build a ScanChunk with synthetic files.

    Args:
        *names_and_sizes: (path, size) pairs for file contents.

    Returns:
        ScanChunk carrying the synthetic files.
    """
    files = [ProtoFile(path=name, content=b"x" * size) for name, size in names_and_sizes]
    return engine_pb2.ScanChunk(scan_id="s1", project_root="project", files=files, last=True)


async def _run_operation_with_chunks(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[engine_pb2.ScanChunk],
) -> None:
    """Drive run_project_operation with stubbed clone/chunk/gRPC layers.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        chunks: Chunks returned by the stubbed ``yield_scan_chunks``.
    """
    from apme_gateway.scan import driver as driver_mod

    async def _no_clone(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(driver_mod, "clone_repo", _no_clone)
    monkeypatch.setattr(driver_mod, "get_clone_head", lambda _d: "abc")
    monkeypatch.setattr(driver_mod, "yield_scan_chunks", lambda *a, **k: iter(chunks))

    mock_channel = MagicMock()
    mock_channel.close = AsyncMock()
    monkeypatch.setattr(
        grpc.aio,
        "insecure_channel",
        MagicMock(return_value=mock_channel),
    )
    mock_stub = MagicMock()
    monkeypatch.setattr(
        engine_pb2_grpc,
        "EngineStub",
        MagicMock(return_value=mock_stub),
    )
    await driver_mod.run_project_operation(
        project_id="proj-1",
        repo_url="https://example.com/r.git",
        branch="main",
        engine_address="127.0.0.1:50051",
    )


async def test_scan_max_files_cap_many_small_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many small files beyond APME_SCAN_MAX_FILES fail fast.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SCAN_MAX_FILES", "5")
    chunks = [
        _scan_chunk_with_files(*[(f"f{i}.yml", 10) for i in range(3)]),
        _scan_chunk_with_files(*[(f"g{i}.yml", 10) for i in range(3)]),
    ]
    with pytest.raises(ValueError, match="file limit exceeded"):
        await _run_operation_with_chunks(monkeypatch, chunks)


async def test_scan_max_bytes_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aggregate bytes beyond APME_SCAN_MAX_BYTES fail fast.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_SCAN_MAX_BYTES", "100")
    chunks = [_scan_chunk_with_files(("a.yml", 60), ("b.yml", 60))]
    with pytest.raises(ValueError, match="size limit exceeded"):
        await _run_operation_with_chunks(monkeypatch, chunks)


async def test_scan_caps_allow_small_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bundle within caps proceeds to the gRPC stream.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.scan import driver as driver_mod

    async def _no_clone(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(driver_mod, "clone_repo", _no_clone)
    monkeypatch.setattr(driver_mod, "get_clone_head", lambda _d: "abc")
    chunk = _scan_chunk_with_files(("a.yml", 10))
    monkeypatch.setattr(driver_mod, "yield_scan_chunks", lambda *a, **k: iter([chunk]))

    async def _fake_events() -> AsyncIterator[object]:
        mock_event = MagicMock()
        mock_event.WhichOneof.return_value = "result"
        mock_event.result = MagicMock()
        yield mock_event

    mock_stub = MagicMock()
    mock_stub.FixSession.return_value = _fake_events()
    mock_channel = MagicMock()
    mock_channel.close = AsyncMock()
    with (
        patch.object(grpc.aio, "insecure_channel", return_value=mock_channel),
        patch.object(engine_pb2_grpc, "EngineStub", return_value=mock_stub),
    ):
        scan_id, _result, sha = await driver_mod.run_project_operation(
            project_id="proj-1",
            repo_url="https://example.com/r.git",
            branch="main",
            engine_address="127.0.0.1:50051",
        )
    assert scan_id
    assert sha == "abc"


async def test_scan_disk_size_fails_fast_before_chunking(monkeypatch: pytest.MonkeyPatch) -> None:
    """An oversized clone fails fast on disk size before chunk streaming.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_gateway.scan import driver as driver_mod

    monkeypatch.setenv("APME_SCAN_MAX_BYTES", "100")
    monkeypatch.setattr(driver_mod, "_temp_dir_disk_bytes", lambda _d: 101)
    chunks = [_scan_chunk_with_files(("a.yml", 1))]
    with pytest.raises(ValueError, match="size limit exceeded"):
        await _run_operation_with_chunks(monkeypatch, chunks)


def test_temp_dir_disk_bytes_sums_files(tmp_path: Path) -> None:
    """_temp_dir_disk_bytes sums regular file sizes under the directory.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    from apme_gateway.scan import driver as driver_mod

    (tmp_path / "a.txt").write_bytes(b"x" * 10)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_bytes(b"y" * 5)
    assert driver_mod._temp_dir_disk_bytes(str(tmp_path)) == 15


class TestImportThrottle:
    """Shared-bucket throttle for unlinked imports (#18)."""

    def test_unlinked_imports_share_external_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rotating project_path without project_id shares one quota.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        from apme_gateway.api import router as router_mod

        monkeypatch.setattr(router_mod, "_IMPORT_RATE_STATE", {})
        monkeypatch.setattr(router_mod, "_IMPORT_RATE_LIMIT_PER_MIN", 2)
        for _ in range(2):
            router_mod._check_import_rate_limit("external", unlinked=True)
        with pytest.raises(HTTPException) as exc_info:
            router_mod._check_import_rate_limit("external", unlinked=True)
        assert exc_info.value.status_code == 429

    def test_linked_imports_keep_per_project_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Distinct project_ids keep independent quotas.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        from apme_gateway.api import router as router_mod

        monkeypatch.setattr(router_mod, "_IMPORT_RATE_STATE", {})
        monkeypatch.setattr(router_mod, "_IMPORT_RATE_LIMIT_PER_MIN", 1)
        monkeypatch.setattr(router_mod, "_IMPORT_RATE_GLOBAL_PER_MIN", 100)
        router_mod._check_import_rate_limit("proj-a")
        router_mod._check_import_rate_limit("proj-b")


class TestImportScanCaps:
    """Caps, redaction, and naming for POST /api/v1/scans/import."""

    def test_violations_array_capped(self) -> None:
        """More than 5000 violations are rejected at the schema boundary."""
        from pydantic import ValidationError

        from apme_gateway.api.schemas import ImportScanRequest

        with pytest.raises(ValidationError):
            ImportScanRequest(violations=[{"rule_id": "L001"}] * 5001)
        ok = ImportScanRequest(violations=[{"rule_id": "L001"}] * 5000)
        assert len(ok.violations) == 5000

    def test_message_and_file_length_caps(self) -> None:
        """Per-field lengths are enforced (message 4k, file 1k)."""
        from pydantic import ValidationError

        from apme_gateway.api.schemas import ImportScanRequest

        with pytest.raises(ValidationError):
            ImportScanRequest(violations=[{"message": "x" * 4001}])
        with pytest.raises(ValidationError):
            ImportScanRequest(violations=[{"file": "x" * 1001}])
        ok = ImportScanRequest(violations=[{"message": "x" * 4000, "file": "y" * 1000}])
        assert len(ok.violations) == 1

    def test_redact_import_secrets(self) -> None:
        """Obvious api_key/token values are replaced with [REDACTED]."""
        from apme_gateway.api.router import _redact_import_secrets

        redacted = _redact_import_secrets("found api_key=supersecret123 in task")
        assert "supersecret123" not in redacted
        assert "[REDACTED]" in redacted
        assert _redact_import_secrets("nothing sensitive here") == "nothing sensitive here"

    def test_redact_import_secrets_json_and_extra_keys(self) -> None:
        """JSON-quoted secrets and wider key names are also redacted."""
        from apme_gateway.api.router import _redact_import_secrets

        redacted = _redact_import_secrets('config {"password": "hunter2"} leaked')
        assert "hunter2" not in redacted
        assert "[REDACTED]" in redacted
        scrubbed_key = _redact_import_secrets("private_key -----BEGIN OPENSSH PRIVATE KEY-----")
        assert "BEGIN OPENSSH" not in scrubbed_key
        assert "[REDACTED]" in scrubbed_key
        scrubbed_bearer = _redact_import_secrets("Authorization: Bearer abcdef123456")
        assert "abcdef123456" not in scrubbed_bearer
        assert "[REDACTED]" in scrubbed_bearer
        scrubbed_pwd = _redact_import_secrets("login pwd=hunter2 failed")
        assert "hunter2" not in scrubbed_pwd
        assert "[REDACTED]" in scrubbed_pwd

    def test_redact_import_secrets_extended_shapes(self) -> None:
        """AWS/session key names and bare token shapes are redacted (#2)."""
        from apme_gateway.api.router import _redact_import_secrets

        keyed = _redact_import_secrets("aws_access_key_id=AKIAIOSFODNN7EXAMPLE leaked")
        assert "AKIAIOSFODNN7EXAMPLE" not in keyed
        assert "[REDACTED]" in keyed
        bare = _redact_import_secrets("token AKIAIOSFODNN7EXAMPLE exposed")
        assert "AKIAIOSFODNN7EXAMPLE" not in bare
        ghp = _redact_import_secrets("push with ghp_abcdefghij1234567890 token")
        assert "ghp_abcdefghij1234567890" not in ghp
        assert "[REDACTED]" in ghp

    def test_manifest_hash_alias(self) -> None:
        """manifest_hash is an additive alias of requirements_hash."""
        from apme_gateway.api.schemas import SessionVenvInfo

        info = SessionVenvInfo(session_id="abc", manifest_hash="h", requirements_hash="h")
        assert info.manifest_hash == info.requirements_hash == "h"


def test_temp_dir_disk_bytes_prunes_vcs_dirs(tmp_path: Path) -> None:
    """VCS metadata (.git/.hg) is pruned from the disk-size cap accounting.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    from apme_gateway.scan import driver as driver_mod

    (tmp_path / "a.txt").write_bytes(b"x" * 10)
    git_objects = tmp_path / ".git" / "objects"
    git_objects.mkdir(parents=True)
    (git_objects / "pack").write_bytes(b"y" * 1000)
    hg_dir = tmp_path / ".hg"
    hg_dir.mkdir()
    (hg_dir / "store").write_bytes(b"z" * 500)
    assert driver_mod._temp_dir_disk_bytes(str(tmp_path)) == 10


def test_temp_dir_disk_bytes_ignores_symlink_targets(tmp_path: Path) -> None:
    """Symlinks never inflate the total (lstat, regular files only).

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    from apme_gateway.scan import driver as driver_mod

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_bytes(b"x" * 10)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"z" * 5000)
    (root / "evil").symlink_to(outside)
    (root / "dangling").symlink_to(tmp_path / "does-not-exist")
    assert driver_mod._temp_dir_disk_bytes(str(root)) == 10


class TestCollectFormatFiles:
    """Cap-checked, symlink-safe format collection in the scan driver."""

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        """Symlinked *.yml entries are skipped outright, never followed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from pathlib import Path as _Path

        from apme_gateway.scan.driver import _collect_format_files

        root = tmp_path / "repo"
        root.mkdir()
        (root / "a.yml").write_bytes(b"---\n")
        outside = tmp_path / "outside.yml"
        outside.write_bytes(b"secret: true\n")
        (root / "evil.yml").symlink_to(outside)
        collected = _collect_format_files(_Path(root), max_files=100, max_bytes=1024 * 1024)
        assert collected == [("a.yml", b"---\n")]

    def test_containment_escape_not_collected(self, tmp_path: Path) -> None:
        """Files resolving outside the clone root are not collected.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from pathlib import Path as _Path

        from apme_gateway.scan.driver import _collect_format_files

        root = tmp_path / "repo"
        root.mkdir()
        (root / "a.yaml").write_bytes(b"---\n")
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "evil.yml").write_bytes(b"evil: true\n")
        (root / "linkdir").symlink_to(outside_dir, target_is_directory=True)
        collected = _collect_format_files(_Path(root), max_files=100, max_bytes=1024 * 1024)
        assert collected == [("a.yaml", b"---\n")]

    def test_root_through_symlink_still_collected(self, tmp_path: Path) -> None:
        """A clone root reached via a symlink resolves before containment.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from pathlib import Path as _Path

        from apme_gateway.scan.driver import _collect_format_files

        real = tmp_path / "real"
        real.mkdir()
        (real / "a.yml").write_bytes(b"---\n")
        link = tmp_path / "linkroot"
        link.symlink_to(real, target_is_directory=True)
        collected = _collect_format_files(_Path(link), max_files=100, max_bytes=1024 * 1024)
        assert collected == [("a.yml", b"---\n")]

    def test_per_file_cap_before_read(self, tmp_path: Path) -> None:
        """A single huge file trips the stat gate without being read.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from pathlib import Path as _Path

        from apme_gateway.scan.driver import ScanCapExceeded, _collect_format_files

        root = tmp_path / "repo"
        root.mkdir()
        big = root / "big.yml"
        with open(big, "wb") as fh:
            fh.truncate(10 * 1024 * 1024)
        with pytest.raises(ScanCapExceeded, match="too large"):
            _collect_format_files(_Path(root), max_files=100, max_bytes=100)

    def test_aggregate_caps_are_second_gate(self, tmp_path: Path) -> None:
        """Aggregate file/byte caps trip after the per-file stat gate.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from pathlib import Path as _Path

        from apme_gateway.scan.driver import ScanCapExceeded, _collect_format_files

        root = tmp_path / "repo"
        root.mkdir()
        (root / "a.yml").write_bytes(b"x" * 10)
        (root / "b.yml").write_bytes(b"y" * 10)
        with pytest.raises(ScanCapExceeded, match="file limit exceeded"):
            _collect_format_files(_Path(root), max_files=1, max_bytes=1024 * 1024)
        with pytest.raises(ScanCapExceeded, match="size limit exceeded"):
            _collect_format_files(_Path(root), max_files=100, max_bytes=15)
        # ScanCapExceeded stays a ValueError for existing handlers.
        with pytest.raises(ValueError, match="size limit exceeded"):
            _collect_format_files(_Path(root), max_files=100, max_bytes=15)


class TestRunProjectFormat:
    """Driver-owned format: clone + collect + Format gRPC + cleanup."""

    async def _stub_format(
        self,
        monkeypatch: pytest.MonkeyPatch,
        diffs: list[tuple[str, str]],
    ) -> None:
        """Stub the Engine Format unary RPC.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            diffs: (path, diff) pairs served by the fake response.
        """
        mock_channel = MagicMock()
        mock_channel.close = AsyncMock()
        monkeypatch.setattr(
            grpc.aio,
            "insecure_channel",
            MagicMock(return_value=mock_channel),
        )
        resp = MagicMock()
        resp.diffs = [MagicMock(path=p, diff=d) for p, d in diffs]
        mock_stub = MagicMock()
        mock_stub.Format = AsyncMock(return_value=resp)
        monkeypatch.setattr(
            engine_pb2_grpc,
            "EngineStub",
            MagicMock(return_value=mock_stub),
        )

    async def test_happy_path_returns_commit_and_filters_empty_diffs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clone head + non-empty diffs are returned; tempdir is cleaned up.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        from apme_gateway.scan import driver as driver_mod

        async def _no_clone(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(driver_mod, "clone_repo", _no_clone)
        monkeypatch.setattr(driver_mod, "get_clone_head", lambda _d: "abc123")
        monkeypatch.setattr(
            driver_mod,
            "_collect_format_files",
            lambda _root, _mf, _mb: [("a.yml", b"---\n")],
        )
        await self._stub_format(monkeypatch, [("a.yml", "diff-text"), ("b.yml", "")])

        def _format_tmpdirs() -> set[str]:
            """Snapshot stray format tempdirs (cleanup assertion helper).

            Returns:
                Names of ``apme_project_format_*`` entries in the temp dir.
            """
            return {p.name for p in Path(tempfile.gettempdir()).glob("apme_project_format_*")}

        before = _format_tmpdirs()
        commit, diffs = await driver_mod.run_project_format(
            repo_url="https://example.com/r.git",
            branch="main",
            engine_address="127.0.0.1:50051",
        )
        assert commit == "abc123"
        assert diffs == [{"path": "a.yml", "diff": "diff-text"}]
        assert _format_tmpdirs() <= before

    async def test_clone_value_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid repo/branch ValueErrors propagate unwrapped (router maps 400).

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        from apme_gateway.scan import driver as driver_mod

        async def _bad_clone(*args: object, **kwargs: object) -> None:
            raise ValueError("bad branch")

        monkeypatch.setattr(driver_mod, "clone_repo", _bad_clone)
        with pytest.raises(ValueError, match="bad branch"):
            await driver_mod.run_project_format(
                repo_url="https://example.com/r.git",
                branch="bad branch",
                engine_address="127.0.0.1:50051",
            )

    async def test_clone_runtime_error_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clone failures surface as RuntimeError (router maps 502).

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        from apme_gateway.scan import driver as driver_mod

        async def _failing_clone(*args: object, **kwargs: object) -> None:
            raise RuntimeError("git clone failed (exit 128)")

        monkeypatch.setattr(driver_mod, "clone_repo", _failing_clone)
        with pytest.raises(RuntimeError, match="Clone failed"):
            await driver_mod.run_project_format(
                repo_url="https://example.com/r.git",
                branch="main",
                engine_address="127.0.0.1:50051",
            )

    async def test_engine_failure_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Engine Format failures surface as RuntimeError (router maps 502).

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        from apme_gateway.scan import driver as driver_mod

        async def _no_clone(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(driver_mod, "clone_repo", _no_clone)
        monkeypatch.setattr(driver_mod, "get_clone_head", lambda _d: "abc123")
        monkeypatch.setattr(driver_mod, "_collect_format_files", lambda _r, _mf, _mb: [])
        mock_channel = MagicMock()
        mock_channel.close = AsyncMock()
        monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=mock_channel))
        mock_stub = MagicMock()
        mock_stub.Format = AsyncMock(side_effect=RuntimeError("unavailable"))
        monkeypatch.setattr(engine_pb2_grpc, "EngineStub", MagicMock(return_value=mock_stub))
        with pytest.raises(RuntimeError, match="Engine Format failed"):
            await driver_mod.run_project_format(
                repo_url="https://example.com/r.git",
                branch="main",
                engine_address="127.0.0.1:50051",
            )


@pytest.mark.usefixtures("gateway_db")
class TestProjectFormatEndpoint:
    """Thin router mapping for POST /api/v1/projects/{id}/format."""

    async def _seed_project(self, project_id: str = "fmt-proj-1") -> None:
        """Insert a project row for format endpoint tests.

        Args:
            project_id: Primary key for the project.
        """
        async with get_session() as db:
            db.add(
                Project(
                    id=project_id,
                    name="Format Project",
                    repo_url="https://github.com/test/repo.git",
                    branch="main",
                    created_at="2026-03-01T00:00:00Z",
                    health_score=100,
                )
            )
            await db.commit()

    async def test_unknown_project_404(self, client: AsyncClient) -> None:
        """Unknown project ids return 404.

        Args:
            client: Async HTTP test client.
        """
        resp = await client.post("/api/v1/projects/no-such-project/format", json={})
        assert resp.status_code == 404

    async def test_cap_exceeded_413(self, client: AsyncClient) -> None:
        """Driver ScanCapExceeded maps to HTTP 413.

        Args:
            client: Async HTTP test client.
        """
        from apme_gateway.scan.driver import ScanCapExceeded

        await self._seed_project()
        with patch(
            "apme_gateway.scan.driver.run_project_format",
            new=AsyncMock(side_effect=ScanCapExceeded("Format file limit exceeded: 5 files (max 2)")),
        ):
            resp = await client.post("/api/v1/projects/fmt-proj-1/format", json={})
        assert resp.status_code == 413
        assert "file limit exceeded" in resp.json()["detail"]

    async def test_invalid_repo_400(self, client: AsyncClient) -> None:
        """Driver ValueError maps to HTTP 400.

        Args:
            client: Async HTTP test client.
        """
        await self._seed_project()
        with patch(
            "apme_gateway.scan.driver.run_project_format",
            new=AsyncMock(side_effect=ValueError("bad branch")),
        ):
            resp = await client.post("/api/v1/projects/fmt-proj-1/format", json={})
        assert resp.status_code == 400

    async def test_engine_failure_502(self, client: AsyncClient) -> None:
        """Driver RuntimeError maps to HTTP 502.

        Args:
            client: Async HTTP test client.
        """
        await self._seed_project()
        with patch(
            "apme_gateway.scan.driver.run_project_format",
            new=AsyncMock(side_effect=RuntimeError("Engine Format failed: boom")),
        ):
            resp = await client.post("/api/v1/projects/fmt-proj-1/format", json={})
        assert resp.status_code == 502

    async def test_happy_path_passthrough(self, client: AsyncClient) -> None:
        """Commit + diffs from the driver are returned unchanged.

        Args:
            client: Async HTTP test client.
        """
        await self._seed_project()
        with patch(
            "apme_gateway.scan.driver.run_project_format",
            new=AsyncMock(return_value=("abc123", [{"path": "a.yml", "diff": "diff-text"}])),
        ) as mock_format:
            resp = await client.post("/api/v1/projects/fmt-proj-1/format", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "fmt-proj-1"
        assert body["commit"] == "abc123"
        assert body["diffs"] == [{"path": "a.yml", "diff": "diff-text"}]
        _, kwargs = mock_format.call_args
        assert kwargs["branch"] == "main"


@pytest.mark.usefixtures("gateway_db")
class TestImportScanHandler:
    """Handler tests for POST /api/v1/scans/import."""

    @pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
    def _reset_import_throttle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Isolate handler tests from the process-global import throttle.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        from apme_gateway.api import router as router_mod

        monkeypatch.setattr(router_mod, "_IMPORT_RATE_STATE", {})

    def _violation(
        self,
        rule_id: str = "L001",
        remediation_class: str = "",
        message: str = "bad task",
    ) -> dict[str, object]:
        """Build one import violation payload.

        Args:
            rule_id: Rule identifier.
            remediation_class: Tier label (empty when unknown).
            message: Human-readable description.

        Returns:
            Violation payload dict.
        """
        return {
            "rule_id": rule_id,
            "level": "error",
            "message": message,
            "file": "a.yml",
            "line": 5,
            "path": "",
            "remediation_class": remediation_class,
        }

    async def test_tier_counts_with_unknown_to_manual_review(self, client: AsyncClient) -> None:
        """Tier counts resolve per label; empty/unknown labels count as manual_review.

        Args:
            client: Async HTTP test client.
        """
        from sqlalchemy import select as sa_select

        payload = {
            "project_path": "external",
            "scan_type": "check",
            "violations": [
                self._violation("L001", "auto-fixable"),
                self._violation("L002", "auto-fixable"),
                self._violation("L003", "ai-candidate"),
                self._violation("L004", "weird-future-tier"),
                self._violation("L005", ""),
            ],
        }
        resp = await client.post("/api/v1/scans/import", json=payload)
        assert resp.status_code == 201
        scan_id = resp.json()["scan_id"]
        async with get_session() as db:
            scan = await db.get(Scan, scan_id)
            assert scan is not None
            assert (scan.auto_fixable, scan.ai_candidate, scan.manual_review) == (2, 1, 2)
            rows = list((await db.execute(sa_select(Violation).where(Violation.scan_id == scan_id))).scalars().all())
        by_rule = {v.rule_id: v for v in rows}
        assert by_rule["L004"].remediation_class == 0
        assert by_rule["L005"].remediation_class == 0
        assert by_rule["L001"].remediation_class == 1
        assert by_rule["L003"].remediation_class == 2

    async def test_redaction_persisted(self, client: AsyncClient) -> None:
        """Secret values in messages are stored redacted, never raw.

        Args:
            client: Async HTTP test client.
        """
        from sqlalchemy import select as sa_select

        payload = {
            "project_path": "external",
            "scan_type": "check",
            "violations": [self._violation(message="leak api_key=supersecret123 here")],
        }
        resp = await client.post("/api/v1/scans/import", json=payload)
        assert resp.status_code == 201
        scan_id = resp.json()["scan_id"]
        async with get_session() as db:
            rows = list((await db.execute(sa_select(Violation).where(Violation.scan_id == scan_id))).scalars().all())
        assert len(rows) == 1
        assert "supersecret123" not in rows[0].message
        assert "[REDACTED]" in rows[0].message

    async def test_distinct_session_per_import(self, client: AsyncClient) -> None:
        """Each import mints its own session (no shared 'external' session).

        Args:
            client: Async HTTP test client.
        """
        payload = {"project_path": "external", "scan_type": "check", "violations": []}
        first = await client.post("/api/v1/scans/import", json=payload)
        second = await client.post("/api/v1/scans/import", json=payload)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["session_id"] != second.json()["session_id"]
        async with get_session() as db:
            assert await db.get(Session, first.json()["session_id"]) is not None
            assert await db.get(Session, second.json()["session_id"]) is not None

    async def test_unknown_project_404(self, client: AsyncClient) -> None:
        """Linking an import to a missing project returns 404.

        Args:
            client: Async HTTP test client.
        """
        resp = await client.post(
            "/api/v1/scans/import",
            json={"project_id": "no-such-project", "scan_type": "check", "violations": []},
        )
        assert resp.status_code == 404

    async def test_truncation(self) -> None:
        """Over-length message/file values are truncated before persistence."""
        from sqlalchemy import select as sa_select

        from apme_gateway.api.router import import_external_scan
        from apme_gateway.api.schemas import ImportScanRequest, ImportViolationSchema

        body = ImportScanRequest.model_construct(
            project_id=None,
            project_path="external",
            scan_type="check",
            source="cli",
            violations=[
                ImportViolationSchema.model_construct(
                    rule_id="L001",
                    level="warning",
                    message="x" * 5000,
                    file="y" * 1500,
                    line=1,
                    path="",
                    remediation_class="",
                )
            ],
        )
        result = await import_external_scan(body)
        assert result.violation_count == 1
        async with get_session() as db:
            rows = list(
                (await db.execute(sa_select(Violation).where(Violation.scan_id == result.scan_id))).scalars().all()
            )
        assert len(rows) == 1
        assert len(rows[0].message) == 4000
        assert len(rows[0].file) == 1000


async def test_import_scan_handler_rejects_over_cap_400() -> None:
    """The import handler rejects >5000 violations with 400 (above the schema cap).

    DB-free: the cap check raises before any session is opened.
    """
    from apme_gateway.api.router import import_external_scan
    from apme_gateway.api.schemas import ImportScanRequest, ImportViolationSchema

    body = ImportScanRequest.model_construct(
        project_id=None,
        project_path="external",
        scan_type="check",
        source="cli",
        violations=[
            ImportViolationSchema.model_construct(
                rule_id="L001",
                level="warning",
                message="m",
                file="f.yml",
                line=1,
                path="",
                remediation_class="",
            )
        ]
        * 5001,
    )
    with pytest.raises(HTTPException) as exc_info:
        await import_external_scan(body)
    assert exc_info.value.status_code == 400


async def test_project_format_invalid_branch_422(client: AsyncClient) -> None:
    """Branch overrides violating ref rules fail with 422 before any DB/clone work.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post("/api/v1/projects/fmt-proj-1/format", json={"branch": "bad..branch"})
    assert resp.status_code == 422
