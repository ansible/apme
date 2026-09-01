"""Health check for APME services: Engine, Native, OPA, Ansible, Galaxy Proxy."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Protocol

import grpc
import httpx

from apme.v1 import common_pb2, engine_pb2_grpc, validate_pb2_grpc


def _http_health_body_ok(body: str) -> bool:
    """Return True when Galaxy Proxy health JSON reports ``{"status": "ok"}``.

    Matches ``EngineServicer.Health`` — exact JSON dict with case-sensitive
    ``status`` value ``ok``. Bare strings and substring matches are rejected.

    Args:
        body: Raw HTTP response body.

    Returns:
        Whether the body indicates a healthy service.
    """
    text = body.strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


class _HealthStub(Protocol):
    """Protocol for gRPC stubs that expose a Health RPC."""

    def Health(self, req: object, timeout: float = 5.0) -> object:
        """Call Health RPC.

        Args:
            req: Health request message.
            timeout: RPC timeout in seconds.

        Returns:
            Health response message.
        """
        ...


def _derive_addresses(engine_addr: str) -> dict[str, str]:
    """From engine host:port derive default addresses for all services.

    Args:
        engine_addr: Engine service address (host:port or host).

    Returns:
        Dict mapping service names to addresses (engine, native, opa, ansible,
        galaxy_proxy).
    """
    if ":" in engine_addr:
        host, _ = engine_addr.rsplit(":", 1)
    else:
        host = engine_addr
    return {
        "engine": engine_addr,
        "native": f"{host}:50055",
        "opa": f"{host}:50054",
        "ansible": f"{host}:50053",
        "galaxy_proxy": f"http://{host}:8765",
    }


def check_grpc_health(
    addr: str, stub_factory: Callable[[grpc.Channel], _HealthStub], timeout: float = 5.0
) -> dict[str, str | float | bool | None]:
    """Call Health RPC on a gRPC service; return {ok, status, error, latency_ms}.

    Args:
        addr: gRPC address (host:port).
        stub_factory: Callable that creates a Health stub from a channel.
        timeout: RPC timeout in seconds.

    Returns:
        Dict with ok, status, error, latency_ms keys.
    """
    start = time.perf_counter()
    channel = None
    try:
        channel = grpc.insecure_channel(addr)
        stub = stub_factory(channel)
        req = common_pb2.HealthRequest()
        resp = stub.Health(req, timeout=timeout)
    except grpc.RpcError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": e.details() or str(e.code()),
            "latency_ms": round(elapsed_ms, 2),
        }
    except Exception as e:  # noqa: BLE001 - health checks must degrade gracefully
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": str(e),
            "latency_ms": round(elapsed_ms, 2),
        }
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": (getattr(resp, "status", "") or "").strip().lower() == "ok",
            "status": getattr(resp, "status", None) or "ok",
            "error": None,
            "latency_ms": round(elapsed_ms, 2),
        }
    finally:
        if channel is not None:
            channel.close()


def check_http_health(base_url: str, timeout: float = 5.0) -> dict[str, str | float | bool | None]:
    """GET ``{base}/health`` on an HTTP service; return {ok, status, error, latency_ms}.

    Args:
        base_url: HTTP base URL (e.g. ``http://127.0.0.1:8765``).
        timeout: Request timeout in seconds.

    Returns:
        Dict with ok, status, error, latency_ms keys.
    """
    start = time.perf_counter()
    url = base_url.rstrip("/") + "/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            body = resp.text
            status_code = resp.status_code
    except httpx.HTTPError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": str(e),
            "latency_ms": round(elapsed_ms, 2),
        }
    except Exception as e:  # noqa: BLE001 - health checks must degrade gracefully
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": str(e),
            "latency_ms": round(elapsed_ms, 2),
        }
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000
        ok = status_code == 200 and _http_health_body_ok(body)
        return {
            "ok": ok,
            "status": "ok" if ok else f"HTTP {status_code}",
            "error": None if ok else f"unexpected health body: {body[:80]}",
            "latency_ms": round(elapsed_ms, 2),
        }


def run_health_checks(
    engine_addr: str,
    native_addr: str | None = None,
    opa_addr: str | None = None,
    ansible_addr: str | None = None,
    galaxy_proxy_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, dict[str, str | float | bool | None]]:
    """Run all health checks. Addresses not provided are derived from engine_addr.

    Args:
        engine_addr: Engine service address (required).
        native_addr: Native validator address (optional).
        opa_addr: OPA validator address (optional).
        ansible_addr: Ansible validator address (optional).
        galaxy_proxy_url: Galaxy Proxy base URL (optional).
        timeout: RPC/HTTP timeout in seconds.

    Returns:
        Dict mapping service names to health result dicts.
    """
    defaults = _derive_addresses(engine_addr)
    native_addr = native_addr or os.environ.get("NATIVE_GRPC_ADDRESS") or defaults["native"]
    opa_addr = opa_addr or os.environ.get("OPA_GRPC_ADDRESS") or defaults["opa"]
    ansible_addr = ansible_addr or os.environ.get("ANSIBLE_GRPC_ADDRESS") or defaults["ansible"]
    galaxy_proxy_url = galaxy_proxy_url or os.environ.get("APME_GALAXY_PROXY_URL") or defaults["galaxy_proxy"]

    results: dict[str, dict[str, str | float | bool | None]] = {}
    results["engine"] = check_grpc_health(engine_addr, engine_pb2_grpc.EngineStub, timeout)
    results["native"] = check_grpc_health(native_addr, validate_pb2_grpc.ValidatorStub, timeout)
    results["opa"] = check_grpc_health(opa_addr, validate_pb2_grpc.ValidatorStub, timeout)
    results["ansible"] = check_grpc_health(ansible_addr, validate_pb2_grpc.ValidatorStub, timeout)
    results["galaxy_proxy"] = check_http_health(galaxy_proxy_url, timeout)
    return results
