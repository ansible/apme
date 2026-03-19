"""Daemon subcommand: start/stop/status for local APME daemon."""

from __future__ import annotations

import argparse
import sys

from apme_engine.daemon.launcher import daemon_status, start_daemon, stop_daemon


def run_daemon(args: argparse.Namespace) -> None:
    cmd = args.daemon_command

    if cmd == "start":
        state = daemon_status()
        if state is not None:
            sys.stderr.write(
                f"Daemon already running (pid {state.pid}, primary {state.primary})\n"
            )
            return
        try:
            state = start_daemon()
            sys.stderr.write(
                f"Daemon started (pid {state.pid}, primary {state.primary})\n"
            )
        except RuntimeError as e:
            sys.stderr.write(f"Failed to start daemon: {e}\n")
            sys.exit(1)

    elif cmd == "stop":
        if stop_daemon():
            sys.stderr.write("Daemon stopped.\n")
        else:
            sys.stderr.write("No daemon running.\n")

    elif cmd == "status":
        state = daemon_status()
        if state is None:
            sys.stderr.write("No daemon running.\n")
            sys.exit(1)
        sys.stdout.write(f"PID:      {state.pid}\n")
        sys.stdout.write(f"Primary:  {state.primary}\n")
        sys.stdout.write(f"Version:  {state.version}\n")
        sys.stdout.write(f"Started:  {state.started_at}\n")
        if state.services:
            sys.stdout.write("Services:\n")
            for name, addr in sorted(state.services.items()):
                sys.stdout.write(f"  {name:15s} {addr}\n")
