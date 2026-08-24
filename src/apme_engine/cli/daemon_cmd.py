"""Daemon subcommand: start/stop/status for local APME daemon."""

from __future__ import annotations

import argparse
import sys

from apme_engine.daemon.launcher import daemon_status, start_daemon, stop_daemon


def run_daemon(args: argparse.Namespace) -> None:
    """Execute the daemon subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    cmd = args.daemon_command

    if cmd == "start":
        try:
            state = daemon_status()
        except RuntimeError as e:
            sys.stderr.write(f"{e}\n")
            sys.exit(1)
        if state is not None:
            sys.stderr.write(f"Daemon already running (pid {state.pid}, engine {state.engine})\n")
            return
        try:
            state = start_daemon()
            sys.stderr.write(f"Daemon started (pid {state.pid}, engine {state.engine})\n")
        except RuntimeError as e:
            sys.stderr.write(f"Failed to start daemon: {e}\n")
            sys.exit(1)

    elif cmd == "stop":
        if stop_daemon():
            sys.stderr.write("Daemon stopped.\n")
        else:
            sys.stderr.write("No daemon running.\n")

    elif cmd == "status":
        try:
            state = daemon_status()
        except RuntimeError as e:
            sys.stderr.write(f"{e}\n")
            sys.exit(1)
        if state is None:
            sys.stderr.write("No daemon running.\n")
            sys.exit(1)
        sys.stdout.write(f"PID:      {state.pid}\n")
        sys.stdout.write(f"Engine:   {state.engine}\n")
        sys.stdout.write(f"Version:  {state.version}\n")
        sys.stdout.write(f"Started:  {state.started_at}\n")
        if state.services:
            sys.stdout.write("Services:\n")
            for name, addr in sorted(state.services.items()):
                sys.stdout.write(f"  {name:15s} {addr}\n")
