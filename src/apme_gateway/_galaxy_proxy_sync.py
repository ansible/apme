"""Push Galaxy server configs from the Gateway DB to the Galaxy Proxy (ADR-045).

The Galaxy Proxy runs ``ansible-galaxy`` for collection downloads and needs
to know which Galaxy/Automation Hub servers are configured.  Rather than
coupling the proxy to the gateway DB, the gateway pushes the current config
to the proxy's ``POST /admin/galaxy-config`` endpoint:

- On gateway startup (best-effort; proxy may not be ready yet)
- Every 15 seconds, including after a proxy-only restart or failed push
- After every create / update / delete of a Galaxy server via the REST API

The push is fire-and-forget: failures are logged but never block the
gateway's own request path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_PROXY_URL_ENV = "APME_GALAXY_PROXY_URL"
_PROXY_URL_DEFAULT = "http://127.0.0.1:8765"

_PROXY_ADMIN_TOKEN_ENV = "APME_PROXY_ADMIN_TOKEN"
# Must match _ADMIN_TOKEN_HEADER in galaxy_proxy/proxy/server.py — the two
# services deploy independently, so a one-side rename 403s config pushes.
_PROXY_ADMIN_TOKEN_HEADER = "x-apme-proxy-token"

_pending_push: asyncio.Task[None] | None = None


async def reconcile_galaxy_config(interval: float = 15.0) -> None:
    """Keep proxy configuration synchronized across independent restarts.

    Args:
        interval: Seconds between reconciliation attempts.
    """
    while True:
        schedule_push(periodic=True)
        await asyncio.sleep(interval)


async def stop_pending_push() -> None:
    """Cancel and await any config push during Gateway shutdown."""
    pending = _pending_push
    if pending is not None:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


# Last-push outcome for the /health freshness signal. Updated on every
# attempted push (success or failure) so a token-skewed 403 surfaces as a
# degraded Galaxy Proxy component instead of scans silently running stale.
_last_push_ok: bool | None = None
_last_push_at_mono: float | None = None
_last_push_error: str | None = None


def get_sync_status() -> dict[str, object]:
    """Return the last Galaxy-proxy config-push outcome with its age.

    Returns:
        Dict with ``attempted`` (any push tried yet), ``ok`` (last push
        succeeded; None when never attempted), ``age_s`` (seconds since
        the last attempt; None when never attempted), and ``error``
        (short failure description or None on success).
    """
    age: float | None = None
    if _last_push_at_mono is not None:
        age = max(0.0, time.monotonic() - _last_push_at_mono)
    return {
        "attempted": _last_push_at_mono is not None,
        "ok": _last_push_ok,
        "age_s": age,
        "error": _last_push_error,
    }


def _record_push_result(*, ok: bool, error: str | None) -> None:
    """Record a push outcome for the /health freshness signal.

    Args:
        ok: Whether the push succeeded.
        error: Short failure description (None on success).
    """
    global _last_push_ok, _last_push_at_mono, _last_push_error  # noqa: PLW0603
    _last_push_ok = ok
    _last_push_at_mono = time.monotonic()
    _last_push_error = error


def _proxy_base_url() -> str:
    return os.environ.get(_PROXY_URL_ENV, "").strip() or _PROXY_URL_DEFAULT


def _admin_token_value() -> str | None:
    """Return the configured proxy admin token, or signal unset/invalid.

    Returns:
        Stripped ASCII token, ``""`` when unset, or ``None`` when set but
        contains non-ASCII characters (misconfigured).
    """
    token = os.environ.get(_PROXY_ADMIN_TOKEN_ENV, "").strip()
    if not token:
        return ""
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        logger.error(
            "%s contains non-ASCII characters; proxy sync will not send admin auth",
            _PROXY_ADMIN_TOKEN_ENV,
        )
        return None
    return token


def _admin_token_headers() -> dict[str, str]:
    """Return the admin token header when ``APME_PROXY_ADMIN_TOKEN`` is set.

    Returns:
        Header dict with the proxy admin token, or empty when unset.
    """
    token = _admin_token_value()
    if token:
        return {_PROXY_ADMIN_TOKEN_HEADER: token}
    return {}


async def push_galaxy_config(*, periodic: bool = False) -> bool:
    """Load Galaxy servers from the DB and POST them to the proxy.

    Args:
        periodic: Routine reconciliation logs repeated outcomes at DEBUG.

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
    except Exception as exc:
        log = logger.debug if _last_push_ok is False else logger.warning
        log("Failed to load Galaxy servers from DB for proxy sync", exc_info=_last_push_ok is not False)
        _record_push_result(ok=False, error=f"db load failed: {type(exc).__name__}: {exc}"[:200])
        return False

    payload = {
        "servers": [
            {
                "name": s.name,
                "url": s.url,
                "token": s.token or "",
                "auth_url": s.auth_url or "",
                "validate_certs": s.validate_certs,
            }
            for s in servers
        ],
    }

    url = _proxy_base_url().rstrip("/") + "/admin/galaxy-config"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload, headers=_admin_token_headers())
            resp.raise_for_status()
        log = logger.debug if periodic and _last_push_ok is True else logger.info
        log(
            "Pushed %d Galaxy server(s) to proxy at %s",
            len(servers),
            url,
        )
        _record_push_result(ok=True, error=None)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log = logger.debug if _last_push_ok is False else logger.warning
        log("Failed to push Galaxy config to proxy at %s", url, exc_info=_last_push_ok is not False)
        _record_push_result(ok=False, error=f"push failed: {type(exc).__name__}: {exc}"[:200])
        return False


def schedule_push(*, periodic: bool = False) -> None:
    """Schedule a background push of Galaxy configs to the proxy.

    Safe to call from any async context — the push runs as a fire-and-forget
    task that logs errors but never propagates them.  Consecutive calls are
    coalesced: if a push is already in flight the new request is skipped
    (the in-flight push will pick up the latest DB state anyway).

    Args:
        periodic: Whether this push comes from routine reconciliation.
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
            await push_galaxy_config(periodic=periodic)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Background Galaxy proxy sync failed", exc_info=True)
        finally:
            _pending_push = None

    _pending_push = loop.create_task(_bg_push())
