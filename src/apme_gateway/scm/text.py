"""Shared text/blob helpers for SCM providers."""

from __future__ import annotations


def is_text_blob(data: bytes) -> bool:
    """Heuristic: treat content as text if it decodes as UTF-8 without errors.

    Args:
        data: Raw bytes to check.

    Returns:
        True if the data is valid UTF-8 text.
    """
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
