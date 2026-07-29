"""OpenTelemetry metrics bootstrap for APME services.

Enabled when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set and ``OTEL_SDK_DISABLED``
is not truthy. Otherwise this module is a no-op so local CLI/daemon runs
stay light without a collector.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False
_meter: Any = None
_provider: Any = None

_DEFAULT_EXPORT_INTERVAL_MS = 10000


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _export_interval_ms() -> int:
    """Parse ``OTEL_METRIC_EXPORT_INTERVAL`` with a safe default.

    Returns:
        Export interval in milliseconds (always positive).
    """
    raw = (os.environ.get("OTEL_METRIC_EXPORT_INTERVAL") or "").strip()
    if not raw:
        return _DEFAULT_EXPORT_INTERVAL_MS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid OTEL_METRIC_EXPORT_INTERVAL=%r; using default %d",
            raw,
            _DEFAULT_EXPORT_INTERVAL_MS,
        )
        return _DEFAULT_EXPORT_INTERVAL_MS
    if value <= 0:
        logger.warning(
            "Non-positive OTEL_METRIC_EXPORT_INTERVAL=%r; using default %d",
            raw,
            _DEFAULT_EXPORT_INTERVAL_MS,
        )
        return _DEFAULT_EXPORT_INTERVAL_MS
    return value


def setup_otel(service_name: str | None = None) -> None:
    """Initialize the global MeterProvider with an OTLP HTTP exporter.

    Misconfiguration or missing packages leave metrics disabled (no-op).
    Failures never raise out of this function.

    Args:
        service_name: Overrides ``OTEL_SERVICE_NAME`` when provided.
    """
    global _initialized, _meter, _provider
    if _initialized:
        return
    _initialized = True

    try:
        if _truthy(os.environ.get("OTEL_SDK_DISABLED")):
            logger.info("OpenTelemetry disabled via OTEL_SDK_DISABLED")
            return

        endpoint = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
        if not endpoint:
            logger.debug("OpenTelemetry inactive: OTEL_EXPORTER_OTLP_ENDPOINT unset")
            return

        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
        from opentelemetry.sdk.resources import Resource

        from apme_engine.observability.buckets import (
            GALAXY_FETCH_DURATION_BUCKETS_S,
            HTTP_DURATION_BUCKETS_S,
            SCAN_DURATION_BUCKETS_S,
            VALIDATOR_DURATION_BUCKETS_S,
            VENV_DURATION_BUCKETS_S,
        )

        name = (service_name or os.environ.get("OTEL_SERVICE_NAME") or "apme").strip()
        resource_attrs: dict[str, str] = {"service.name": name}
        extra = (os.environ.get("OTEL_RESOURCE_ATTRIBUTES") or "").strip()
        for part in extra.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                key, value = key.strip(), value.strip()
                if key and value:
                    resource_attrs[key] = value

        # Accept base endpoint (http://host:4318) or full metrics URL.
        metrics_endpoint = endpoint
        if not endpoint.rstrip("/").endswith("/v1/metrics"):
            metrics_endpoint = endpoint.rstrip("/") + "/v1/metrics"

        export_interval_ms = _export_interval_ms()
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=metrics_endpoint),
            export_interval_millis=export_interval_ms,
        )
        # Enforce domain-tuned buckets (advisory alone is insufficient for quantile math).
        views = [
            View(
                instrument_name="apme.scan.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(SCAN_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.scan.phase.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VALIDATOR_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.validator.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VALIDATOR_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.http.server.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(HTTP_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.venv.acquire.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VENV_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.galaxy.fetch.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(GALAXY_FETCH_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.galaxy.wheel.serve.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(GALAXY_FETCH_DURATION_BUCKETS_S)),
            ),
            View(
                instrument_name="apme.grpc.server.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VALIDATOR_DURATION_BUCKETS_S)),
            ),
        ]
        _provider = MeterProvider(
            resource=Resource.create(resource_attrs),
            metric_readers=[reader],
            views=views,
        )
        metrics.set_meter_provider(_provider)
        _meter = metrics.get_meter("apme", "0.1.0")
        logger.info(
            "OpenTelemetry metrics enabled service=%s endpoint=%s interval_ms=%d",
            name,
            metrics_endpoint,
            export_interval_ms,
        )
    except ImportError:
        logger.warning("OpenTelemetry packages missing; metrics export disabled")
        _meter = None
        _provider = None
    except Exception:  # noqa: BLE001 — never crash callers for metrics bootstrap
        logger.warning("OpenTelemetry setup failed; metrics export disabled", exc_info=True)
        _meter = None
        _provider = None


def get_meter() -> Any:
    """Return the APME meter, or ``None`` when OTel is inactive.

    Returns:
        The configured meter instance, or ``None`` when export is disabled.
    """
    return _meter


def shutdown_otel() -> None:
    """Flush and shut down the MeterProvider if it was started."""
    global _initialized, _provider, _meter
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.debug("OpenTelemetry shutdown failed", exc_info=True)
    _provider = None
    _meter = None
    _initialized = False
    try:
        from apme_engine.observability.metrics import reset_instruments

        reset_instruments()
    except Exception:  # noqa: BLE001
        logger.debug("Failed to reset metric instruments after shutdown", exc_info=True)
