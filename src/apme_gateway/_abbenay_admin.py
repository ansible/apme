"""Low-level Abbenay gRPC admin helpers for Gateway AI settings.

Uses ``ListEngines``, ``DiscoverModels``, ``ConfigureProvider``,
``RemoveProvider``, and ``UpdateConfig`` so Portal-managed providers are
applied to the running Abbenay daemon (consumed by Primary at job time).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import grpc.aio

logger = logging.getLogger(__name__)

_ADDR_ENV = "APME_ABBENAY_ADDR"
_DEFAULT_ADDR = "127.0.0.1:50057"


@dataclass(frozen=True)
class AbbenayEngineInfo:
    """Engine metadata returned by Abbenay ``ListEngines``.

    Attributes:
        id: Abbenay engine id.
        requires_key: Whether the engine needs an API key.
        default_base_url: Suggested API root.
        default_env_var: Legacy env-var name hint.
    """

    id: str
    requires_key: bool
    default_base_url: str
    default_env_var: str


@dataclass(frozen=True)
class AbbenayDiscoveredModel:
    """Model descriptor returned by Abbenay ``DiscoverModels``.

    Attributes:
        id: Engine model id.
        name: Human-readable model name.
        provider: Upstream provider label.
        engine: Abbenay engine id.
    """

    id: str
    name: str
    provider: str
    engine: str


def _resolve_addr() -> str:
    """Return the Abbenay gRPC listen address from env or the default.

    Returns:
        Host:port or ``unix://`` path for the Abbenay admin channel.
    """
    return os.environ.get(_ADDR_ENV, "").strip() or _DEFAULT_ADDR


def _grpc_target(addr: str) -> str:
    """Normalize an Abbenay address for ``grpc.aio.insecure_channel``.

    Args:
        addr: Host:port or ``unix://`` path.

    Returns:
        Channel target string.
    """
    if addr.startswith("unix://"):
        return addr
    return addr


class AbbenayAdminClient:
    """Async admin client over the Abbenay gRPC API."""

    def __init__(self, addr: str | None = None) -> None:
        """Create a client bound to ``addr`` or the process default.

        Args:
            addr: Optional Abbenay gRPC address override.
        """
        self._addr = addr or _resolve_addr()
        self._channel: grpc.aio.Channel | None = None
        self._stub: Any = None

    async def connect(self) -> None:
        """Open the insecure gRPC channel and stub."""
        from abbenay_grpc.abbenay.v1 import service_pb2_grpc  # noqa: PLC0415

        target = _grpc_target(self._addr)
        self._channel = grpc.aio.insecure_channel(target)
        self._stub = service_pb2_grpc.AbbenayStub(self._channel)

    async def close(self) -> None:
        """Close the gRPC channel if open."""
        if self._channel is not None:
            await self._channel.close(grace=None)
            self._channel = None
            self._stub = None

    async def __aenter__(self) -> AbbenayAdminClient:
        """Connect on context-manager enter.

        Returns:
            Connected admin client.
        """
        await self.connect()
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Close on context-manager exit.

        Args:
            *_args: Unused exception context from the context manager protocol.
        """
        await self.close()

    async def list_engines(self) -> list[AbbenayEngineInfo]:
        """List engines advertised by Abbenay.

        Returns:
            Engine descriptors from ``ListEngines``.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        resp = await self._stub.ListEngines(proto.ListEnginesRequest(), timeout=10)
        return [
            AbbenayEngineInfo(
                id=e.id,
                requires_key=bool(e.requires_key),
                default_base_url=e.default_base_url or "",
                default_env_var=e.default_env_var or "",
            )
            for e in resp.engines
        ]

    async def discover_models(
        self,
        *,
        engine_id: str,
        api_key: str = "",
        base_url: str = "",
    ) -> list[AbbenayDiscoveredModel]:
        """Discover models for an engine.

        Args:
            engine_id: Abbenay engine id.
            api_key: Optional API key for authenticated discovery.
            base_url: Optional custom API base URL.

        Returns:
            Discovered model descriptors.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        req = proto.DiscoverModelsRequest(engine_id=engine_id)
        if api_key:
            req.api_key = api_key
        if base_url:
            req.base_url = base_url
        resp = await self._stub.DiscoverModels(req, timeout=60)
        return [
            AbbenayDiscoveredModel(
                id=m.id or m.engine_model_id or "",
                name=m.name or m.id or "",
                provider=m.provider or engine_id,
                engine=m.engine or engine_id,
            )
            for m in resp.models
            if m.id or m.engine_model_id
        ]

    async def configure_provider(
        self,
        *,
        provider_id: str,
        engine: str,
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        """Configure provider credentials via Abbenay gRPC.

        Args:
            provider_id: Virtual provider name.
            engine: Abbenay engine id.
            api_key: Provider API key (may be empty).
            base_url: Optional custom API base URL.

        Raises:
            RuntimeError: When Abbenay reports configure failure.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        req = proto.ConfigureProviderRequest(
            provider_id=provider_id,
            engine=engine,
            api_key=api_key,
            base_url=base_url,
        )
        resp = await self._stub.ConfigureProvider(req, timeout=30)
        if not resp.success:
            msg = f"Abbenay ConfigureProvider failed for '{provider_id}'"
            raise RuntimeError(msg)

    async def remove_provider(self, provider_id: str) -> None:
        """Remove a virtual provider from Abbenay.

        Args:
            provider_id: Virtual provider name.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        await self._stub.RemoveProvider(
            proto.RemoveProviderRequest(provider_id=provider_id),
            timeout=30,
        )

    async def get_config(self) -> Any:
        """Fetch the live Abbenay configuration object.

        Returns:
            Abbenay ``GetConfig`` response payload.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        return await self._stub.GetConfig(proto.GetConfigRequest(), timeout=10)

    async def update_config(self, config: Any) -> None:
        """Replace the live Abbenay configuration.

        Args:
            config: Abbenay config object from ``get_config``.
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        await self._stub.UpdateConfig(
            proto.UpdateConfigRequest(config=config),
            timeout=30,
        )

    async def sync_provider_models(
        self,
        *,
        provider_id: str,
        engine: str,
        base_url: str,
        models: dict[str, dict[str, Any]],
        api_key: str = "",
    ) -> None:
        """Configure credentials and publish the model allow-list to Abbenay.

        Args:
            provider_id: Virtual provider name.
            engine: Abbenay engine id.
            base_url: Optional custom API base URL.
            models: Model id → params map.
            api_key: Provider API key (may be empty).
        """
        from abbenay_grpc.abbenay.v1 import service_pb2 as proto  # noqa: PLC0415

        await self.configure_provider(
            provider_id=provider_id,
            engine=engine,
            api_key=api_key,
            base_url=base_url,
        )

        config = await self.get_config()
        provider_cfg = proto.FullProviderConfig(
            engine=engine,
            base_url=base_url,
        )
        for model_id in models:
            provider_cfg.models[model_id].CopyFrom(proto.ModelParamConfig(model_id=model_id))

        config.providers[provider_id].CopyFrom(provider_cfg)
        await self.update_config(config)


async def open_abbenay_admin() -> AbbenayAdminClient | None:
    """Connect to Abbenay, returning None when the client library is missing.

    Returns:
        Connected client, or ``None`` when gRPC/client setup fails.
    """
    try:
        client = AbbenayAdminClient()
        await client.connect()
        return client
    except ImportError:
        logger.warning("abbenay_grpc is not installed — AI provider sync skipped")
        return None
    except Exception:
        logger.warning("Failed to connect to Abbenay at %s", _resolve_addr(), exc_info=True)
        return None


def models_json_to_dict(raw: str) -> dict[str, dict[str, Any]]:
    """Parse stored models JSON, returning an empty dict on invalid input.

    Args:
        raw: JSON string from the Gateway DB.

    Returns:
        Model id → params map.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v if isinstance(v, dict) else {} for k, v in parsed.items()}


def extra_json_to_dict(raw: str) -> dict[str, Any]:
    """Parse stored extra JSON, returning an empty dict on invalid input.

    Args:
        raw: JSON string from the Gateway DB.

    Returns:
        Extra metadata object.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
