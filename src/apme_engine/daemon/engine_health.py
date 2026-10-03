"""Validator health fan-out: per-service probes with bounded latency.

Extracted from :mod:`apme_engine.daemon.engine_server` so health probing
has a focused owner and test target. Behavior-preserving move: every probe
degrades to an entry instead of raising, and the engine's ``Health`` method
still applies the overall deadline plus the short recovery grace.
"""

from __future__ import annotations

import asyncio

import grpc.aio
import httpx

from apme.v1 import validate_pb2_grpc
from apme.v1.common_pb2 import HealthRequest, ServiceHealth

# Health fan-out bounds (PE-23): per-probe timeout plus a single overall
# deadline so one slow validator cannot stall the aggregate Health response.
# The overall deadline is deliberately larger than the per-probe timeout so it
# only fires when several probes hang past their own timeouts. Recovery after
# the deadline gets only a short grace for cancellation cleanup (not a second
# full deadline) so worst-case latency stays within k8s probe budgets.
_HEALTH_PER_PROBE_TIMEOUT_S = 5.0
_HEALTH_OVERALL_TIMEOUT_S = 12.0
_HEALTH_RECOVERY_GRACE_S = 2.0


async def _missing_validator_health(name: str, env_var: str, *, required: bool) -> tuple[bool, ServiceHealth | None]:
    """Report a validator with no configured address.

    Args:
        name: Validator name (key of ``VALIDATOR_ENV_VARS``).
        env_var: Environment variable holding its gRPC address.
        required: Whether the validator is required (unconfigured required
            validators are unhealthy; optional ones are skipped).

    Returns:
        Tuple of (unhealthy, entry or ``None`` when the optional validator
        is simply skipped).
    """
    if required:
        return (
            True,
            ServiceHealth(name=name, status=f"error: {env_var} not configured", address=""),
        )
    return (False, None)


async def _probe_validator_health(name: str, addr: str, *, required: bool) -> tuple[bool, ServiceHealth | None]:
    """Probe one validator with a per-probe timeout; degrade instead of raising.

    Args:
        name: Validator name (key of ``VALIDATOR_ENV_VARS``).
        addr: gRPC address of the validator.
        required: Whether the validator is required.

    Returns:
        Tuple of (unhealthy, ``ServiceHealth`` entry). Timeouts and errors
        are recorded as degraded entries, never raised.
    """
    try:
        channel = grpc.aio.insecure_channel(addr)
        try:
            stub = validate_pb2_grpc.ValidatorStub(channel)  # type: ignore[no-untyped-call]
            resp = await asyncio.wait_for(
                stub.Health(HealthRequest(), timeout=_HEALTH_PER_PROBE_TIMEOUT_S),
                timeout=_HEALTH_PER_PROBE_TIMEOUT_S,
            )
            status = resp.status
            return (
                required and status != "ok",
                ServiceHealth(name=name, status=status, address=addr),
            )
        finally:
            await channel.close(grace=None)
    except TimeoutError:
        return (
            required,
            ServiceHealth(name=name, status="error: health probe timed out", address=addr),
        )
    except Exception as e:  # noqa: BLE001 - health probe must degrade
        return (required, ServiceHealth(name=name, status=f"error: {e}", address=addr))


async def _probe_galaxy_proxy_health(proxy_url: str) -> tuple[bool, ServiceHealth | None]:
    """Probe Galaxy Proxy ``/health`` with a per-probe timeout.

    Args:
        proxy_url: Galaxy Proxy base URL (may be empty when unconfigured).

    Returns:
        Tuple of (unhealthy, ``ServiceHealth`` entry). Timeouts and errors
        are recorded as degraded entries, never raised.
    """
    if not proxy_url:
        return (
            True,
            ServiceHealth(
                name="galaxy_proxy",
                status="error: APME_GALAXY_PROXY_URL not configured",
                address="",
            ),
        )
    health_url = proxy_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_PER_PROBE_TIMEOUT_S) as client:
            http_resp = await asyncio.wait_for(
                client.get(health_url),
                timeout=_HEALTH_PER_PROBE_TIMEOUT_S,
            )
        proxy_ok = False
        detail = f"HTTP {http_resp.status_code}"
        if http_resp.status_code == 200:
            try:
                payload = http_resp.json()
            except ValueError:
                payload = None
            proxy_ok = isinstance(payload, dict) and payload.get("status") == "ok"
            if not proxy_ok:
                reported = payload.get("status") if isinstance(payload, dict) else None
                detail = f"status={reported!r}" if reported is not None else "invalid /health body"
        if proxy_ok:
            return (False, ServiceHealth(name="galaxy_proxy", status="ok", address=proxy_url))
        return (
            True,
            ServiceHealth(name="galaxy_proxy", status=f"error: {detail}", address=proxy_url),
        )
    except TimeoutError:
        return (
            True,
            ServiceHealth(name="galaxy_proxy", status="error: health probe timed out", address=proxy_url),
        )
    except httpx.TimeoutException:
        # httpx raises its own timeout hierarchy, not builtin TimeoutError:
        # report it under the same uniform message as validator probes.
        return (
            True,
            ServiceHealth(name="galaxy_proxy", status="error: health probe timed out", address=proxy_url),
        )
    except Exception as e:  # noqa: BLE001 - health probe must degrade
        return (True, ServiceHealth(name="galaxy_proxy", status=f"error: {e}", address=proxy_url))
