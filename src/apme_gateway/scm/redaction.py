"""Credential redaction helpers for SCM operations."""

from __future__ import annotations

import re

_CRED_REDACT_RE = re.compile(r"(https?://)[^@]+@")
# Basic tokens redact from length 1 — the ``authorization: basic`` prefix is specific
# enough to avoid prose false positives. Bearer keeps a small floor so ``Bearer of``
# is not mangled while short access tokens still mask.
_BASIC_AUTH_HEADER_RE = re.compile(r"(?i)(authorization:\s*basic\s+)[A-Za-z0-9+/=]{1,}")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{4,}")


def redact_credentials(text: str) -> str:
    """Redact embedded credentials from URLs and auth headers in text.

    Replaces ``https://user:token@host`` with ``https://[REDACTED]@host``
    and masks ``Authorization: Basic <base64>`` header tokens (including
    ``AUTHORIZATION: Basic`` ``http.extraHeader`` values surfaced via
    ``GIT_TRACE`` / ``GIT_CURL_VERBOSE`` stderr) with
    ``Authorization: Basic [REDACTED]`` to prevent token exposure in logs
    or error messages. ``Bearer <token>`` values are masked the same way.
    Bare ``Basic`` prose without an ``Authorization:`` prefix is left
    untouched to avoid English false positives.

    Args:
        text: Text potentially containing URLs with credentials.

    Returns:
        Text with credentials redacted.
    """
    redacted = _CRED_REDACT_RE.sub(r"\1[REDACTED]@", text)
    redacted = _BASIC_AUTH_HEADER_RE.sub(r"\1[REDACTED]", redacted)
    return _BEARER_RE.sub(r"\1[REDACTED]", redacted)
