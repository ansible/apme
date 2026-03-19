"""Async gRPC client for the Primary orchestrator service.

The gateway never runs the engine directly — it always delegates to Primary
via gRPC.  This keeps the gateway's dependency surface small (only proto stubs,
no apme_engine).
"""

from __future__ import annotations

import os
import time
import uuid

import grpc
import grpc.aio

from apme.v1 import (
    cache_pb2_grpc,
    common_pb2,
    primary_pb2,
    primary_pb2_grpc,
    validate_pb2_grpc,
)

_PRIMARY_ADDR = os.getenv("APME_PRIMARY_ADDRESS", "127.0.0.1:50051")
_SCAN_TIMEOUT = int(os.getenv("APME_SCAN_TIMEOUT", "300"))
_CHUNK_SIZE = 50


class PrimaryClient:
    """Thin async wrapper around the Primary gRPC stub."""

    def __init__(self, address: str | None = None) -> None:
        self._address = address or _PRIMARY_ADDR
        self._channel: grpc.aio.Channel | None = None
        self._stub: primary_pb2_grpc.PrimaryStub | None = None

    async def connect(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._address)
        self._stub = primary_pb2_grpc.PrimaryStub(self._channel)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()

    @property
    def stub(self) -> primary_pb2_grpc.PrimaryStub:
        if self._stub is None:
            raise RuntimeError("PrimaryClient not connected — call connect() first")
        return self._stub

    async def scan(
        self,
        files: dict[str, str],
        project_name: str,
        *,
        ansible_core_version: str = "",
        collection_specs: list[str] | None = None,
    ) -> primary_pb2.ScanResponse:
        """Send files to Primary.ScanStream and return the response."""
        scan_id = str(uuid.uuid4())
        proto_files = [common_pb2.File(path=path, content=content.encode("utf-8")) for path, content in files.items()]

        async def _chunk_generator():
            for i in range(0, max(len(proto_files), 1), _CHUNK_SIZE):
                batch = proto_files[i : i + _CHUNK_SIZE]
                is_first = i == 0
                is_last = (i + _CHUNK_SIZE) >= len(proto_files)
                chunk = primary_pb2.ScanChunk(
                    scan_id=scan_id,
                    project_root=project_name,
                    files=batch,
                    last=is_last,
                )
                if is_first:
                    chunk.options.CopyFrom(
                        primary_pb2.ScanOptions(
                            ansible_core_version=ansible_core_version,
                            collection_specs=collection_specs or [],
                        )
                    )
                yield chunk

        return await self.stub.ScanStream(_chunk_generator(), timeout=_SCAN_TIMEOUT)

    async def format_files(self, files: dict[str, str]) -> primary_pb2.FormatResponse:
        """Send files to Primary.Format."""
        proto_files = [common_pb2.File(path=path, content=content.encode("utf-8")) for path, content in files.items()]
        request = primary_pb2.FormatRequest(files=proto_files)
        return await self.stub.Format(request, timeout=60)

    async def health(self) -> str:
        """Check Primary health.  Returns status string."""
        resp = await self.stub.Health(common_pb2.HealthRequest(), timeout=5)
        return resp.status


def _stub_for_service(name: str, channel: grpc.aio.Channel):
    """Return the correct gRPC stub for a given service name."""
    if name == "primary":
        return primary_pb2_grpc.PrimaryStub(channel)
    if name in ("native", "opa", "gitleaks", "ansible"):
        return validate_pb2_grpc.ValidatorStub(channel)
    if name == "cache_maintainer":
        return cache_pb2_grpc.CacheMaintainerStub(channel)
    return primary_pb2_grpc.PrimaryStub(channel)


class HealthProbe:
    """Probe multiple gRPC services for health status."""

    _SERVICES: list[tuple[str, str]] = [
        ("primary", os.getenv("APME_PRIMARY_ADDRESS", "127.0.0.1:50051")),
        ("native", os.getenv("NATIVE_GRPC_ADDRESS", "127.0.0.1:50055")),
        ("opa", os.getenv("OPA_GRPC_ADDRESS", "127.0.0.1:50054")),
        ("ansible", os.getenv("ANSIBLE_GRPC_ADDRESS", "127.0.0.1:50053")),
        ("gitleaks", os.getenv("GITLEAKS_GRPC_ADDRESS", "127.0.0.1:50056")),
        ("cache_maintainer", os.getenv("APME_CACHE_GRPC_ADDRESS", "127.0.0.1:50052")),
    ]

    @classmethod
    async def probe_all(cls) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for name, addr in cls._SERVICES:
            t0 = time.monotonic()
            try:
                async with grpc.aio.insecure_channel(addr) as ch:
                    stub = _stub_for_service(name, ch)
                    resp = await stub.Health(common_pb2.HealthRequest(), timeout=3)
                    latency = (time.monotonic() - t0) * 1000
                    results.append({"name": name, "status": resp.status, "latency_ms": round(latency, 1)})
            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000
                results.append({"name": name, "status": f"error: {exc}", "latency_ms": round(latency, 1)})
        return results
