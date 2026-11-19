"""Submit idempotency-token rules shared by the CLI and the Gateway.

A submit token (``Idempotency-Key`` header, or ``submit_token`` in an
``/approve`` body) is what makes ``POST /operation/submit`` replayable: the
Gateway stores it with the resulting operation, so a repeat with the same
token returns the same operation instead of pushing a second branch.

The entropy floor exists because the token is user-supplied and names another
user's operation if guessed. Keeping the predicate here — rather than in the
Gateway only — lets ``apme submit --idempotency-key`` reject a weak token
before the request is sent, with the same rule the Gateway enforces, so the
two can never disagree. ``apme_engine`` is the dependency the Gateway already
imports; the reverse (CLI importing ``apme_gateway``) would drag the server
stack into engine-only installs.
"""

from __future__ import annotations

from string import hexdigits

SUBMIT_TOKEN_MAX_CHARS = 128
"""Longest accepted token, before and after trimming."""

SUBMIT_TOKEN_MIN_HEX_CHARS = 32
"""Entropy floor: 32 hex characters == 128 bits."""


def is_valid_submit_token(token: str) -> bool:
    """Return True when *token* meets the submit entropy/length rules.

    A token passes when neither its raw nor its trimmed form exceeds
    :data:`SUBMIT_TOKEN_MAX_CHARS` and its dash-stripped form is at least
    :data:`SUBMIT_TOKEN_MIN_HEX_CHARS` hexadecimal characters (dashes are
    allowed so UUID spellings work).

    Args:
        token: Candidate idempotency token.

    Returns:
        True when the token is acceptable as a submit idempotency key.
    """
    if len(token) > SUBMIT_TOKEN_MAX_CHARS:
        return False
    trimmed = token.strip()
    if len(trimmed) > SUBMIT_TOKEN_MAX_CHARS:
        return False
    stripped = trimmed.replace("-", "")
    if len(stripped) < SUBMIT_TOKEN_MIN_HEX_CHARS:
        return False
    return all(char in hexdigits for char in stripped)


def generate_submit_token() -> str:
    """Return a fresh 128-bit submit token.

    Returns:
        32-character lowercase hex string from a random UUID4.
    """
    from uuid import uuid4  # noqa: PLC0415 -- keep module import surface tiny

    return uuid4().hex


def canonicalize_submit_token(token: str) -> str:
    """Return the single canonical spelling used as a submit identity.

    Case and dash placement are presentation details: ``AABB-CCDD``,
    ``aabbccdd`` and ``aabb-ccdd`` all name the same 128-bit key. Every
    identity derived from a token — the in-process idempotency store key,
    the in-flight/scan lock keys, the durable ``Scan.submit_token`` claim,
    and the deterministic auto-branch digest — must use this one spelling.
    Otherwise two respellings of the same key get separate claims while
    collapsing onto the same auto-branch, so the second submit pushes into
    the first scan's branch and rewrites its pull request instead of
    colliding on the per-project token uniqueness constraint.

    Trimming and validation still happen on the raw value (see
    :func:`is_valid_submit_token`) so an over-limit token keeps its size and
    is rejected before it can be normalized into a different identity.

    Args:
        token: Validated submit token (any accepted case/dash spelling).

    Returns:
        Lowercase hex spelling with dashes removed.
    """
    return token.strip().lower().replace("-", "")
