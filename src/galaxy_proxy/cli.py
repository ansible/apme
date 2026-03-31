"""CLI entry point for galaxy-proxy (argparse, no external deps)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galaxy_proxy.collection_downloader import GalaxyServerConfig


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


def _parse_galaxy_server(raw: str) -> GalaxyServerConfig:
    """Parse a ``--galaxy-server`` value into a :class:`GalaxyServerConfig`.

    Format: ``URL[,token=TOK][,name=LABEL][,auth_url=URL]``

    Args:
        raw: Raw server string from CLI or env var.

    Returns:
        Parsed GalaxyServerConfig instance.
    """
    from galaxy_proxy.collection_downloader import GalaxyServerConfig

    parts = [p.strip() for p in raw.split(",")]
    url = parts[0]
    token: str | None = None
    name: str | None = None
    auth_url: str | None = None
    for part in parts[1:]:
        if part.startswith("token="):
            token = part[len("token=") :]
        elif part.startswith("name="):
            name = part[len("name=") :]
        elif part.startswith("auth_url="):
            auth_url = part[len("auth_url=") :]

    label = name or url.split("//")[-1].split("/")[0]
    return GalaxyServerConfig(name=label, url=url, token=token, auth_url=auth_url)


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
        "--ansible-cfg",
        default=None,
        type=Path,
        help="Path to ansible.cfg for Galaxy server auth (env: ANSIBLE_CONFIG).",
    )
    parser.add_argument(
        "--galaxy-server",
        dest="galaxy_servers",
        action="append",
        default=None,
        help="Upstream Galaxy server: URL[,token=TOK][,name=LABEL][,auth_url=URL]. Repeatable.",
    )
    parser.add_argument(
        "--ansible-galaxy-bin",
        default=os.environ.get("ANSIBLE_GALAXY_BIN"),
        help="Path to ansible-galaxy binary (env: ANSIBLE_GALAXY_BIN).",
    )
    parser.add_argument("--pypi-url", default="https://pypi.org", help="Upstream PyPI URL for passthrough.")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Wheel cache directory.")
    parser.add_argument("--metadata-ttl", type=int, default=600, help="Metadata cache TTL in seconds.")
    parser.add_argument("--no-passthrough", action="store_true", help="Disable PyPI passthrough.")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity.")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.ansible_cfg and args.galaxy_servers:
        parser.error("--ansible-cfg and --galaxy-server are mutually exclusive")

    if args.ansible_cfg is None and not args.galaxy_servers:
        env_cfg = os.environ.get("ANSIBLE_CONFIG")
        if env_cfg:
            args.ansible_cfg = Path(env_cfg)

    import uvicorn

    from galaxy_proxy.proxy.server import create_app

    parsed_servers: list[GalaxyServerConfig] | None = None
    if args.galaxy_servers:
        parsed_servers = [_parse_galaxy_server(s) for s in args.galaxy_servers]

    app = create_app(
        pypi_url=args.pypi_url,
        cache_dir=args.cache_dir,
        metadata_ttl=float(args.metadata_ttl),
        enable_passthrough=not args.no_passthrough,
        ansible_cfg_path=args.ansible_cfg,
        galaxy_servers=parsed_servers,
        ansible_galaxy_bin=args.ansible_galaxy_bin,
    )

    host, port = args.host, args.port
    sys.stderr.write(f"Starting Galaxy Proxy on {host}:{port}\n")
    if parsed_servers:
        for i, srv in enumerate(parsed_servers, 1):
            auth = " (authenticated)" if srv.token else ""
            sys.stderr.write(f"  Galaxy [{i}]: {srv.name}{auth}\n")
    elif args.ansible_cfg:
        sys.stderr.write(f"Galaxy auth: {args.ansible_cfg}\n")
    else:
        sys.stderr.write("Galaxy auth: ansible-galaxy default config discovery\n")
    sys.stderr.write(f"PyPI passthrough: {'disabled' if args.no_passthrough else args.pypi_url}\n")
    sys.stderr.write(f"Cache: {args.cache_dir or '~/.cache/ansible-collection-proxy'}\n")
    sys.stderr.flush()

    uvicorn.run(app, host=host, port=port, log_level="info" if args.verbose else "warning")


if __name__ == "__main__":
    main()
