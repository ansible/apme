"""Operational observability (OpenTelemetry metrics).

Product-facing per-scan diagnostics remain in ADR-013 gRPC messages.
This package exports fleet/ops metrics via OTLP when configured.
"""

from apme_engine.observability.metrics import (
    record_galaxy_fetch,
    record_galaxy_wheel_serve,
    record_scan_diagnostics,
    record_venv_acquire,
)
from apme_engine.observability.otel_setup import get_meter, setup_otel, shutdown_otel

__all__ = [
    "get_meter",
    "record_galaxy_fetch",
    "record_galaxy_wheel_serve",
    "record_scan_diagnostics",
    "record_venv_acquire",
    "setup_otel",
    "shutdown_otel",
]
