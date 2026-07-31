"""Tests for log bridge context propagation into executor workers."""

from __future__ import annotations

import asyncio
import contextvars
import logging

from apme_engine.log_bridge import RequestLogHandler, attach_collector, install_handler


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
        await asyncio.get_running_loop().run_in_executor(
            None,
            ctx.run,
            _log_from_worker,
            "executor-log-message",
        )
        entries = sink.entries

    assert any(e.message == "executor-log-message" for e in entries)
