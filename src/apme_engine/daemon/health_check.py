"""Health check for APME services: Primary, Native, OPA, Ansible (all gRPC)."""

import os
import time
from collections.abc import Callable
from typing import Protocol

import grpc

from apme.v1 import common_pb2, primary_pb2_grpc, validate_pb2_grpc


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


def _derive_addresses(primary_addr: str) -> dict[str, str]:
    """From primary host:port derive default addresses for all services.

    Args:
        primary_addr: Primary service address (host:port or host).

    Returns:
        Dict mapping service names to addresses (primary, native, opa, ansible).
    """
    if ":" in primary_addr:
        host, _ = primary_addr.rsplit(":", 1)
    else:
        host = primary_addr
    return {
        "primary": primary_addr,
        "native": f"{host}:50055",
        "opa": f"{host}:50054",
        "ansible": f"{host}:50053",
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
    try:
        channel = grpc.insecure_channel(addr)
        stub = stub_factory(channel)
        req = common_pb2.HealthRequest()
        resp = stub.Health(req, timeout=timeout)
        channel.close()
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": (getattr(resp, "status", "") or "").strip().lower() == "ok",
            "status": getattr(resp, "status", None) or "ok",
            "error": None,
            "latency_ms": round(elapsed_ms, 2),
        }
    except grpc.RpcError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": e.details() or str(e.code()),
            "latency_ms": round(elapsed_ms, 2),
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": False,
            "status": None,
            "error": str(e),
            "latency_ms": round(elapsed_ms, 2),
        }


def run_health_checks(
    primary_addr: str,
    native_addr: str | None = None,
    opa_addr: str | None = None,
    ansible_addr: str | None = None,
    timeout: float = 5.0,
) -> dict[str, dict[str, str | float | bool | None]]:
    """Run all health checks. Addresses not provided are derived from primary_addr.

    Args:
        primary_addr: Primary service address (required).
        native_addr: Native validator address (optional).
        opa_addr: OPA validator address (optional).
        ansible_addr: Ansible validator address (optional).
        timeout: RPC timeout in seconds.

    Returns:
        Dict mapping service names to check_grpc_health result dicts.
    """
    defaults = _derive_addresses(primary_addr)
    native_addr = native_addr or os.environ.get("NATIVE_GRPC_ADDRESS") or defaults["native"]
    opa_addr = opa_addr or os.environ.get("OPA_GRPC_ADDRESS") or defaults["opa"]
    ansible_addr = ansible_addr or os.environ.get("ANSIBLE_GRPC_ADDRESS") or defaults["ansible"]

    results = {}
    results["primary"] = check_grpc_health(primary_addr, primary_pb2_grpc.PrimaryStub, timeout)
    results["native"] = check_grpc_health(native_addr, validate_pb2_grpc.ValidatorStub, timeout)
    results["opa"] = check_grpc_health(opa_addr, validate_pb2_grpc.ValidatorStub, timeout)
    results["ansible"] = check_grpc_health(ansible_addr, validate_pb2_grpc.ValidatorStub, timeout)
    return results
