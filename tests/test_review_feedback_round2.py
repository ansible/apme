"""Regression tests for PR 708 review-feedback fixes.

Covers the validated ce-code-review findings and the four unresolved
CodeRabbit threads: upload-cap dedup parity across ingress paths,
operator-queue generation preservation without accounting leaks,
Galaxy URL encoded-literal rejection, and Gateway-side HTTPS validation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apme.v1.common_pb2 import File
from apme.v1.engine_pb2 import ScanChunk
from apme_engine.daemon.engine_server import EngineServicer
from apme_gateway.api.schemas import (
    CreateGalaxyServerRequest,
    UpdateGalaxyServerRequest,
)
from apme_gateway.scan.operator_queue import OperatorAnswerQueue
from galaxy_proxy.proxy.server import _validate_galaxy_server_url


async def _one_chunk(files: list[tuple[str, bytes]]) -> AsyncIterator[ScanChunk]:
    """Yield a single terminal ScanChunk with the given files.

    Args:
        files: (path, content) pairs for the chunk.

    Yields:
        ScanChunk: Terminal chunk carrying the files.
    """
    yield ScanChunk(
        scan_id="s",
        files=[File(path=path, content=content) for path, content in files],
        last=True,
    )


async def test_accumulate_chunks_dedups_repeated_normalized_paths() -> None:
    """One chunk repeating a normalized path counts once, like FixSession."""
    accumulated, _, _, _, _ = await EngineServicer._accumulate_chunks(
        _one_chunk([("a.yml", b"v1"), ("./a.yml", b"v2")])
    )
    assert [f.path for f in accumulated] == ["a.yml"]
    assert accumulated[0].content == b"v2"


async def test_drain_through_keeps_queue_accounting_stable() -> None:
    """Retained newer-generation items do not inflate unfinished_tasks.

    After draining and consuming the retained item, ``join()`` must
    return promptly — without the matching ``task_done()`` it would hang
    on a phantom outstanding item.
    """
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    queue.begin_prompt()
    await queue.put("old")
    await queue.put("new", for_generation=99)
    assert queue.drain_through(1) == 1
    queue._items.get_nowait()
    queue._items.task_done()
    await asyncio.wait_for(queue._items.join(), timeout=1.0)


async def test_next_answer_preserves_newer_item_without_losing_it() -> None:
    """A newer-generation head is stashed and served to its own prompt."""
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    queue.begin_prompt()
    await queue.put("future", for_generation=99)
    assert await queue.next_answer(0.01, "Test wait", "defaulting") is None
    assert queue._items.qsize() == 1
    assert await queue.next_answer(0.05, "Future wait", "defaulting", expected_generation=99) == "future"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "url",
    [
        "https://2130706433/collections/",
        "https://0x7f000001/collections/",
        "https://0177.0.0.1/collections/",
        "https://2852039166/collections/",
        "https://[::ffff:127.0.0.1]/collections/",
        "https://[::ffff:169.254.169.254]/collections/",
        "https://localhost/collections/",
        "https://hub.localhost/collections/",
    ],
)
def test_proxy_rejects_encoded_literal_loopback_urls(url: str) -> None:
    """Integer/hex/octal and IPv4-mapped literals must not bypass the block.

    Args:
        url: Galaxy server URL with an encoded literal-IP host.
    """
    with pytest.raises(HTTPException):
        _validate_galaxy_server_url(url)


def test_proxy_still_allows_hostnames_and_public_ips() -> None:
    """Hostnames and public IPs remain allowed (no DNS judgment locally)."""
    _validate_galaxy_server_url("https://galaxy.ansible.com/api/")
    _validate_galaxy_server_url("https://8.8.8.8/api/")


def test_create_galaxy_server_rejects_http_url() -> None:
    """Gateway rejects http:// at the row instead of 422ing the whole push."""
    with pytest.raises(ValidationError):
        CreateGalaxyServerRequest(name="legacy", url="http://galaxy.example.com/api/")
    req = CreateGalaxyServerRequest(name="ok", url="https://galaxy.example.com/api/")
    assert req.url == "https://galaxy.example.com/api/"


def test_update_galaxy_server_allows_none_but_rejects_http() -> None:
    """Partial updates validate the URL only when one is provided."""
    assert UpdateGalaxyServerRequest(name="renamed").url is None
    with pytest.raises(ValidationError):
        UpdateGalaxyServerRequest(url="http://galaxy.example.com/api/")


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "url",
    [
        "https://user:pass@galaxy.example.com/api/",
        "https://127.0.0.1/api/",
        "https://2130706433/api/",
        "https://[::ffff:169.254.169.254]/api/",
        "https://localhost/api/",
        "https://hub.localhost/api/",
    ],
)
def test_create_galaxy_server_mirrors_proxy_local_address_gate(url: str) -> None:
    """Gateway rejects what the proxy would 422, at the offending row.

    Args:
        url: Galaxy server URL the proxy refuses.
    """
    with pytest.raises(ValidationError):
        CreateGalaxyServerRequest(name="bad", url=url)
