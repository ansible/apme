"""Push Galaxy server configs from the Gateway DB to the Galaxy Proxy (ADR-045).

The Galaxy Proxy runs ``ansible-galaxy`` for collection downloads and needs
to know which Galaxy/Automation Hub servers are configured.  Rather than
coupling the proxy to the gateway DB, the gateway pushes the current config
to the proxy's ``POST /admin/galaxy-config`` endpoint:

- On gateway startup (best-effort; proxy may not be ready yet)
- After every create / update / delete of a Galaxy server via the REST API

The push is fire-and-forget: failures are logged but never block the
gateway's own request path.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_PROXY_URL_ENV = "APME_GALAXY_PROXY_URL"
_PROXY_URL_DEFAULT = "http://127.0.0.1:8765"

_pending_push: asyncio.Task[None] | None = None


def _proxy_base_url() -> str:
    return os.environ.get(_PROXY_URL_ENV, "").strip() or _PROXY_URL_DEFAULT


async def push_galaxy_config() -> bool:
    """Load Galaxy servers from the DB and POST them to the proxy.

    Returns:
        bool: ``True`` on success, ``False`` on any failure (logged, never raised).

    Raises:
        asyncio.CancelledError: Re-raised if the task is cancelled.
    """
    import httpx  # noqa: PLC0415

    from apme_gateway.db import get_session  # noqa: PLC0415
    from apme_gateway.db import queries as q  # noqa: PLC0415

    try:
        async with get_session() as db:
            servers = await q.list_galaxy_servers(db)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Failed to load Galaxy servers from DB for proxy sync", exc_info=True)
        return False

    payload = {
        "servers": [
            {
                "name": s.name,
                "url": s.url,
                "token": s.token or "",
                "auth_url": s.auth_url or "",
            }
            for s in servers
        ],
    }

    url = _proxy_base_url().rstrip("/") + "/admin/galaxy-config"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info(
            "Pushed %d Galaxy server(s) to proxy at %s",
            len(servers),
            url,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Failed to push Galaxy config to proxy at %s", url, exc_info=True)
        return False


def schedule_push() -> None:
    """Schedule a background push of Galaxy configs to the proxy.

    Safe to call from any async context — the push runs as a fire-and-forget
    task that logs errors but never propagates them.  Consecutive calls are
    coalesced: if a push is already in flight the new request is skipped
    (the in-flight push will pick up the latest DB state anyway).
    """
    global _pending_push  # noqa: PLW0603

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running event loop; skipping Galaxy proxy sync")
        return

    if _pending_push is not None and not _pending_push.done():
        logger.debug("Galaxy proxy sync already in flight; skipping duplicate")
        return

    async def _bg_push() -> None:
        global _pending_push  # noqa: PLW0603
        try:
            await push_galaxy_config()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Background Galaxy proxy sync failed", exc_info=True)
        finally:
            _pending_push = None

    _pending_push = loop.create_task(_bg_push())
