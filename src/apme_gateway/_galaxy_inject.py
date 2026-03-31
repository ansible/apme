"""Load global Galaxy server definitions and convert to gRPC proto messages (ADR-045).

Gateway-initiated scans (project operations and playground sessions) inject
globally configured Galaxy servers into the gRPC ``ScanOptions`` and ``FixOptions``
so that the engine can write a session-scoped ``ansible.cfg`` for
``ansible-galaxy`` authentication.
"""

from __future__ import annotations

import asyncio
import logging

from apme.v1.common_pb2 import GalaxyServerDef
from apme_gateway.db import get_session
from apme_gateway.db import queries as q

logger = logging.getLogger(__name__)


async def load_galaxy_server_defs() -> list[GalaxyServerDef]:
    """Fetch global Galaxy servers from the DB and return proto messages.

    Returns an empty list (with a warning) if the database is unavailable,
    so callers never block on a DB failure.  ``CancelledError`` is
    re-raised so task cancellation propagates correctly.

    Returns:
        List of GalaxyServerDef protobuf messages.

    Raises:
        asyncio.CancelledError: Re-raised to preserve task cancellation.
    """
    try:
        async with get_session() as db:
            servers = await q.list_galaxy_servers(db)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Failed to load Galaxy servers from DB; proceeding without", exc_info=True)
        return []

    defs: list[GalaxyServerDef] = []
    for s in servers:
        defs.append(
            GalaxyServerDef(
                name=s.name,
                url=s.url,
                token=s.token,
                auth_url=s.auth_url,
            )
        )
    return defs
