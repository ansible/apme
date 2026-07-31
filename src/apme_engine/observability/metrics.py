"""APME scan/ops metric instruments.

Metric names use OTel dotted form; the Collector Prometheus exporter maps
them to Prometheus names such as ``apme_scan_duration_seconds``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apme_engine.observability.buckets import (
    GALAXY_FETCH_DURATION_BUCKETS_S,
    HTTP_DURATION_BUCKETS_S,
    SCAN_DURATION_BUCKETS_S,
    VALIDATOR_DURATION_BUCKETS_S,
    VENV_DURATION_BUCKETS_S,
)
from apme_engine.observability.otel_setup import get_meter

if TYPE_CHECKING:
    from apme.v1.primary_pb2 import ScanDiagnostics

logger = logging.getLogger(__name__)

_scan_duration: Any = None
_phase_duration: Any = None
_validator_duration: Any = None
_scan_completed: Any = None
_http_duration: Any = None
_venv_duration: Any = None
_venv_completed: Any = None
_galaxy_fetch_duration: Any = None
_galaxy_fetch_completed: Any = None
_galaxy_wheel_duration: Any = None
_galaxy_wheel_completed: Any = None
_grpc_duration: Any = None
_grpc_completed: Any = None
_instruments_ready = False


def _ensure_instruments() -> bool:
    """Lazily create instruments once a meter is available.

    Returns:
        True when instruments exist and recording is safe.
    """
    global _scan_duration, _phase_duration, _validator_duration, _scan_completed
    global _http_duration, _venv_duration, _venv_completed
    global _galaxy_fetch_duration, _galaxy_fetch_completed
    global _galaxy_wheel_duration, _galaxy_wheel_completed
    global _grpc_duration, _grpc_completed, _instruments_ready
    if _instruments_ready:
        return _scan_duration is not None

    meter = get_meter()
    if meter is None:
        # Do not latch ready: setup_otel() may run later in this process.
        return False

    _instruments_ready = True

    _scan_duration = meter.create_histogram(
        name="apme.scan.duration",
        unit="s",
        description="End-to-end APME scan pipeline duration",
        explicit_bucket_boundaries_advisory=list(SCAN_DURATION_BUCKETS_S),
    )
    _phase_duration = meter.create_histogram(
        name="apme.scan.phase.duration",
        unit="s",
        description="APME scan phase durations (parse, annotate, fan_out, engine)",
        explicit_bucket_boundaries_advisory=list(VALIDATOR_DURATION_BUCKETS_S),
    )
    _validator_duration = meter.create_histogram(
        name="apme.validator.duration",
        unit="s",
        description="Per-validator duration from ADR-013 diagnostics",
        explicit_bucket_boundaries_advisory=list(VALIDATOR_DURATION_BUCKETS_S),
    )
    _http_duration = meter.create_histogram(
        name="apme.http.server.duration",
        unit="s",
        description="HTTP server request duration",
        explicit_bucket_boundaries_advisory=list(HTTP_DURATION_BUCKETS_S),
    )
    _venv_duration = meter.create_histogram(
        name="apme.venv.acquire.duration",
        unit="s",
        description="Session venv acquire duration (warm hit, incremental install, or cold create)",
        explicit_bucket_boundaries_advisory=list(VENV_DURATION_BUCKETS_S),
    )
    _venv_completed = meter.create_counter(
        name="apme.venv.acquire.completed",
        unit="{acquire}",
        description="Completed session venv acquires by outcome",
    )
    _galaxy_fetch_duration = meter.create_histogram(
        name="apme.galaxy.fetch.duration",
        unit="s",
        description="Outbound Ansible Galaxy fetch duration (collection download or version lookup)",
        explicit_bucket_boundaries_advisory=list(GALAXY_FETCH_DURATION_BUCKETS_S),
    )
    _galaxy_fetch_completed = meter.create_counter(
        name="apme.galaxy.fetch.completed",
        unit="{fetch}",
        description="Completed outbound Ansible Galaxy fetches by operation and status",
    )
    _galaxy_wheel_duration = meter.create_histogram(
        name="apme.galaxy.wheel.serve.duration",
        unit="s",
        description="Galaxy Proxy wheel serve duration (cache hit or download miss)",
        explicit_bucket_boundaries_advisory=list(GALAXY_FETCH_DURATION_BUCKETS_S),
    )
    _galaxy_wheel_completed = meter.create_counter(
        name="apme.galaxy.wheel.serve.completed",
        unit="{serve}",
        description="Completed Galaxy Proxy wheel serves by cache outcome",
    )
    _scan_completed = meter.create_counter(
        name="apme.scan.completed",
        unit="{scan}",
        description="Completed APME scans by status",
    )
    _grpc_duration = meter.create_histogram(
        name="apme.grpc.server.duration",
        unit="s",
        description="gRPC server RPC duration (Validator Validate/Health)",
        explicit_bucket_boundaries_advisory=list(VALIDATOR_DURATION_BUCKETS_S),
    )
    _grpc_completed = meter.create_counter(
        name="apme.grpc.server.completed",
        unit="{rpc}",
        description="Completed gRPC server RPCs by method and status",
    )
    return True


def reset_instruments() -> None:
    """Clear cached instruments so a later ``setup_otel`` can recreate them."""
    global _scan_duration, _phase_duration, _validator_duration, _scan_completed
    global _http_duration, _venv_duration, _venv_completed
    global _galaxy_fetch_duration, _galaxy_fetch_completed
    global _galaxy_wheel_duration, _galaxy_wheel_completed
    global _grpc_duration, _grpc_completed, _instruments_ready
    _scan_duration = None
    _phase_duration = None
    _validator_duration = None
    _scan_completed = None
    _http_duration = None
    _venv_duration = None
    _venv_completed = None
    _galaxy_fetch_duration = None
    _galaxy_fetch_completed = None
    _galaxy_wheel_duration = None
    _galaxy_wheel_completed = None
    _grpc_duration = None
    _grpc_completed = None
    _instruments_ready = False


def _collections_requested_bucket(count: int) -> str:
    """Coarse bucket for collection counts to avoid Prometheus cardinality blow-ups.

    Args:
        count: Number of collection specs requested.

    Returns:
        One of ``0``, ``1-5``, ``6-20``, or ``21+``.
    """
    n = max(count, 0)
    if n == 0:
        return "0"
    if n <= 5:
        return "1-5"
    if n <= 20:
        return "6-20"
    return "21+"


def record_scan_diagnostics(diag: ScanDiagnostics, *, status: str = "ok") -> None:
    """Record histograms/counters from a completed ``ScanDiagnostics``.

    Args:
        diag: Aggregated scan diagnostics from the Primary pipeline.
        status: Outcome label (``ok`` or ``error``).
    """
    if not _ensure_instruments():
        return

    try:
        attrs = {"status": status}
        _scan_duration.record(max(diag.total_ms, 0.0) / 1000.0, attrs)
        _scan_completed.add(1, attrs)

        phases = {
            "parse": diag.engine_parse_ms,
            "annotate": diag.engine_annotate_ms,
            "engine": diag.engine_total_ms,
            "fan_out": diag.fan_out_ms,
        }
        for phase, ms in phases.items():
            _phase_duration.record(max(ms, 0.0) / 1000.0, {"phase": phase, "status": status})

        for vdiag in diag.validators:
            name = (vdiag.validator_name or "unknown").strip() or "unknown"
            _validator_duration.record(
                max(vdiag.total_ms, 0.0) / 1000.0,
                {"validator": name, "status": status},
            )
    except Exception:  # noqa: BLE001 — never fail the scan path for metrics
        logger.debug("Failed to record scan diagnostics metrics", exc_info=True)


def record_venv_acquire(
    duration_s: float,
    *,
    outcome: str,
    status: str = "ok",
    ansible_core_version: str = "",
    collections_requested: int = 0,
) -> None:
    """Record a session venv acquire duration.

    Args:
        duration_s: Wall time for ``VenvSessionManager.acquire``.
        outcome: ``warm``, ``incremental``, or ``create``.
        status: ``ok`` or ``error``.
        ansible_core_version: Normalised ansible-core version label.
        collections_requested: Number of collection specs requested.
    """
    if not _ensure_instruments():
        return
    if _venv_duration is None or _venv_completed is None:
        return
    try:
        attrs: dict[str, str] = {
            "outcome": outcome or "unknown",
            "status": status,
            "collections_requested": _collections_requested_bucket(collections_requested),
        }
        ver = (ansible_core_version or "").strip()
        if ver:
            attrs["ansible_core_version"] = ver
        _venv_duration.record(max(duration_s, 0.0), attrs)
        _venv_completed.add(1, attrs)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record venv acquire metrics", exc_info=True)


def record_http_request(duration_s: float, *, method: str, status_code: int, service: str) -> None:
    """Record an HTTP server request duration (Gateway / Galaxy Proxy).

    Args:
        duration_s: Request duration in seconds.
        method: HTTP method.
        status_code: Response status code.
        service: Logical service name (``gateway`` or ``galaxy-proxy``).
    """
    if not _ensure_instruments():
        return
    if _http_duration is None:
        return
    try:
        _http_duration.record(
            max(duration_s, 0.0),
            {
                "http.request.method": method,
                "http.response.status_code": str(status_code),
                "service": service,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record HTTP metrics", exc_info=True)


def record_grpc_request(
    duration_s: float,
    *,
    method: str,
    status_code: str,
    service: str,
) -> None:
    """Record a gRPC server RPC duration (validator ``Validate`` / ``Health``).

    Distinct from ``apme.validator.duration``, which Primary records from
    ADR-013 scan diagnostics after fan-out completes.

    Args:
        duration_s: RPC wall time in seconds.
        method: Bare RPC method name (``Validate``, ``Health``).
        status_code: gRPC status code name (``OK``, ``INTERNAL``, …).
        service: Logical service label (``native``, ``opa``, …).
    """
    if not _ensure_instruments():
        return
    if _grpc_duration is None or _grpc_completed is None:
        return
    try:
        attrs = {
            "rpc.method": method or "unknown",
            "rpc.grpc.status_code": status_code or "UNKNOWN",
            "service": service or "unknown",
        }
        _grpc_duration.record(max(duration_s, 0.0), attrs)
        _grpc_completed.add(1, attrs)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record gRPC metrics", exc_info=True)


def record_galaxy_fetch(
    duration_s: float,
    *,
    operation: str,
    status: str = "ok",
    collections_requested: int = 0,
    server: str = "",
) -> None:
    """Record an outbound Ansible Galaxy fetch (download or version lookup).

    Args:
        duration_s: Wall time for the fetch attempt.
        operation: ``download`` (``ansible-galaxy collection download``) or
            ``version_lookup`` (Galaxy API versions HTTP).
        status: ``ok``, ``error``, or ``timeout``.
        collections_requested: Specs requested for ``download`` (0 otherwise).
        server: Galaxy server host for ``version_lookup`` (omit for download).
    """
    if not _ensure_instruments():
        return
    if _galaxy_fetch_duration is None or _galaxy_fetch_completed is None:
        return
    try:
        attrs: dict[str, str] = {
            "operation": operation or "unknown",
            "status": status,
        }
        if operation == "download":
            attrs["collections_requested"] = _collections_requested_bucket(collections_requested)
        host = (server or "").strip()
        if host:
            attrs["server"] = host
        _galaxy_fetch_duration.record(max(duration_s, 0.0), attrs)
        _galaxy_fetch_completed.add(1, attrs)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record Galaxy fetch metrics", exc_info=True)


def record_galaxy_wheel_serve(
    duration_s: float,
    *,
    outcome: str,
    status: str = "ok",
) -> None:
    """Record a Galaxy Proxy ``GET /wheels/{filename}`` serve.

    Args:
        duration_s: Wall time to serve the wheel (includes download on miss).
        outcome: ``hit`` (served from ``/cache/wheels``) or ``miss`` (download
            + convert + cache).
        status: ``ok`` or ``error``.
    """
    if not _ensure_instruments():
        return
    if _galaxy_wheel_duration is None or _galaxy_wheel_completed is None:
        return
    try:
        attrs = {
            "outcome": outcome or "unknown",
            "status": status,
        }
        _galaxy_wheel_duration.record(max(duration_s, 0.0), attrs)
        _galaxy_wheel_completed.add(1, attrs)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record Galaxy wheel serve metrics", exc_info=True)
