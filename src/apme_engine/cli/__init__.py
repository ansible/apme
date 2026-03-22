"""Thin CLI for APME — pure gRPC presentation layer.

The CLI only talks to the Primary service.  It handles local file I/O
(read files, chunk for streaming, write patched bytes back) and output
rendering.  All engine logic lives server-side.
"""

import sys

from apme_engine.cli.parser import build_parser


def main() -> None:
    """Entry point for ``apme-scan``."""
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

    if cmd == "scan":
        from apme_engine.cli.scan import run_scan

        run_scan(args)
    elif cmd == "format":
        from apme_engine.cli.format_cmd import run_format

        run_format(args)
    elif cmd == "fix":
        from apme_engine.cli.fix import run_fix

        run_fix(args)
    elif cmd == "health-check":
        from apme_engine.cli.health import run_health_check

        run_health_check(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
