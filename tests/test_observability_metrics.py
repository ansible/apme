"""Tests for OpenTelemetry metrics helpers (no collector required)."""

from __future__ import annotations

import pytest

from apme.v1.common_pb2 import ValidatorDiagnostics
from apme.v1.primary_pb2 import ScanDiagnostics
from apme_engine.observability import metrics as metrics_mod
from apme_engine.observability import otel_setup


def test_setup_otel_noop_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """OTel stays inactive when no OTLP endpoint is configured.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    otel_setup._initialized = False
    otel_setup._meter = None
    otel_setup._provider = None
    otel_setup.setup_otel("test-service")
    assert otel_setup.get_meter() is None


def test_ensure_instruments_does_not_latch_before_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """record_* before setup_otel must not permanently disable instruments.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(otel_setup, "_meter", None)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    metrics_mod.record_scan_diagnostics(ScanDiagnostics(total_ms=1.0))
    assert metrics_mod._instruments_ready is False


def test_setup_otel_invalid_export_interval_is_noop_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed OTEL_METRIC_EXPORT_INTERVAL must not raise from setup_otel.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "not-an-int")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    otel_setup._initialized = False
    otel_setup._meter = None
    otel_setup._provider = None
    try:
        otel_setup.setup_otel("test-service")  # must not raise
        assert otel_setup._export_interval_ms() == 10000
    finally:
        otel_setup.shutdown_otel()


def test_shutdown_otel_allows_reinit(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutdown_otel clears the initialized latch for a later setup_otel.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    otel_setup._initialized = True
    otel_setup._meter = object()
    otel_setup._provider = None
    otel_setup.shutdown_otel()
    assert otel_setup._initialized is False
    assert otel_setup.get_meter() is None
    otel_setup.setup_otel("again")
    assert otel_setup._initialized is True


def test_setup_otel_respects_sdk_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """OTEL_SDK_DISABLED=true disables export even when an endpoint is set.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    otel_setup._initialized = False
    otel_setup._meter = None
    otel_setup._provider = None
    otel_setup.setup_otel("test-service")
    assert otel_setup.get_meter() is None


def test_record_scan_diagnostics_noop_without_meter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recording diagnostics is a no-op when the meter was never started.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    monkeypatch.setattr(otel_setup, "_meter", None)
    diag = ScanDiagnostics(total_ms=1234.0, fan_out_ms=100.0, engine_parse_ms=10.0)
    metrics_mod.record_scan_diagnostics(diag)  # must not raise


def test_record_scan_diagnostics_with_inmemory_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scan diagnostics populate expected OTel metric names in-memory.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import (
        SCAN_DURATION_BUCKETS_S,
        VALIDATOR_DURATION_BUCKETS_S,
    )

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
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
        ],
    )
    meter = provider.get_meter("apme", "0.1.0")

    monkeypatch.setattr(otel_setup, "_meter", meter)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    monkeypatch.setattr(metrics_mod, "_phase_duration", None)
    monkeypatch.setattr(metrics_mod, "_validator_duration", None)
    monkeypatch.setattr(metrics_mod, "_http_duration", None)
    monkeypatch.setattr(metrics_mod, "_scan_completed", None)

    diag = ScanDiagnostics(
        total_ms=2500.0,
        fan_out_ms=400.0,
        engine_parse_ms=100.0,
        engine_annotate_ms=50.0,
        engine_total_ms=200.0,
        validators=[
            ValidatorDiagnostics(validator_name="native", total_ms=300.0),
            ValidatorDiagnostics(validator_name="opa", total_ms=80.0),
        ],
    )
    metrics_mod.record_scan_diagnostics(diag, status="ok")

    data = reader.get_metrics_data()
    assert data is not None
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}
    assert "apme.scan.duration" in names
    assert "apme.scan.phase.duration" in names
    assert "apme.validator.duration" in names
    assert "apme.scan.completed" in names

    bounds_by_name: dict[str, list[float]] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for point in metric.data.data_points:
                    if hasattr(point, "explicit_bounds"):
                        bounds_by_name[metric.name] = list(point.explicit_bounds)
    assert bounds_by_name["apme.scan.duration"] == list(SCAN_DURATION_BUCKETS_S)
    assert bounds_by_name["apme.validator.duration"] == list(VALIDATOR_DURATION_BUCKETS_S)
    provider.shutdown()


def test_validator_buckets_spread_subsecond_samples() -> None:
    """Sub-second validators must not all collapse into a single 5s bucket."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import VALIDATOR_DURATION_BUCKETS_S

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name="apme.validator.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VALIDATOR_DURATION_BUCKETS_S)),
            ),
        ],
    )
    hist = provider.get_meter("apme").create_histogram(
        "apme.validator.duration",
        unit="s",
        explicit_bucket_boundaries_advisory=list(VALIDATOR_DURATION_BUCKETS_S),
    )
    for value in (0.14, 0.30, 0.34, 1.67, 3.29, 8.53):
        hist.record(value)

    data = reader.get_metrics_data()
    point = next(
        p for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics for p in m.data.data_points
    )
    # More than one non-empty bucket → quantile math has resolution.
    assert sum(1 for c in point.bucket_counts if c) >= 4
    provider.shutdown()


