"""Credential redaction helpers for SCM operations."""

from __future__ import annotations

import re

_CRED_REDACT_RE = re.compile(r"(https?://)[^@]+@")


def redact_credentials(text: str) -> str:
    """Redact embedded credentials from URLs in text.

    Replaces ``https://user:token@host`` with ``https://[REDACTED]@host``
    to prevent token exposure in logs or error messages.

    Args:
        text: Text potentially containing URLs with credentials.

    Returns:
        Text with credentials redacted.
    """
    return _CRED_REDACT_RE.sub(r"\1[REDACTED]@", text)
