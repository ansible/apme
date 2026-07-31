"""Centralized log bridge: routes Python logging to gRPC transport + stderr (ADR-033).

All subsystems use standard ``logging.getLogger("apme.<subsystem>")``.
This module provides a custom handler that:

1. Always writes to stderr (daemon.log in daemon mode, container log in pod mode)
2. Conditionally collects ``ProgressUpdate`` protos into a per-request
   ``CollectorSink`` (validators, Primary ``Format`` RPC, etc.)

The active sink is tracked via ``contextvars`` so concurrent requests each
get their own log collection without interference. Blocking validator work
handed to ``run_in_executor`` must capture the context on the event-loop
thread first::

    ctx = contextvars.copy_context()
    await loop.run_in_executor(None, ctx.run, blocking_fn, *args)
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading

from apme.v1.common_pb2 import ProgressUpdate

_PHASE_PREFIX = "apme."

_PY_TO_PROTO_LEVEL: dict[int, int] = {
    logging.DEBUG: 1,
    logging.INFO: 2,
    logging.WARNING: 3,
    logging.ERROR: 4,
    logging.CRITICAL: 4,
}

_INSTALLED = False


class LogSink:
    """Base class for per-request log sinks."""

    def emit(self, entry: ProgressUpdate) -> None:
        """Deliver a single log entry to the sink.

        Args:
            entry: ProgressUpdate proto to deliver.

        Raises:
            NotImplementedError: Subclasses must override this method.
        """
        raise NotImplementedError


class CollectorSink(LogSink):
    """Thread-safe sink that appends entries to a list.

    Used by validators (per ``Validate()`` call) and by Primary for
    unary RPCs such as ``Format``.
    """

    def __init__(self) -> None:
        """Initialize an empty thread-safe entry list."""
        self._entries: list[ProgressUpdate] = []
        self._lock = threading.Lock()

    def emit(self, entry: ProgressUpdate) -> None:
        """Append a log entry to the collection (thread-safe).

        Args:
            entry: ProgressUpdate proto to collect.
        """
        with self._lock:
            self._entries.append(entry)

    @property
    def entries(self) -> list[ProgressUpdate]:
        """Return a snapshot of collected entries.

        Returns:
            Copy of the collected ProgressUpdate list.
        """
        with self._lock:
            return list(self._entries)


_current_sink: contextvars.ContextVar[LogSink | None] = contextvars.ContextVar("apme_log_sink", default=None)


class _AttachCollector:
    """Context manager that sets a ``CollectorSink`` for the current context."""

    def __init__(self) -> None:
        self.sink = CollectorSink()
        self._token: contextvars.Token[LogSink | None] | None = None

    def __enter__(self) -> CollectorSink:
        self._token = _current_sink.set(self.sink)
        return self.sink

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _current_sink.reset(self._token)


def attach_collector() -> _AttachCollector:
    """Return a context manager that installs a ``CollectorSink``.

    Returns:
        Context manager yielding the ``CollectorSink``.
    """
    return _AttachCollector()


def _derive_phase(logger_name: str) -> str:
    """Derive the ``phase`` field from a logger name.

    ``apme.primary`` -> ``"primary"``, ``apme.remediation.engine`` -> ``"remediation"``.

    Args:
        logger_name: Dotted Python logger name.

    Returns:
        Short phase string for the ``ProgressUpdate.phase`` field.
    """
    if logger_name.startswith(_PHASE_PREFIX):
        remainder = logger_name[len(_PHASE_PREFIX) :]
        return remainder.split(".")[0]
    return logger_name.split(".")[0] if logger_name else ""


class RequestLogHandler(logging.Handler):
    """Logging handler that routes records to stderr and the active gRPC sink.

    Installed once per process via ``install_handler()``.
    """

    def __init__(self) -> None:
        """Configure handler with DEBUG level and a timestamped stderr formatter."""
        super().__init__(level=logging.DEBUG)
        self._stderr_formatter = logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Write to stderr and route to the active per-request gRPC sink.

        Args:
            record: Python log record to process.
        """
        # 1. Always write to stderr (-> daemon.log or container log)
        try:
            msg = self._stderr_formatter.format(record)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)

        # 2. Route to per-request gRPC sink if one is active
        sink = _current_sink.get(None)
        if sink is not None:
            proto_level = _PY_TO_PROTO_LEVEL.get(record.levelno, 2)
            phase = _derive_phase(record.name)
            try:
                formatted_msg = record.getMessage()
            except Exception:
                formatted_msg = str(record.msg)
            entry = ProgressUpdate(
                message=formatted_msg,
                phase=phase,
                level=proto_level,
            )
            try:
                sink.emit(entry)
            except Exception:
                self.handleError(record)


def install_handler() -> None:
    """Install ``RequestLogHandler`` on the root logger (idempotent).

    Called by every process entry point — ``launcher.py`` for daemon mode,
    each ``*_main.py`` for pod mode.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove default handlers to avoid duplicate stderr output
    for h in root.handlers[:]:
        root.removeHandler(h)

    root.addHandler(RequestLogHandler())
