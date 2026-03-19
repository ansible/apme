"""Argument parsing for all CLI subcommands."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="APME: Ansible Policy & Modernization Engine",
    )
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument(
        "--na", "--no-ansi",
        action="store_true", default=False, dest="no_ansi",
        help="Disable ANSI color output",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── scan ──
    scan_p = subparsers.add_parser(
        "scan", parents=[global_opts],
        help="Scan a playbook, role, or project for policy violations",
    )
    scan_p.add_argument("target", nargs="?", default=".", help="Path to playbook, role, or project")
    scan_p.add_argument("--json", action="store_true", help="Output violations as JSON")
    scan_p.add_argument(
        "--ansible-version", default=None,
        help="ansible-core version for validation (e.g. 2.18, 2.20)",
    )
    scan_p.add_argument(
        "--collections", nargs="*", default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0)",
    )
    scan_p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="-v for summary, -vv for full per-rule breakdown",
    )

    # ── format ──
    fmt_p = subparsers.add_parser(
        "format", parents=[global_opts],
        help="Normalize YAML formatting (indentation, key order, jinja spacing)",
    )
    fmt_p.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    fmt_p.add_argument("--apply", action="store_true", help="Write formatted files in place")
    fmt_p.add_argument("--check", action="store_true", help="Exit 1 if files would change (CI mode)")
    fmt_p.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")

    # ── fix ──
    fix_p = subparsers.add_parser(
        "fix", parents=[global_opts],
        help="Format + scan + remediate: full fix pipeline",
    )
    fix_p.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    fix_p.add_argument("--apply", action="store_true", help="Write changes in place")
    fix_p.add_argument("--check", action="store_true", help="Exit 1 if changes would be made (CI mode)")
    fix_p.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")
    fix_p.add_argument("--max-passes", type=int, default=5, help="Max convergence passes (default: 5)")
    fix_p.add_argument(
        "--ansible-version", default=None,
        help="ansible-core version for validation (e.g. 2.18, 2.20)",
    )
    fix_p.add_argument(
        "--collections", nargs="*", default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0)",
    )
    fix_p.add_argument(
        "--auto-approve", action="store_true", default=False,
        help="Approve all AI proposals without prompting (CI mode)",
    )
    fix_p.add_argument(
        "--ai", action="store_true", default=False,
        help="Enable Tier 2 AI-assisted remediation",
    )
    fix_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output structured data payloads as JSON",
    )

    # ── cache ──
    cache_p = subparsers.add_parser(
        "cache", parents=[global_opts],
        help="Manage collection cache (Galaxy + GitHub)",
    )
    cache_sub = cache_p.add_subparsers(dest="cache_command", required=True)

    pg = cache_sub.add_parser("pull-galaxy", help="Install a Galaxy collection")
    pg.add_argument("spec", help="Collection spec: namespace.collection[:version]")
    pg.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    pr = cache_sub.add_parser("pull-requirements", help="Install collections from requirements.yml")
    pr.add_argument("requirements_path", help="Path to requirements.yml")
    pr.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    co = cache_sub.add_parser("clone-org", help="Clone GitHub org repos (Ansible collections)")
    co.add_argument("org", help="GitHub organization name")
    co.add_argument("--repos", nargs="*", default=None, help="Specific repo names (default: all)")
    co.add_argument("--depth", type=int, default=1, help="Git clone depth (default: 1)")
    co.add_argument("--token", default=None, help="GitHub token for API")

    # ── daemon ──
    daemon_p = subparsers.add_parser(
        "daemon", parents=[global_opts],
        help="Manage the local APME daemon (start/stop/status)",
    )
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)
    daemon_sub.add_parser("start", help="Start the local daemon")
    daemon_sub.add_parser("stop", help="Stop the local daemon")
    daemon_sub.add_parser("status", help="Show daemon status")

    # ── health-check ──
    health_p = subparsers.add_parser(
        "health-check", parents=[global_opts],
        help="Check health of the engine (Primary + all downstream services)",
    )
    health_p.add_argument("--timeout", type=float, default=5.0, help="Timeout per check (default: 5s)")
    health_p.add_argument("--json", action="store_true", help="Output as JSON")

    # ── session (stub) ──
    subparsers.add_parser(
        "session", parents=[global_opts],
        help="Manage named venv sessions (not yet available via gRPC)",
    )

    return parser
