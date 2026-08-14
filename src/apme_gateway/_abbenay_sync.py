"""Push AI provider configs from the Gateway DB to Abbenay.

Durable SoT is the Gateway ``ai_providers`` table. Abbenay receives a
just-in-time push into its process-lifetime ``memory`` secret store
(DR-047) — used before AI-enabled scans and after settings CRUD.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from apme_gateway.api.abbenay_proxy import abbenay_http_base_url, abbenay_http_token

logger = logging.getLogger(__name__)

_pending_sync: asyncio.Task[None] | None = None
_PROXY_TIMEOUT_S = 60.0


def _secret_name_for(provider_name: str) -> str:
    """Derive the Abbenay memory secret name for a provider.

    Args:
        provider_name: Virtual Abbenay provider name (slug).

    Returns:
        Upper-snake secret name used with ``secretStore=memory``.
    """
    return f"{provider_name.upper().replace('-', '_')}_API_KEY"


async def _configure_provider_http(
    client: httpx.AsyncClient,
    *,
    provider_id: str,
    engine: str,
    base_url: str,
    api_key: str,
    models: dict[str, dict[str, object]],
) -> None:
    """Push one provider+cred into Abbenay memory and publish models.

    Args:
        client: Shared HTTP client.
        provider_id: Abbenay virtual provider name.
        engine: Abbenay engine id.
        base_url: Optional custom API base URL.
        api_key: Provider API key (may be empty for keyless engines).
        models: Model id → params map to publish into Abbenay config.

    Raises:
        RuntimeError: When the Abbenay token is missing or HTTP calls fail.
    """
    base = abbenay_http_base_url()
    token = abbenay_http_token()
    if not token:
        msg = "Abbenay HTTP admin token not configured"
        raise RuntimeError(msg)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    secret_name = _secret_name_for(provider_id)
    configure_body: dict[str, object] = {
        "engine": engine,
        "secretName": secret_name,
        "secretStore": "memory",
    }
    if api_key:
        configure_body["apiKey"] = api_key
    if base_url:
        configure_body["baseUrl"] = base_url

    resp = await client.post(
        f"{base}/api/provider/{provider_id}/configure",
        headers=headers,
        json=configure_body,
    )
    if resp.status_code >= 400:
        msg = f"Abbenay configure {provider_id} failed: {resp.status_code} {resp.text[:200]}"
        raise RuntimeError(msg)

    # Merge models into persisted config (secret_* already set by configure).
    cfg_resp = await client.get(f"{base}/api/config", headers=headers)
    if cfg_resp.status_code >= 400:
        msg = f"Abbenay get config failed: {cfg_resp.status_code}"
        raise RuntimeError(msg)
    raw = cfg_resp.json()
    config = raw.get("config") if isinstance(raw, dict) and "config" in raw else raw
    if not isinstance(config, dict):
        config = {}
    providers = dict(config.get("providers") or {})
    prev = dict(providers.get(provider_id) or {})
    prev["engine"] = engine
    prev["secret_name"] = secret_name
    prev["secret_store"] = "memory"
    if base_url:
        prev["base_url"] = base_url
    prev["models"] = {mid: (opts if isinstance(opts, dict) else {}) for mid, opts in models.items()}
    providers[provider_id] = prev
    put = await client.post(
        f"{base}/api/config",
        headers=headers,
        json={"location": "user", "config": {**config, "providers": providers}},
    )
    if put.status_code >= 400:
        msg = f"Abbenay update config failed: {put.status_code} {put.text[:200]}"
        raise RuntimeError(msg)


async def push_ai_providers() -> bool:
    """Load AI providers from the DB and reconcile them with Abbenay memory.

    Returns:
        bool: ``True`` on success, ``False`` on any failure (logged, never raised).

    Raises:
        asyncio.CancelledError: Re-raised if the task is cancelled.
    """
    from apme_gateway._abbenay_admin import (  # noqa: PLC0415
        models_json_to_dict,
        open_abbenay_admin,
    )
    from apme_gateway.db import get_session  # noqa: PLC0415
    from apme_gateway.db import queries as q  # noqa: PLC0415

    try:
        async with get_session() as db:
            providers = await q.list_ai_providers(db)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Failed to load AI providers from DB for Abbenay sync", exc_info=True)
        return False

    if not abbenay_http_token():
        logger.warning("Abbenay HTTP token unset — AI provider push skipped")
        return False

    desired_names = {p.name for p in providers}

    # Prune removed providers via gRPC when available.
    client = await open_abbenay_admin()
    if client is not None:
        try:
            existing = await client.get_config()
            for provider_id in list(existing.providers.keys()):
                if provider_id not in desired_names:
                    await client.remove_provider(provider_id)
        except Exception:
            logger.debug("Could not prune removed Abbenay providers", exc_info=True)
        finally:
            await client.close()

    try:
        async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT_S) as http:
            for provider in providers:
                models = models_json_to_dict(provider.models_json)
                await _configure_provider_http(
                    http,
                    provider_id=provider.name,
                    engine=provider.engine,
                    base_url=provider.base_url or "",
                    api_key=provider.api_key or "",
                    models=models,
                )
        logger.info("Synced %d AI provider(s) to Abbenay (memory)", len(providers))
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Failed to push AI providers to Abbenay", exc_info=True)
        return False


def schedule_sync() -> None:
    """Schedule a background Abbenay sync (fire-and-forget, coalesced)."""
    global _pending_sync  # noqa: PLW0603

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running event loop; skipping Abbenay sync")
        return

    if _pending_sync is not None and not _pending_sync.done():
        logger.debug("Abbenay sync already in flight; skipping duplicate")
        return

    async def _bg_sync() -> None:
        global _pending_sync  # noqa: PLW0603
        try:
            await push_ai_providers()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Background Abbenay sync failed", exc_info=True)
        finally:
            _pending_sync = None

    _pending_sync = loop.create_task(_bg_sync())
