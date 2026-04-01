"""Thin CLI for APME — presentation layer.

The CLI talks to the Primary service (gRPC) for engine operations and to
the Gateway (REST) for read-heavy queries on persisted data (e.g. SBOM).
It handles local file I/O (read files, chunk for streaming, write patched
bytes back) and output rendering.  All engine logic lives server-side.
"""

import sys

from apme_engine.cli.parser import build_parser


def main() -> None:
    """Entry point for ``apme``."""
    parser = build_parser()
    args = parser.parse_args()

    if args.no_ansi:
        from apme_engine.cli.ansi import force_no_color

        force_no_color()

    cmd = args.command

    if cmd == "daemon":
        from apme_engine.cli.daemon_cmd import run_daemon

        run_daemon(args)
        return

    if cmd == "check":
        from apme_engine.cli.check import run_check

        run_check(args)
    elif cmd == "format":
        from apme_engine.cli.format_cmd import run_format

        run_format(args)
    elif cmd == "remediate":
        from apme_engine.cli.remediate import run_remediate

        run_remediate(args)
    elif cmd == "health-check":
        from apme_engine.cli.health import run_health_check

        run_health_check(args)
    elif cmd == "sbom":
        from apme_engine.cli.sbom_cmd import run_sbom

        run_sbom(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
