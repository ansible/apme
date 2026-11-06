"""Tests for log bridge context propagation into executor workers."""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from apme_engine.log_bridge import RequestLogHandler, attach_collector


def test_executor_propagates_collector_sink_via_copy_context() -> None:
    """Worker-thread records reach the collector under ``ctx.run``."""
    handler = RequestLogHandler()
    record = logging.LogRecord(
        name="apme.test_bridge",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="executor-log-message",
        args=(),
        exc_info=None,
    )
    # Use a private stream and executor so this check doesn't touch pytest's
    # captured stderr or a process-wide logger/executor configuration.
    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        patch("apme_engine.log_bridge.sys", SimpleNamespace(stderr=StringIO())),
        attach_collector() as sink,
    ):
        ctx = contextvars.copy_context()
        executor.submit(ctx.run, handler.handle, record).result(timeout=5)
        entries = sink.entries

    assert any(e.message == "executor-log-message" for e in entries)