def test_record_venv_acquire_with_inmemory_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Venv acquire metrics use tuned buckets and outcome labels.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import VENV_DURATION_BUCKETS_S

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name="apme.venv.acquire.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VENV_DURATION_BUCKETS_S)),
            ),
        ],
    )
    meter = provider.get_meter("apme", "0.1.0")
    monkeypatch.setattr(otel_setup, "_meter", meter)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    monkeypatch.setattr(metrics_mod, "_phase_duration", None)
    monkeypatch.setattr(metrics_mod, "_validator_duration", None)
    monkeypatch.setattr(metrics_mod, "_http_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_completed", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_duration", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_completed", None)
    monkeypatch.setattr(metrics_mod, "_scan_completed", None)

    metrics_mod.record_venv_acquire(
        0.02,
        outcome="warm",
        ansible_core_version="2.17",
        collections_requested=3,
    )
    metrics_mod.record_venv_acquire(
        12.5,
        outcome="create",
        ansible_core_version="2.17",
        collections_requested=3,
    )

    data = reader.get_metrics_data()
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}
    assert "apme.venv.acquire.duration" in names
    assert "apme.venv.acquire.completed" in names
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == "apme.venv.acquire.duration":
                    for point in metric.data.data_points:
                        assert list(point.explicit_bounds) == list(VENV_DURATION_BUCKETS_S)
    provider.shutdown()


def test_record_galaxy_fetch_with_inmemory_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Galaxy fetch metrics cover download and version_lookup operations.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import GALAXY_FETCH_DURATION_BUCKETS_S

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name="apme.galaxy.fetch.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(GALAXY_FETCH_DURATION_BUCKETS_S)),
            ),
        ],
    )
    meter = provider.get_meter("apme", "0.1.0")
    monkeypatch.setattr(otel_setup, "_meter", meter)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    monkeypatch.setattr(metrics_mod, "_phase_duration", None)
    monkeypatch.setattr(metrics_mod, "_validator_duration", None)
    monkeypatch.setattr(metrics_mod, "_http_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_completed", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_duration", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_completed", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_wheel_duration", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_wheel_completed", None)
    monkeypatch.setattr(metrics_mod, "_scan_completed", None)

    metrics_mod.record_galaxy_fetch(
        8.2,
        operation="download",
        status="ok",
        collections_requested=2,
    )
    metrics_mod.record_galaxy_fetch(
        0.35,
        operation="version_lookup",
        status="ok",
        server="galaxy.ansible.com",
    )
    metrics_mod.record_galaxy_fetch(
        15.0,
        operation="download",
        status="timeout",
        collections_requested=1,
    )

    data = reader.get_metrics_data()
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}
    assert "apme.galaxy.fetch.duration" in names
    assert "apme.galaxy.fetch.completed" in names

    ops: set[str] = set()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == "apme.galaxy.fetch.duration":
                    for point in metric.data.data_points:
                        assert list(point.explicit_bounds) == list(GALAXY_FETCH_DURATION_BUCKETS_S)
                        ops.add(point.attributes["operation"])
    assert ops == {"download", "version_lookup"}
    provider.shutdown()


