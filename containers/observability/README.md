# APME observability companion (Prometheus + Grafana)

Scrapes the in-pod OpenTelemetry Collector Prometheus exporter
(`localhost:8889`) and ships a pre-provisioned **APME Scan Times** dashboard.

## Prerequisites

1. APME pod running (`tox -e up`) with the `otel-collector` container.
2. Metrics visible: `curl -s http://localhost:8889/metrics | head`

## Start

```bash
./containers/observability/up.sh
```

| UI | URL |
|----|-----|
| Grafana | http://127.0.0.1:3002 (`admin` / `$APME_GRAFANA_ADMIN_PASSWORD`, default `apme-local`) |
| Prometheus | http://127.0.0.1:9091 |

Ports bind to loopback only. Anonymous Grafana access is disabled.

Prometheus TSDB persists under
`${XDG_CACHE_HOME:-$HOME/.cache}/apme/prometheus-tsdb` (override with
`APME_PROM_DATA`). Retention is 15 days. `down.sh` keeps the data;
`down.sh --wipe` deletes it.

Dashboard **mean** panels use instant `_sum/_count` so they stay populated
between scans. Percentile panels use `rate()` and may go blank until new
samples arrive.

## Stop

```bash
./containers/observability/down.sh          # keep history
./containers/observability/down.sh --wipe   # also delete TSDB
```

## Metrics (P0)

| OTel name | Prometheus (typical) | Source |
|-----------|----------------------|--------|
| `apme.scan.duration` | `apme_scan_duration_seconds` | Primary |
| `apme.scan.phase.duration` | `apme_scan_phase_duration_seconds` | Primary |
| `apme.validator.duration` | `apme_validator_duration_seconds` | Primary (from ADR-013) |
| `apme.scan.completed` | `apme_scan_completed_total` | Primary |
| `apme.http.server.duration` | `apme_http_server_duration_seconds` | Gateway, Galaxy Proxy |
| `apme.venv.acquire.duration` | `apme_venv_acquire_duration_seconds` | Primary (`outcome=warm`, `incremental`, or `create`) |
| `apme.venv.acquire.completed` | `apme_venv_acquire_completed_total` | Primary |
| `apme.galaxy.fetch.duration` | `apme_galaxy_fetch_duration_seconds` | Galaxy Proxy (`operation=download` or `version_lookup`) |
| `apme.galaxy.fetch.completed` | `apme_galaxy_fetch_completed_total` | Galaxy Proxy |
| `apme.galaxy.wheel.serve.duration` | `apme_galaxy_wheel_serve_duration_seconds` | Galaxy Proxy (`outcome=hit` or `miss`) |
| `apme.galaxy.wheel.serve.completed` | `apme_galaxy_wheel_serve_completed_total` | Galaxy Proxy |

Histogram boundaries are explicit (see `apme_engine/observability/buckets.py`):
HTTP uses OTel semantic-convention buckets; scan/validator/phase use
APME-tuned boundaries so `histogram_quantile` is meaningful. SDK defaults
(`0, 5, 10, …`) are intentionally not used.

Apps export OTLP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (pod.yaml sets
`http://127.0.0.1:4318`). Set `OTEL_SDK_DISABLED=true` to force off.

## Helm note

The Podman pod co-locates Gateway with the engine. Helm splits Gateway / Abbenay /
UI into separate Deployments, so an in-pod collector on the **engine** Deployment
only sees Primary + validators + galaxy-proxy. Gateway/UI need their own
collector sidecar (or a cluster Collector) when that topology is used.

