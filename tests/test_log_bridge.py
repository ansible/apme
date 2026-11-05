"""Tests for log bridge context propagation into executor workers."""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from apme_engine.log_bridge import RequestLogHandler, attach_collector


def _log_from_worker(message: str) -> None:
    """Emit an info log from a worker thread.

    Args:
        message: Log message text.
    """
    logging.getLogger("apme.test_bridge").info(message)


def test_executor_propagates_collector_sink_via_copy_context() -> None:
    """Worker-thread logs are collected when executor uses ``ctx.run``."""
    logger = logging.getLogger("apme.test_bridge")
    handler = RequestLogHandler()
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        # Use a private stream and handler so an unrelated test's global
        # logging configuration or pytest's captured stderr cannot block the
        # executor worker while this test checks context propagation. A
        # dedicated executor also keeps unrelated default-pool work from
        # starving this context propagation check.
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            patch("apme_engine.log_bridge.sys", SimpleNamespace(stderr=StringIO())),
            attach_collector() as sink,
        ):
            ctx = contextvars.copy_context()
            executor.submit(
                ctx.run,
                _log_from_worker,
                "executor-log-message",
            ).result()
            entries = sink.entries
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    assert any(e.message == "executor-log-message" for e in entries)
