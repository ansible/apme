"""Histogram bucket boundaries for APME OTel duration metrics.

SDK defaults (0, 5, 10, …) dump most sub‑5s samples into one bucket and make
``histogram_quantile`` lie (e.g. OPA p95 ≈ 4.75s). Boundaries below are tuned
so typical APME latencies spread across mid buckets; HTTP follows the OTel
HTTP semantic-convention set.
"""

from __future__ import annotations

# https://opentelemetry.io/docs/specs/semconv/http/http-metrics/
# (http.server.request.duration explicit bucket boundaries)
HTTP_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
)

# End-to-end scans: warm ~5–15s, cold often 30–60s+
SCAN_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.5,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    20.0,
    30.0,
    45.0,
    60.0,
    90.0,
    120.0,
)

# Validator / phase: sub-second (native/opa/gitleaks) through tens of seconds
# (collection_health / ansible / fan_out)
VALIDATOR_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    25.0,
    45.0,
    60.0,
)

# Session venv acquire: warm hits are ms; cold create + collection install can be minutes
VENV_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
    45.0,
    60.0,
    90.0,
    120.0,
    180.0,
)

# Outbound Galaxy: version lookup is sub-second–few seconds; tarball download can be minutes
GALAXY_FETCH_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    180.0,
    300.0,
)
