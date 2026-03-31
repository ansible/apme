"""Parse Galaxy server configuration from ``ansible.cfg``.

Reads the ``[galaxy]`` → ``server_list`` directive and per-server
``[galaxy_server.<name>]`` sections to produce ``GalaxyServerDef`` proto
messages.  This lets the CLI send the user's existing Galaxy/Automation Hub
credentials as scan-scoped metadata (ADR-045) without reimplementing auth.

Resolution order (first wins):
1. ``--galaxy-server`` CLI flags  (future — not implemented yet)
2. ``ANSIBLE_CONFIG`` environment variable
3. ``ansible.cfg`` in the project root
4. ``~/.ansible.cfg``
"""

from __future__ import annotations

import configparser
import logging
import os
from pathlib import Path

from apme.v1.common_pb2 import GalaxyServerDef

logger = logging.getLogger(__name__)


def resolve_ansible_cfg(project_root: Path | None = None) -> Path | None:
    """Locate the effective ``ansible.cfg`` using Ansible's own precedence.

    Checks, in order:
    1. ``ANSIBLE_CONFIG`` env var (explicit override)
    2. ``ansible.cfg`` in *project_root* (project-local config)
    3. ``~/.ansible.cfg`` (user-level config)

    The ``/etc/ansible/ansible.cfg`` system default is intentionally
    excluded — it rarely contains Galaxy credentials and would add
    noise to scan metadata.

    Args:
        project_root: Project root directory (from ``discover_project_root``).

    Returns:
        Path to the ansible.cfg file, or ``None`` if none found.
    """
    env_cfg = os.environ.get("ANSIBLE_CONFIG")
    if env_cfg:
        p = Path(env_cfg).expanduser()
        if p.is_file():
            return p
        logger.debug("ANSIBLE_CONFIG=%s does not exist, skipping", env_cfg)

    if project_root is not None:
        local = project_root / "ansible.cfg"
        if local.is_file():
            return local

    home_cfg = Path.home() / ".ansible.cfg"
    if home_cfg.is_file():
        return home_cfg

    return None


def parse_galaxy_servers(cfg_path: Path) -> list[GalaxyServerDef]:
    """Parse Galaxy server definitions from an ``ansible.cfg`` file.

    Reads ``[galaxy]`` → ``server_list`` for the ordered list of server
    names, then reads each ``[galaxy_server.<name>]`` section for ``url``,
    ``token``, and ``auth_url``.

    Servers without a ``url`` are skipped with a warning.

    Args:
        cfg_path: Path to the ``ansible.cfg`` file.

    Returns:
        Ordered list of ``GalaxyServerDef`` proto messages.
    """
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(str(cfg_path), encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        logger.warning("Failed to parse %s: %s", cfg_path, exc)
        return []

    raw_list = cfg.get("galaxy", "server_list", fallback="")
    server_names = [n.strip() for n in raw_list.split(",") if n.strip()]
    if not server_names:
        return []

    servers: list[GalaxyServerDef] = []
    for name in server_names:
        section = f"galaxy_server.{name}"
        if not cfg.has_section(section):
            logger.warning(
                "ansible.cfg references server %r but [%s] section not found",
                name,
                section,
            )
            continue

        url = cfg.get(section, "url", fallback="").strip()
        if not url:
            logger.warning("Galaxy server %r has no url, skipping", name)
            continue

        token = cfg.get(section, "token", fallback="").strip()
        auth_url = cfg.get(section, "auth_url", fallback="").strip()

        servers.append(
            GalaxyServerDef(
                name=name,
                url=url,
                token=token,
                auth_url=auth_url,
            )
        )

    if servers:
        logger.debug(
            "Parsed %d Galaxy server(s) from %s: %s",
            len(servers),
            cfg_path,
            ", ".join(s.name for s in servers),
        )

    return servers


def discover_galaxy_servers(project_root: Path | None = None) -> list[GalaxyServerDef]:
    """Discover Galaxy servers from the user's ``ansible.cfg``.

    Convenience wrapper that resolves the config path and parses it.

    Args:
        project_root: Project root directory for local config lookup.

    Returns:
        Ordered list of ``GalaxyServerDef`` proto messages, or empty list
        if no config found or no servers defined.
    """
    cfg_path = resolve_ansible_cfg(project_root)
    if cfg_path is None:
        return []
    return parse_galaxy_servers(cfg_path)
