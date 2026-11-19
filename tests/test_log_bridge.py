"""Tests for log bridge context propagation into executor workers."""

from __future__ import annotations

import asyncio
import contextvars
import logging

import pytest

from apme_engine.log_bridge import RequestLogHandler, attach_collector, install_handler

# A wedged executor worker thread would otherwise stall the xdist worker until
# the CI job timeout cancels the whole run (observed once in the wild on a
# frontend-only change, so nothing in this path was at fault). Fail fast with a
# diagnostic instead of hanging silently for hours.
_EXECUTOR_TIMEOUT_SECONDS = 30.0


def _log_from_worker(message: str) -> None:
    """Emit an info log from a worker thread.

    Args:
        message: Log message text.
    """
    logging.getLogger("apme.test_bridge").info(message)


async def test_executor_propagates_collector_sink_via_copy_context() -> None:
    """Worker-thread logs are collected when executor uses ``ctx.run``."""
    install_handler()
    root = logging.getLogger()
    if not any(isinstance(h, RequestLogHandler) for h in root.handlers):
        root.addHandler(RequestLogHandler())

    with attach_collector() as sink:
        ctx = contextvars.copy_context()
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None,
                    ctx.run,
                    _log_from_worker,
                    "executor-log-message",
                ),
                timeout=_EXECUTOR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            pytest.fail(
                f"log-bridge executor worker did not finish within {_EXECUTOR_TIMEOUT_SECONDS}s; "
                "the worker thread appears wedged (logging handler lock or stderr stall?)"
            )
        entries = sink.entries

    assert any(e.message == "executor-log-message" for e in entries)
