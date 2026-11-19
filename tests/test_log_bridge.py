"""Tests for log bridge context propagation into executor workers."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import multiprocessing
import sys

import pytest

from apme_engine.log_bridge import RequestLogHandler, attach_collector, install_handler

# A wedged worker would otherwise stall the xdist worker until the CI job
# timeout cancels the whole run (observed once in the wild on a frontend-only
# change, so nothing in this path was at fault). The scenario therefore runs
# in a child process that the test terminates on timeout: a permanently
# blocked ``RequestLogHandler.emit`` cannot hold up the test, the rest of the
# suite, or interpreter exit. Neither a daemon worker thread nor detaching
# the handler is enough, because ``logging.shutdown()`` runs at interpreter
# exit and acquires every live handler's lock — a lock a wedged ``emit``
# holds — so only a process boundary that can be killed bounds every path.
_SCENARIO_TIMEOUT_SECONDS = 30.0


def _log_from_worker(message: str) -> None:
    """Emit an info log from a worker thread.

    Args:
        message: Log message text.
    """
    logging.getLogger("apme.test_bridge").info(message)


async def _log_via_executor(message: str) -> None:
    """Emit a worker log through the executor pattern the engine documents.

    Args:
        message: Log message text.
    """
    ctx = contextvars.copy_context()
    await asyncio.get_running_loop().run_in_executor(None, ctx.run, _log_from_worker, message)


def _run_scenario(message: str) -> None:
    """Run the log-bridge executor scenario and exit with its verdict.

    Exits with status 0 when the collector sink captured the worker's
    message, status 1 when it did not.

    Args:
        message: Log message text to emit from the worker thread.
    """
    install_handler()
    root = logging.getLogger()
    if not any(isinstance(h, RequestLogHandler) for h in root.handlers):
        root.addHandler(RequestLogHandler())

    with attach_collector() as sink:
        asyncio.run(_log_via_executor(message))
        captured = any(e.message == message for e in sink.entries)
    if not captured:
        print(f"collector sink did not capture worker message {message!r}", file=sys.stderr)
    sys.exit(0 if captured else 1)


def test_executor_propagates_collector_sink_via_copy_context() -> None:
    """Worker-thread logs are collected when executor uses ``ctx.run``."""
    scenario = multiprocessing.get_context("spawn").Process(
        target=_run_scenario,
        args=("executor-log-message",),
        name="log-bridge-scenario",
        daemon=True,
    )
    scenario.start()
    scenario.join(_SCENARIO_TIMEOUT_SECONDS)
    if scenario.is_alive():
        scenario.terminate()
        scenario.join(_SCENARIO_TIMEOUT_SECONDS)
        if scenario.is_alive():
            scenario.kill()
            scenario.join()
        pytest.fail(
            f"log-bridge scenario process did not finish within {_SCENARIO_TIMEOUT_SECONDS}s; "
            "the worker appears wedged (logging handler lock or stderr stall?) and the "
            "scenario process was terminated"
        )
    assert scenario.exitcode == 0, (
        f"collector sink did not capture the worker's message (scenario exit code {scenario.exitcode})"
    )