def test_record_galaxy_wheel_serve_with_inmemory_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wheel serve metrics distinguish cache hit vs miss.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import GALAXY_FETCH_DURATION_BUCKETS_S

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name="apme.galaxy.wheel.serve.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(GALAXY_FETCH_DURATION_BUCKETS_S)),
            ),
        ],
    )
    meter = provider.get_meter("apme", "0.1.0")
    monkeypatch.setattr(otel_setup, "_meter", meter)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    monkeypatch.setattr(metrics_mod, "_scan_duration", None)
    monkeypatch.setattr(metrics_mod, "_phase_duration", None)
    monkeypatch.setattr(metrics_mod, "_validator_duration", None)
    monkeypatch.setattr(metrics_mod, "_http_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_duration", None)
    monkeypatch.setattr(metrics_mod, "_venv_completed", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_duration", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_fetch_completed", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_wheel_duration", None)
    monkeypatch.setattr(metrics_mod, "_galaxy_wheel_completed", None)
    monkeypatch.setattr(metrics_mod, "_scan_completed", None)

    metrics_mod.record_galaxy_wheel_serve(0.002, outcome="hit")
    metrics_mod.record_galaxy_wheel_serve(11.0, outcome="miss")
    metrics_mod.record_galaxy_wheel_serve(3.0, outcome="miss", status="error")

    data = reader.get_metrics_data()
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}
    assert "apme.galaxy.wheel.serve.duration" in names
    assert "apme.galaxy.wheel.serve.completed" in names

    outcomes: set[str] = set()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == "apme.galaxy.wheel.serve.duration":
                    for point in metric.data.data_points:
                        assert list(point.explicit_bounds) == list(GALAXY_FETCH_DURATION_BUCKETS_S)
                        outcomes.add(point.attributes["outcome"])
    assert outcomes == {"hit", "miss"}
    provider.shutdown()


def test_record_grpc_request_with_inmemory_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validator gRPC server metrics cover Validate/Health by service.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    from apme_engine.observability.buckets import VALIDATOR_DURATION_BUCKETS_S

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name="apme.grpc.server.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=list(VALIDATOR_DURATION_BUCKETS_S)),
            ),
        ],
    )
    meter = provider.get_meter("apme", "0.1.0")
    monkeypatch.setattr(otel_setup, "_meter", meter)
    monkeypatch.setattr(metrics_mod, "_instruments_ready", False)
    metrics_mod.reset_instruments()

    metrics_mod.record_grpc_request(0.12, method="Validate", status_code="OK", service="opa")
    metrics_mod.record_grpc_request(0.01, method="Health", status_code="OK", service="opa")
    metrics_mod.record_grpc_request(2.5, method="Validate", status_code="INTERNAL", service="native")

    data = reader.get_metrics_data()
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}
    assert "apme.grpc.server.duration" in names
    assert "apme.grpc.server.completed" in names

    methods: set[str] = set()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == "apme.grpc.server.duration":
                    for point in metric.data.data_points:
                        assert list(point.explicit_bounds) == list(VALIDATOR_DURATION_BUCKETS_S)
                        methods.add(point.attributes["rpc.method"])
    assert methods == {"Validate", "Health"}
    provider.shutdown()


def test_grpc_method_label_and_interceptor_wraps_unary() -> None:
    """Interceptor extracts method names and wraps unary-unary handlers."""
    import asyncio
    from types import SimpleNamespace

    import grpc

    from apme_engine.observability.grpc_middleware import GrpcMetricsInterceptor, _method_label

    assert _method_label("/apme.v1.Validator/Validate") == "Validate"
    assert _method_label("") == "unknown"

    async def _run() -> None:
        called = {"n": 0}

        async def original(request: object, context: object) -> str:
            called["n"] += 1
            return "ok"

        handler = grpc.unary_unary_rpc_method_handler(original)
        interceptor = GrpcMetricsInterceptor(service="opa")

        async def continuation(_details: object) -> object:
            return handler

        details = SimpleNamespace(method="/apme.v1.Validator/Validate")
        wrapped = await interceptor.intercept_service(continuation, details)  # type: ignore[arg-type]
        assert wrapped is not None
        assert wrapped.unary_unary is not None
        context = SimpleNamespace(code=lambda: None)
        result = await wrapped.unary_unary({"x": 1}, context)
        assert result == "ok"
        assert called["n"] == 1

    asyncio.run(_run())
