"""Backend discovery: find or auto-start the Engine gRPC service."""

import sys

import grpc

from apme_engine.cli._exit_codes import EXIT_ERROR
from apme_engine.daemon.launcher import ensure_daemon


def resolve_engine(args: object = None) -> tuple[grpc.Channel, str]:
    """Resolve the Engine address and return an open gRPC channel.

    Uses the three-tier discovery from ``ensure_daemon()``:
    1. ``APME_ENGINE_ADDRESS`` env var
    2. Running daemon (``~/.apme-data/daemon.json``)
    3. Auto-start daemon

    Args:
        args: CLI namespace (reserved for future use).

    Returns:
        Tuple of (gRPC channel, engine address string).
    """
    try:
        addr = ensure_daemon()
    except RuntimeError as e:
        sys.stderr.write(f"Failed to connect to APME engine: {e}\n")
        sys.stderr.write("Try: apme daemon start\n")
        sys.exit(EXIT_ERROR)
    _max_msg = 50 * 1024 * 1024
    return grpc.insecure_channel(
        addr,
        options=[
            ("grpc.max_send_message_length", _max_msg),
            ("grpc.max_receive_message_length", _max_msg),
        ],
    ), addr
