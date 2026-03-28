"""CLI entry point for galaxy-proxy (argparse, no external deps).

Environment-variable configuration
-----------------------------------
Servers can be defined via ``GALAXY_SERVER_LIST`` (ansible.cfg-style)::

    GALAXY_SERVER_LIST=certified,community

    GALAXY_SERVER_CERTIFIED_URL=https://console.redhat.com/api/automation-hub/
    GALAXY_SERVER_CERTIFIED_TOKEN=<offline-token>
    GALAXY_SERVER_CERTIFIED_AUTH_URL=https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
    GALAXY_SERVER_CERTIFIED_AUTH_TYPE=sso

    GALAXY_SERVER_COMMUNITY_URL=https://galaxy.ansible.com

Per-server variables: ``GALAXY_SERVER_<NAME>_URL``, ``_TOKEN``, ``_AUTH_URL``,
``_AUTH_TYPE`` (``token`` | ``bearer`` | ``sso``).

The legacy ``GALAXY_URL`` / ``GALAXY_TOKEN`` env vars still work as a
single-server fallback when ``GALAXY_SERVER_LIST`` is unset.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galaxy_proxy.galaxy_client import GalaxyServer

_VALID_AUTH_TYPES = {"token", "bearer", "sso"}

logger = logging.getLogger(__name__)


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _parse_galaxy_server(raw: str) -> GalaxyServer:
    """Parse a ``--galaxy-server`` value into a :class:`GalaxyServer`.

    Format: ``URL[,token=TOK][,name=LABEL][,auth_url=URL][,auth_type=TYPE]``

    Args:
        raw: Raw server string from CLI or env var.

    Returns:
        Parsed GalaxyServer instance.
    """
    from galaxy_proxy.galaxy_client import GalaxyServer

    parts = [p.strip() for p in raw.split(",")]
    url = parts[0]
    token: str | None = None
    name: str | None = None
    auth_url: str | None = None
    auth_type: str = "token"
    for part in parts[1:]:
        if part.startswith("token="):
            token = part[len("token=") :]
        elif part.startswith("name="):
            name = part[len("name=") :]
        elif part.startswith("auth_url="):
            auth_url = part[len("auth_url=") :]
        elif part.startswith("auth_type="):
            auth_type = part[len("auth_type=") :]

    auth_type = auth_type.lower().strip()
    if auth_type not in _VALID_AUTH_TYPES:
        sys.stderr.write(f"WARNING: auth_type={auth_type!r} in {raw!r} is invalid — defaulting to token\n")
        auth_type = "token"

    if auth_type == "sso" and not token:
        sys.stderr.write(f"WARNING: {raw!r} uses auth_type=sso but no token=... — SSO exchange will fail\n")

    return GalaxyServer(url=url, token=token, name=name, auth_url=auth_url, auth_type=auth_type)


def _servers_from_env() -> list[GalaxyServer] | None:
    """Build a server list from ``GALAXY_SERVER_LIST`` env vars.

    Returns:
        List of configured servers, or ``None`` if ``GALAXY_SERVER_LIST`` is
        not set.
    """
    server_list = os.environ.get("GALAXY_SERVER_LIST", "").strip()
    if not server_list:
        return None

    from galaxy_proxy.galaxy_client import GalaxyServer

    servers: list[GalaxyServer] = []
    for entry in server_list.split(","):
        name = entry.strip().upper()
        if not name:
            continue

        prefix = f"GALAXY_SERVER_{name}_"
        url = os.environ.get(f"{prefix}URL", "").strip()
        if not url:
            sys.stderr.write(
                f"WARNING: GALAXY_SERVER_LIST includes {entry.strip()!r} but {prefix}URL is not set — skipping\n"
            )
            continue

        if not url.startswith(("http://", "https://")):
            sys.stderr.write(f"WARNING: {prefix}URL={url!r} missing http(s):// — skipping\n")
            continue

        token = os.environ.get(f"{prefix}TOKEN") or None
        auth_url = os.environ.get(f"{prefix}AUTH_URL") or None
        auth_type = (os.environ.get(f"{prefix}AUTH_TYPE") or "token").lower().strip()

        if auth_type not in _VALID_AUTH_TYPES:
            sys.stderr.write(f"WARNING: {prefix}AUTH_TYPE={auth_type!r} invalid — defaulting to token\n")
            auth_type = "token"

        if auth_type == "sso" and not token:
            sys.stderr.write(f"WARNING: {prefix} uses auth_type=sso but no TOKEN — SSO exchange will fail\n")

        servers.append(
            GalaxyServer(
                url=url,
                token=token,
                name=entry.strip(),
                auth_url=auth_url,
                auth_type=auth_type,
            )
        )

    return servers or None


def main(argv: list[str] | None = None) -> None:
    """Galaxy proxy entry point.

    Args:
        argv: Arguments for argparse; ``None`` parses from :data:`sys.argv`.
    """
    parser = argparse.ArgumentParser(
        prog="galaxy-proxy",
        description="PEP 503 proxy: serve Galaxy collections as Python wheels.",
    )
    parser.add_argument("--port", "-p", type=int, default=8765, help="Port to bind to (default: 8765).")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0).")
    parser.add_argument(
        "--galaxy-url",
        default=os.environ.get("GALAXY_URL", "https://galaxy.ansible.com"),
        help="Default Galaxy server URL (env: GALAXY_URL).",
    )
    parser.add_argument(
        "--galaxy-token",
        default=os.environ.get("GALAXY_TOKEN"),
        help="Auth token (env: GALAXY_TOKEN).",
    )
    parser.add_argument(
        "--galaxy-server",
        dest="galaxy_servers",
        action="append",
        default=[],
        help=(
            "Upstream Galaxy server: URL[,token=TOK][,name=LABEL]"
            "[,auth_url=URL][,auth_type=token|bearer|sso]. Repeatable."
        ),
    )
    parser.add_argument("--pypi-url", default="https://pypi.org", help="Upstream PyPI URL for passthrough.")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Wheel cache directory.")
    parser.add_argument("--metadata-ttl", type=int, default=600, help="Metadata cache TTL in seconds.")
    parser.add_argument("--no-passthrough", action="store_true", help="Disable PyPI passthrough.")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity.")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    import uvicorn

    from galaxy_proxy.proxy.server import create_app

    parsed_servers: list[GalaxyServer] | None = None
    if args.galaxy_servers:
        parsed_servers = [_parse_galaxy_server(s) for s in args.galaxy_servers]
    else:
        parsed_servers = _servers_from_env()

    app = create_app(
        galaxy_url=args.galaxy_url,
        galaxy_token=args.galaxy_token,
        pypi_url=args.pypi_url,
        cache_dir=args.cache_dir,
        metadata_ttl=float(args.metadata_ttl),
        enable_passthrough=not args.no_passthrough,
        galaxy_servers=parsed_servers,
    )

    host, port = args.host, args.port
    sys.stderr.write(f"Starting Galaxy Proxy on {host}:{port}\n")

    if args.verbose:
        env_server_list = os.environ.get("GALAXY_SERVER_LIST", "")
        if env_server_list:
            sys.stderr.write(f"  GALAXY_SERVER_LIST={env_server_list}\n")
            for name in env_server_list.split(","):
                name = name.strip().upper()
                for suffix in ("URL", "TOKEN", "AUTH_URL", "AUTH_TYPE"):
                    key = f"GALAXY_SERVER_{name}_{suffix}"
                    val = os.environ.get(key, "")
                    if val:
                        display = "[REDACTED]" if suffix == "TOKEN" else val
                        sys.stderr.write(f"    {key}={display}\n")

    if parsed_servers:
        for i, srv in enumerate(parsed_servers, 1):
            auth_info = f" ({srv.auth_type})" if srv.token else ""
            sys.stderr.write(f"  Galaxy [{i}]: {srv.label()} -> {srv.url}{auth_info}\n")
    else:
        sys.stderr.write(f"Galaxy: {args.galaxy_url}\n")
    sys.stderr.write(f"PyPI passthrough: {'disabled' if args.no_passthrough else args.pypi_url}\n")
    sys.stderr.write(f"Cache: {args.cache_dir or '~/.cache/ansible-collection-proxy'}\n")
    sys.stderr.flush()

    uvicorn.run(app, host=host, port=port, log_level="info" if args.verbose else "warning")


if __name__ == "__main__":
    main()
