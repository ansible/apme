"""Tests for log bridge context propagation into worker threads."""

from __future__ import annotations

import contextvars
import logging
import threading

import pytest

from apme_engine.log_bridge import RequestLogHandler, attach_collector, install_handler

# A wedged worker thread would otherwise stall the xdist worker until the CI
# job timeout cancels the whole run (observed once in the wild on a
# frontend-only change, so nothing in this path was at fault). The worker runs
# on a daemon thread and is joined with a timeout, so a blocked
# ``RequestLogHandler.emit`` fails fast with a diagnostic and — unlike the
# default executor's non-daemon threads, which CPython joins at interpreter
# shutdown — can never keep the test process (or the CI job) alive afterwards.
_WORKER_TIMEOUT_SECONDS = 30.0


def _log_from_worker(message: str) -> None:
    """Emit an info log from a worker thread.

    Args:
        message: Log message text.
    """
    logging.getLogger("apme.test_bridge").info(message)


def test_worker_thread_propagates_collector_sink_via_copy_context() -> None:
    """Worker-thread logs are collected when the worker uses ``ctx.run``."""
    install_handler()
    root = logging.getLogger()
    if not any(isinstance(h, RequestLogHandler) for h in root.handlers):
        root.addHandler(RequestLogHandler())

    with attach_collector() as sink:
        ctx = contextvars.copy_context()
        worker = threading.Thread(
            target=ctx.run,
            args=(_log_from_worker, "worker-log-message"),
            name="log-bridge-test-worker",
            daemon=True,
        )
        worker.start()
        worker.join(_WORKER_TIMEOUT_SECONDS)
        if worker.is_alive():
            pytest.fail(
                f"log-bridge worker thread did not finish within {_WORKER_TIMEOUT_SECONDS}s; "
                "the thread appears wedged (logging handler lock or stderr stall?) and is "
                "left running as a daemon so the test process can still exit"
            )
        entries = sink.entries

    assert any(e.message == "worker-log-message" for e in entries)
