"""Argument parsing for all CLI subcommands."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description="APME: Ansible Policy & Modernization Engine",
    )
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument(
        "--na",
        "--no-ansi",
        action="store_true",
        default=False,
        dest="no_ansi",
        help="Disable ANSI color output",
    )
    global_opts.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v for summary + pipeline logs, -vv for full per-rule breakdown",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── check ──
    check_p = subparsers.add_parser(
        "check",
        parents=[global_opts],
        help="Check: format + remediate (dry-run) — shows what would change",
    )
    check_p.add_argument("target", nargs="?", default=".", help="Path to playbook, role, or project")
    check_p.add_argument("--diff", action="store_true", help="Show unified diffs of what remediate would change")
    check_p.add_argument("--json", action="store_true", help="Output violations as JSON (includes diffs)")
    check_p.add_argument(
        "--ansible-version",
        default=None,
        help="ansible-core version for validation (e.g. 2.18, 2.20)",
    )
    check_p.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0)",
    )
    check_p.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="gRPC timeout in seconds (default: 120)",
    )
    check_p.add_argument(
        "--session",
        default=None,
        help="Session ID for venv reuse; [A-Za-z0-9_-] only (default: hash of project root)",
    )

    # ── format ──
    fmt_p = subparsers.add_parser(
        "format",
        parents=[global_opts],
        help="Normalize YAML formatting (indentation, key order, jinja spacing)",
    )
    fmt_p.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    fmt_p.add_argument("--apply", action="store_true", help="Write formatted files in place")
    fmt_p.add_argument("--check", action="store_true", help="Exit 1 if files would change (CI mode)")
    fmt_p.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")
    fmt_p.add_argument(
        "--session",
        default=None,
        help="Session ID for venv reuse; [A-Za-z0-9_-] only (default: hash of project root)",
    )

    # ── remediate ──
    remediate_p = subparsers.add_parser(
        "remediate",
        parents=[global_opts],
        help="Remediate: format + auto-fix, writes changes to disk",
    )
    remediate_p.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    remediate_p.add_argument("--max-passes", type=int, default=5, help="Max convergence passes (default: 5)")
    remediate_p.add_argument(
        "--ansible-version",
        default=None,
        help="ansible-core version for validation (e.g. 2.18, 2.20)",
    )
    remediate_p.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0)",
    )
    remediate_p.add_argument(
        "--auto-approve",
        action="store_true",
        default=False,
        help="Approve all AI proposals without prompting (CI mode)",
    )
    remediate_p.add_argument(
        "--ai",
        action="store_true",
        default=False,
        help="Enable Tier 2 AI-assisted remediation",
    )
    remediate_p.add_argument(
        "--model",
        default=None,
        help=("AI model identifier (e.g. 'openai/gpt-4o'); falls back to APME_AI_MODEL env var"),
    )
    remediate_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured data payloads as JSON",
    )
    remediate_p.add_argument(
        "--session",
        default=None,
        help="Session ID for venv reuse; [A-Za-z0-9_-] only (default: hash of project root)",
    )

    # ── daemon ──
    daemon_p = subparsers.add_parser(
        "daemon",
        parents=[global_opts],
        help="Manage the local APME daemon (start/stop/status)",
    )
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)
    daemon_sub.add_parser("start", help="Start the local daemon")
    daemon_sub.add_parser("stop", help="Stop the local daemon")
    daemon_sub.add_parser("status", help="Show daemon status")

    # ── health-check ──
    health_p = subparsers.add_parser(
        "health-check",
        parents=[global_opts],
        help="Check health of the engine (Primary + all downstream services)",
    )
    health_p.add_argument("--timeout", type=float, default=5.0, help="Timeout per check (default: 5s)")
    health_p.add_argument("--json", action="store_true", help="Output as JSON")

    # ── sbom ──
    sbom_p = subparsers.add_parser(
        "sbom",
        parents=[global_opts],
        help="Generate SBOM for a project (via Gateway REST API)",
    )
    sbom_p.add_argument("project_id", help="Project identifier (UUID or name)")
    sbom_p.add_argument(
        "--format",
        default="cyclonedx",
        choices=["cyclonedx"],
        help="SBOM output format (default: cyclonedx)",
    )
    sbom_p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write SBOM to file instead of stdout",
    )
    sbom_p.add_argument(
        "--gateway-url",
        default=None,
        help="Gateway URL (default: $APME_GATEWAY_URL or http://localhost:8080)",
    )

    return parser
