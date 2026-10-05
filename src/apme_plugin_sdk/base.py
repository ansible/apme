"""PluginBase: async gRPC Plugin server for third-party authors (ADR-042).

Depends on generated ``apme.v1`` stubs only — not ``apme_engine``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Sequence

import grpc
import grpc.aio

from apme.v1 import plugin_pb2_grpc
from apme.v1.common_pb2 import File, HealthRequest, HealthResponse, Violation
from apme.v1.plugin_pb2 import (
    DescribeRequest,
    DescribeResponse,
    TransformRequest,
    TransformResponse,
)
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse

logger = logging.getLogger("apme.plugin_sdk")

_DEFAULT_LISTEN = "0.0.0.0:50100"
_MAX_MSG = 50 * 1024 * 1024


class PluginBase:
    """Subclass and implement ``validate`` (and optionally ``transform``).

    Attributes:
        name: Plugin name used in ``EXT-<name>-`` rule IDs.
        version: Semver or calendar version string.
    """

    name: str = "plugin"
    version: str = "0.0.0"

    @property
    def rule_id_prefix(self) -> str:
        """Return ``EXT-<name>-``.

        Returns:
            Rule ID prefix including the trailing hyphen.
        """
        return f"EXT-{self.name}-"

    def prefixed_id(self, suffix: str) -> str:
        """Build a full rule ID from a numeric/token suffix.

        Args:
            suffix: Bare id (``001``) or already-prefixed id.

        Returns:
            ``EXT-<name>-<suffix>`` unless ``suffix`` already uses this
            plugin's prefix.
        """
        token = suffix.strip()
        own = self.rule_id_prefix
        if token.startswith(own):
            return token
        if token.startswith("EXT-"):
            token = token[len("EXT-") :]
        return f"{own}{token.lstrip('-')}"

    def violation(
        self,
        *,
        rule_id: str,
        message: str,
        file: str = "",
        line: int = 0,
        path: str = "",
        severity: str = "high",
        scope: str = "task",
        ai_guidance: str = "",
    ) -> dict[str, str | int]:
        """Build a violation dict for ``validate`` return values.

        Args:
            rule_id: Suffix or full ``EXT-`` id.
            message: Human-readable finding.
            file: Relative path.
            line: 1-based line number.
            path: Graph node path when known.
            severity: Label (high, medium, …).
            scope: ADR-026 scope (task, play, …).
            ai_guidance: Optional Phase 4 prompt hint (stored on metadata).

        Returns:
            Dict consumed by the SDK servicer.
        """
        out: dict[str, str | int] = {
            "rule_id": self.prefixed_id(rule_id),
            "message": message,
            "file": file,
            "line": line,
            "path": path,
            "severity": severity,
            "scope": scope,
        }
        if ai_guidance:
            out["ai_guidance"] = ai_guidance
        return out

    def health(self) -> str:
        """Return Health status (``ok`` or an error string).

        Override when a wrapped tool or bundle can be missing.

        Returns:
            Status string; Engine treats only exact ``ok`` as healthy.
        """
        return "ok"

    def transform_rule_ids(self) -> list[str]:
        """Return rule IDs this plugin can Transform.

        Returns:
            Prefixed rule IDs; empty means detection-only.
        """
        return []

    def validate(
        self,
        files: Sequence[tuple[str, bytes]],
        hierarchy: object,
    ) -> list[dict[str, str | int]]:
        """Detect violations.

        Args:
            files: ``(path, content)`` pairs from ``ValidateRequest.files``.
            hierarchy: Parsed ``hierarchy_payload`` JSON (list/dict) or ``None``.

        Returns:
            Violation dicts (use ``self.violation``).
        """
        return []

    def transform(
        self,
        file_path: str,
        content: bytes,
        violation: Violation,
        hierarchy: object = None,
    ) -> bytes | None:
        """Return replacement node YAML, or ``None`` if not applied.

        Args:
            file_path: Playbook path.
            content: Current node YAML bytes.
            violation: Proto finding to fix.
            hierarchy: Parsed ``hierarchy_payload`` JSON, or ``None``.

        Returns:
            New YAML bytes, or ``None``.
        """
        del hierarchy
        return None

    def describe(self) -> DescribeResponse:
        """Build the Describe payload.

        Returns:
            DescribeResponse with prefix and transform IDs.
        """
        return DescribeResponse(
            name=self.name,
            version=self.version,
            rule_id_prefix=self.rule_id_prefix,
            transform_rule_ids=self.transform_rule_ids(),
        )

    async def serve_async(self, listen: str | None = None) -> None:
        """Start the Plugin gRPC server until SIGINT/SIGTERM.

        Args:
            listen: Bind address; default ``APME_PLUGIN_LISTEN`` or ``0.0.0.0:50100``.
        """
        bind = listen or os.environ.get("APME_PLUGIN_LISTEN", _DEFAULT_LISTEN)
        server = grpc.aio.server(
            options=[
                ("grpc.max_receive_message_length", _MAX_MSG),
                ("grpc.max_send_message_length", _MAX_MSG),
            ],
        )
        plugin_pb2_grpc.add_PluginServicer_to_server(_PluginServicer(self), server)  # type: ignore[no-untyped-call]
        server.add_insecure_port(bind)
        await server.start()
        logger.info("Plugin %s listening on %s", self.name, bind)
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def _request_stop() -> None:
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with _suppress_not_implemented():
                loop.add_signal_handler(sig, _request_stop)
        await stop.wait()
        await server.stop(grace=5)

    @classmethod
    def serve(cls, listen: str | None = None) -> None:
        """Blocking entry point for ``if __name__ == '__main__'``.

        Args:
            listen: Optional bind address.
        """
        logging.basicConfig(level=logging.INFO)
        asyncio.run(cls().serve_async(listen))


class _suppress_not_implemented:
    """Ignore add_signal_handler failures on unsupported loops."""

    def __enter__(self) -> _suppress_not_implemented:
        """Enter the context.

        Returns:
            Self.
        """
        return self

    def __exit__(self, *args: object) -> bool:
        """Swallow ``NotImplementedError``.

        Args:
            *args: Exception info.

        Returns:
            True when the error was ``NotImplementedError``.
        """
        return bool(args) and isinstance(args[1], NotImplementedError)


class _PluginServicer(plugin_pb2_grpc.PluginServicer):
    """gRPC adapter around a ``PluginBase`` instance."""

    def __init__(self, plugin: PluginBase) -> None:
        """Bind the user plugin.

        Args:
            plugin: Author implementation.
        """
        self._plugin = plugin

    async def Health(
        self,
        request: HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Return the plugin Health status.

        Args:
            request: Unused.
            context: gRPC context.

        Returns:
            HealthResponse from ``PluginBase.health``.
        """
        try:
            status = (self._plugin.health() or "").strip()
        except Exception:  # noqa: BLE001 - plugin Health must always reply
            logger.exception("Plugin %s health() failed", self._plugin.name)
            status = "error: health check failed"
        return HealthResponse(status=status or "error: empty health status")

    async def Describe(
        self,
        request: DescribeRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> DescribeResponse:
        """Delegate to ``plugin.describe``.

        Args:
            request: Unused.
            context: gRPC context.

        Returns:
            DescribeResponse.
        """
        return self._plugin.describe()

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Run ``plugin.validate`` and map dicts to proto.

        Args:
            request: Engine ValidateRequest (``scandata`` ignored).
            context: gRPC context.

        Returns:
            ValidateResponse.

        Raises:
            Exception: After aborting the RPC when ``validate()`` fails.
        """
        files = [(str(f.path), bytes(f.content)) for f in request.files]  # type: ignore[attr-defined]
        hierarchy: object | None = None
        if request.hierarchy_payload:
            try:
                hierarchy = json.loads(request.hierarchy_payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Plugin %s: invalid hierarchy_payload", self._plugin.name)
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, self._plugin.validate, files, hierarchy)
        except Exception:
            logger.exception("Plugin %s validate failed", self._plugin.name)
            await context.abort(grpc.StatusCode.INTERNAL, "validate failed")
            raise
        violations: list[Violation] = []
        for item in raw:
            violations.append(_dict_to_violation(item, self._plugin.rule_id_prefix))
        return ValidateResponse(violations=violations, request_id=request.request_id)

    async def Transform(
        self,
        request: TransformRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> TransformResponse:
        """Run ``plugin.transform``.

        Args:
            request: Node YAML plus violation.
            context: gRPC context.

        Returns:
            TransformResponse.
        """
        hierarchy: object | None = None
        if request.hierarchy_payload:
            try:
                hierarchy = json.loads(request.hierarchy_payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Plugin %s: invalid transform hierarchy_payload", self._plugin.name)
        try:
            loop = asyncio.get_running_loop()
            new_bytes = await loop.run_in_executor(
                None,
                self._plugin.transform,
                request.file.path,
                bytes(request.file.content),
                request.violation,
                hierarchy,
            )
        except Exception as exc:  # noqa: BLE001 - plugin code
            logger.exception("Plugin %s transform failed", self._plugin.name)
            return TransformResponse(request_id=request.request_id, applied=False, error=str(exc))
        if new_bytes is None:
            return TransformResponse(request_id=request.request_id, applied=False)
        return TransformResponse(
            request_id=request.request_id,
            applied=True,
            file=File(path=request.file.path, content=new_bytes),
        )


_SEVERITY = {
    "info": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "error": 5,
    "critical": 6,
}

_SCOPE = {
    "task": 1,
    "block": 2,
    "play": 3,
    "playbook": 4,
    "role": 5,
    "inventory": 6,
    "collection": 7,
}


def _dict_to_violation(item: dict[str, str | int], prefix: str) -> Violation:
    """Convert a plugin dict to proto, enforcing EXT- prefix.

    Args:
        item: Violation dict from ``validate``.
        prefix: Required ``EXT-<name>-`` prefix.

    Returns:
        Proto Violation.
    """
    from apme.v1.common_pb2 import Violation as ViolationProto

    rule_id = str(item.get("rule_id") or "")
    if not rule_id.startswith(prefix):
        if rule_id.startswith("EXT-"):
            rule_id = rule_id[len("EXT-") :]
        rule_id = f"{prefix}{rule_id.lstrip('-')}"
    v = ViolationProto(
        rule_id=rule_id,
        message=str(item.get("message") or ""),
        file=str(item.get("file") or ""),
        path=str(item.get("path") or ""),
        severity=_SEVERITY.get(str(item.get("severity") or "high").lower(), 4),
        scope=_SCOPE.get(str(item.get("scope") or "task").lower(), 1),
    )
    line = item.get("line")
    if isinstance(line, int) and line > 0:
        v.line = line
    guidance = item.get("ai_guidance")
    if isinstance(guidance, str) and guidance:
        v.metadata["ai_guidance"] = guidance
    return v
