"""Submit idempotency store, locks, and binding helpers (extracted from operation_router).

Single-shape API (#14): the four helpers accept only
:class:`SubmitIdempotencyKey` / :class:`SubmitBinding` dataclasses —
no ``(project_id, token)`` or keyword-triple legacy forms.

- #27 data clumps: :class:`SubmitIdempotencyKey` (project_id, token) and
  :class:`SubmitBinding` (branch/activity/scan + optional patch_hash).
- #26 single TTL semantic: :func:`_is_live_entry` plus
  :func:`_find_conflicting_entry` replacing the O(n) inline scan.
- #40 constructor: callers build :class:`SubmitIdempotencyKey`
  directly (store access uses ``.as_tuple()`` for tuple keys).
- #2 BoundedCache: idempotency store, in-flight locks, and scan locks are
  :class:`apme_engine.cache.BoundedCache` instances. TTL-on-write matches
  the entry TTL (same duration); ``_is_live_entry`` (entry.created_at) is
  the authoritative single semantic and the cache TTL is a second layer.
- #13 token entropy: >= 32 hex chars (128-bit); single-use-per-operation
  guidance in docstrings.
- #28 patch binding: ``patch_hash`` field + 409 on mismatch when both
  stored and incoming hashes are present. Callers compute the incoming
  hash via :func:`_compute_patch_hash` before replay checks; a fast-path
  check without a hash replays only when hashes are missing
  (single-use-per-operation guidance applies until the hash flows).
- #14 cross-token scan mutex + #29 timeouts: per-scan locks plus
  :func:`_acquire_lock_with_timeout` (30s -> 503 + Retry-After).
  Lock order is always token-lock (outer, wrapper) then scan-lock
  (inner, impl); documented here and at call sites.

Gateway depends on engine (not vice versa): importing BoundedCache here
is allowed (ADR-020/029 direction).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from string import hexdigits
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from apme_engine.cache import BoundedCache

if TYPE_CHECKING:
    from apme_gateway.api.schemas import SubmitResponse

logger = logging.getLogger(__name__)


def _coded_detail(code: str, message: str) -> dict[str, str]:
    """Format a machine-readable error code with a human message as a dict.

    ADR-060 is additive-only: coded 409s keep the ``{"code", "message"}``
    dict shape so clients can branch on ``detail.code`` without string
    parsing. FastAPI renders this as ``{"detail": {"code": ..., ...}}``.

    Args:
        code: Machine-readable error code (e.g. ``working_set_in_progress``).
        message: Human-readable message.

    Returns:
        Dict with ``code`` and ``message`` keys.
    """
    return {"code": code, "message": message}


_SUBMIT_IDEMPOTENCY_MAX = 1000
_SUBMIT_IDEMPOTENCY_TTL_S = 24 * 3600
_SUBMIT_INFLIGHT_MAX = 1000
_SCAN_LOCKS_MAX = 1000
# #29: bound same-token / same-scan waits so a hung submit fails fast.
_SUBMIT_LOCK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class SubmitIdempotencyKey:
    """Scope an idempotency token to its project (#27 data clump).

    Attributes:
        project_id: Owning project UUID.
        token: Validated idempotency token (>= 32 hex chars, single-use
            per operation — generate a fresh token per submit operation).
    """

    project_id: str
    token: str

    def as_tuple(self) -> tuple[str, str]:
        """Return the legacy ``(project_id, token)`` store key.

        Returns:
            Tuple key for backward-compat store access.
        """
        return (self.project_id, self.token)


@dataclass(frozen=True)
class SubmitBinding:
    """Request binding a token reuse must match (#27 data clump).

    Attributes:
        branch_name: Requested branch name (None when auto).
        activity_id: Requested historical activity id (None for live op).
        scan_id: Resolved scan id for this call (informational for
            atomic retries — scan mismatch alone never 409s).
        patch_hash: Optional hex hash of the patch set (#28). When both
            stored and incoming hashes are present and differ, replay
            409s. None skips the patch check (single-use-per-operation
            guidance applies until callers pass a hash).
    """

    branch_name: str | None
    activity_id: str | None
    scan_id: str
    patch_hash: str | None = None


@dataclass
class _SubmitIdempotencyEntry:
    """One cached submit result with request binding for replay validation.

    Attributes:
        response: Stored submit response replayed on token reuse.
        branch_name: Branch the token was first used with (conflict check).
        activity_id: Activity the token was first used with, if any.
        scan_id: Scan the token was first used with, if any.
        patch_hash: Optional hash of the patch set at submit time (#28).
        created_at: Monotonic timestamp for TTL eviction (authoritative
            for :func:`_is_live_entry`; mirrors BoundedCache TTL-on-write).
    """

    response: SubmitResponse
    branch_name: str | None = None
    activity_id: str | None = None
    scan_id: str | None = None
    patch_hash: str | None = None
    created_at: float = field(default_factory=time.monotonic)


# Idempotency store for POST /submit (N19): (project_id, token) -> entry.
# BoundedCache (LRU + TTL-on-write, same 24h duration as entry TTL).
# TTL-on-write matches entry semantics; _is_live_entry is authoritative.
_SUBMIT_IDEMPOTENCY_STORE: BoundedCache[tuple[str, str], _SubmitIdempotencyEntry] = BoundedCache(
    maxsize=_SUBMIT_IDEMPOTENCY_MAX,
    ttl=float(_SUBMIT_IDEMPOTENCY_TTL_S),
)
_SUBMIT_IDEMPOTENCY_LOCK = asyncio.Lock()


def _is_submit_lock_idle(lock: object) -> bool:
    """Return True when a submit lock has no holders or waiters.

    Mirrors the proxy waiter-aware pattern: a lock that is locked or has
    live waiters still serializes an in-flight submit — evicting it would
    hand a newcomer a fresh mutex and break mutual exclusion (duplicate
    push/PR). Unknown shapes fail closed (map grows over cap).

    Args:
        lock: Per-token or per-scan asyncio lock (or test double).

    Returns:
        True when the lock is provably idle.
    """
    locked = getattr(lock, "locked", None)
    if not callable(locked):
        return False
    try:
        if locked():
            return False
    except Exception:  # noqa: BLE001 — fail closed on introspection errors
        return False
    waiters = getattr(lock, "_waiters", None)
    if waiters:
        try:
            live = [w for w in waiters if not w.cancelled()]
        except Exception:  # noqa: BLE001 — fail closed
            return False
        if live:
            return False
    return True


# Per-(project_id, token) in-flight locks. BoundedCache with idle-aware
# eviction: a locked or waited-on entry is never evicted so a holder's
# mutex cannot be dropped mid-submit; over-cap inserts when all busy
# (never drop live state).
_SUBMIT_INFLIGHT_LOCKS: BoundedCache[tuple[str, str], asyncio.Lock] = BoundedCache(
    maxsize=_SUBMIT_INFLIGHT_MAX,
    is_idle=_is_submit_lock_idle,
)

# Cross-token per-scan mutexes (#14): (project_id, scan_id) -> lock.
# Serializes same-scan different-token submits so duplicates await the
# winner instead of pushing/opening duplicate PRs. BoundedCache with
# idle-aware eviction (same policy as inflight locks); cleared with
# the idempotency store in tests.
_SCAN_LOCKS: BoundedCache[tuple[str, str], asyncio.Lock] = BoundedCache(
    maxsize=_SCAN_LOCKS_MAX,
    is_idle=_is_submit_lock_idle,
)


def _is_live_entry(ent: _SubmitIdempotencyEntry) -> bool:
    """Return whether an idempotency entry is still live (#26 predicate).

    Single expiry semantic shared by :func:`_get_idempotency_entry` and
    :func:`_find_conflicting_entry`.

    Args:
        ent: Stored idempotency entry.

    Returns:
        True when the entry is within the TTL window.
    """
    return time.monotonic() - ent.created_at <= _SUBMIT_IDEMPOTENCY_TTL_S


def _get_submit_inflight_lock(key: SubmitIdempotencyKey) -> asyncio.Lock:
    """Return the in-flight lock for one (project_id, token) pair (#27).

    Creates the lock on first use via BoundedCache ``get_or_create`` (LRU
    touch on hit; idle-aware eviction over the cap — locked entries are
    never evicted). Synchronous cache access only (no awaits) so
    concurrent coroutines cannot interleave creation.

    Lock order (documented, #14): token-lock is always outer (acquired in
    the POST /submit wrapper), scan-lock inner (acquired in the impl
    after scan resolution). Never acquire in reverse.

    Args:
        key: SubmitIdempotencyKey scoping the token to its project.

    Returns:
        The shared ``asyncio.Lock`` for this key.
    """
    return _SUBMIT_INFLIGHT_LOCKS.get_or_create(key.as_tuple(), asyncio.Lock)


def _get_scan_lock(project_id: str, scan_id: str) -> asyncio.Lock:
    """Return the cross-token mutex for one (project_id, scan_id) (#14).

    Synchronous cache access only (no awaits). Bounded via BoundedCache
    with idle-aware eviction (locked entries are never evicted) so the
    map cannot grow without bound. Always acquire AFTER the per-token
    lock (token outer, scan inner) to avoid deadlock.

    Args:
        project_id: Owning project UUID.
        scan_id: Resolved scan/activity id.

    Returns:
        The shared ``asyncio.Lock`` for this scan.
    """
    return _SCAN_LOCKS.get_or_create((project_id, scan_id), asyncio.Lock)


def clear_submit_inflight_locks() -> None:
    """Clear per-token in-flight locks (test helper)."""
    _SUBMIT_INFLIGHT_LOCKS.clear()


def clear_submit_scan_locks() -> None:
    """Clear per-scan mutexes (test helper)."""
    _SCAN_LOCKS.clear()


def _is_valid_submit_token(token: str) -> bool:
    """Require 128-bit hex entropy (>= 32 hex chars) (#13).

    Tokens are single-use per operation: generate a fresh ``uuid4().hex``
    (32 hex chars) per submit operation via ``POST /approve``
    ``submit_token`` or a caller-generated ``Idempotency-Key`` header of
    equal entropy. Short/guessable tokens are rejected with 400
    ``invalid_idempotency_token`` so one user cannot guess another's
    token to replay or block their submit.

    Args:
        token: Candidate idempotency token.

    Returns:
        True when the dash-stripped token is all hex and >= 32 chars.
    """
    stripped = token.strip().replace("-", "")
    if len(stripped) < 32:
        return False
    return all(c in hexdigits for c in stripped)


def _get_idempotency_entry(key: SubmitIdempotencyKey) -> _SubmitIdempotencyEntry | None:
    """Fetch a live cache entry, evicting expired rows (#27, #26, #2).

    LRU touch on hit via BoundedCache. Expiry uses the shared
    :func:`_is_live_entry` semantic.

    Args:
        key: SubmitIdempotencyKey scoping the token to its project.

    Returns:
        Live entry, or None on miss/expiry.
    """
    t = key.as_tuple()
    try:
        entry = _SUBMIT_IDEMPOTENCY_STORE[t]
    except KeyError:
        return None
    if not _is_live_entry(entry):
        with contextlib.suppress(KeyError):
            del _SUBMIT_IDEMPOTENCY_STORE[t]
        return None
    return entry


def _put_idempotency_entry(
    key: SubmitIdempotencyKey,
    response: SubmitResponse,
    binding: SubmitBinding,
) -> None:
    """Store a submit result with LRU eviction (cap 1000) (#27, #28, #2).

    Single-shape: ``_put_idempotency_entry(key, response, binding)`` where
    ``key`` is a :class:`SubmitIdempotencyKey` and ``binding`` a
    :class:`SubmitBinding` carrying the request binding (including the
    patch content hash when available).

    Args:
        key: SubmitIdempotencyKey scoping the token to its project.
        response: Stored submit response replayed on token reuse.
        binding: SubmitBinding with branch/activity/scan/patch_hash.
    """
    t = key.as_tuple()
    _SUBMIT_IDEMPOTENCY_STORE[t] = _SubmitIdempotencyEntry(
        response=response,
        branch_name=binding.branch_name,
        activity_id=binding.activity_id,
        scan_id=binding.scan_id,
        patch_hash=binding.patch_hash,
    )


def _check_idempotency_binding(
    entry: _SubmitIdempotencyEntry,
    binding: SubmitBinding,
    *,
    enforce_hash: bool = True,
) -> None:
    """Raise 409 when a replay token is reused with different params.

    Single-shape: ``_check_idempotency_binding(entry, binding)``.

    Binding is (``branch_name``, ``activity_id``). ``scan_id`` is
    deliberately NOT part of the binding: it is a volatile attempt id —
    ``POST /operate`` allocates a fresh ``scan_id`` per attempt while
    forwarding the same ``Idempotency-Key`` to the embedded submit — so
    a scan mismatch with matching branch/activity replays the stored
    response instead of 409. Reusing a token with a different branch or
    activity is always a conflict. (#28) When both the stored and
    incoming ``patch_hash`` are present and differ, the patch set changed
    under a reused token: 409 ``idempotency_conflict``. When only one
    side carries a hash, the binding fails closed (409) unless the
    stored side has no hash yet, in which case the incoming hash binds
    on first observation (#13). Callers compute the incoming hash via
    :func:`_compute_patch_hash` before replay checks.

    Args:
        entry: Stored idempotency entry.
        binding: SubmitBinding with the incoming request binding
            (branch/activity/scan + optional patch_hash).
        enforce_hash: Skip patch-hash enforcement for pre-load fast
            paths that run before the patch set is available; the
            hash-verified check after the patched load still enforces.

    Raises:
        HTTPException: 409 ``idempotency_conflict`` on branch/activity
            or patch-hash mismatch.
    """
    in_branch = binding.branch_name
    in_activity = binding.activity_id
    in_hash = binding.patch_hash
    if in_branch is not None and in_branch != entry.response.branch_name:
        raise HTTPException(
            status_code=409,
            detail=_coded_detail(
                "idempotency_conflict",
                "Idempotency token was already used with a different branch_name.",
            ),
        )
    if in_activity is not None and entry.activity_id != in_activity:
        raise HTTPException(
            status_code=409,
            detail=_coded_detail(
                "idempotency_conflict",
                "Idempotency token was already used for a different activity.",
            ),
        )
    if in_hash is not None and entry.patch_hash is not None and in_hash != entry.patch_hash:
        raise HTTPException(
            status_code=409,
            detail=_coded_detail(
                "idempotency_conflict",
                "Idempotency token was already used with a different patch set.",
            ),
        )
    if enforce_hash and in_hash is None and entry.patch_hash is not None:
        # The stored result is bound to a concrete patch set but the
        # incoming set cannot be hashed (no comparable content): fail
        # closed rather than replaying a possibly-stale result (#13).
        raise HTTPException(
            status_code=409,
            detail=_coded_detail(
                "idempotency_conflict",
                "Idempotency token was already used with a patch set that "
                "cannot be compared to this submit; retry with a fresh token.",
            ),
        )
    if in_hash is not None and entry.patch_hash is None:
        # Bind on first observation: a stored entry without a hash gains
        # one so later different-patch retries 409 instead of replaying
        # stale results (#13). Single-use-per-operation tokens make the
        # first observed set authoritative.
        entry.patch_hash = in_hash
    # Scan mismatch with matching branch/activity is an atomic retry,
    # not a conflict: replay the stored response (no 409 on scan alone).


def _find_conflicting_entry(
    project_id: str,
    scan_id: str,
    branch_name: str | None,
) -> _SubmitIdempotencyEntry | None:
    """Find a same-scan entry with a different branch (#26).

    Replaces the O(n) inline store scan in the request path. Shares the
    :func:`_is_live_entry` expiry semantic: TTL-dead rows are evicted
    (like the single-key path treats them as a miss) instead of
    conflicting.

    Args:
        project_id: Owning project UUID.
        scan_id: Resolved scan id for this call.
        branch_name: Explicitly requested branch name (None = no check).

    Returns:
        Conflicting live entry, or None when no conflict.
    """
    if branch_name is None:
        return None
    for key in list(_SUBMIT_IDEMPOTENCY_STORE):
        try:
            ent = _SUBMIT_IDEMPOTENCY_STORE[key]
        except KeyError:
            continue
        if not _is_live_entry(ent):
            with contextlib.suppress(KeyError):
                del _SUBMIT_IDEMPOTENCY_STORE[key]
            continue
        if key[0] != project_id:
            continue
        if ent.scan_id == scan_id and branch_name != ent.response.branch_name:
            return ent
    return None


def _compute_patch_hash(patched: Sequence[Any] | None) -> str | None:
    """Hash a patch set for idempotency patch binding (#28).

    Hashes sorted ``(path, content)`` pairs with SHA-256. Accepts DB
    ``PatchedFile`` rows (``.path``/``.content``) or dicts.

    Args:
        patched: Patched-file sequence, if available at submit time.

    Returns:
        Hex digest, or None when no patch content is available.
    """
    if not patched:
        return None
    try:
        items: list[tuple[str, bytes]] = []
        for pf in patched:
            if isinstance(pf, dict):
                path = pf.get("path")
                content = pf.get("content")
            else:
                path = getattr(pf, "path", None)
                content = getattr(pf, "content", None)
            if path is None:
                continue
            if isinstance(content, str):
                content_b = content.encode("utf-8")
            elif isinstance(content, (bytes, bytearray)):
                content_b = bytes(content)
            elif content is None:
                content_b = b""
            else:
                content_b = str(content).encode("utf-8")
            items.append((str(path), content_b))
        if not items:
            return None
        items.sort(key=lambda x: x[0])
        h = hashlib.sha256()
        for path, content_b in items:
            h.update(path.encode("utf-8"))
            h.update(b"\x00")
            h.update(content_b)
            h.update(b"\x00")
        return h.hexdigest()
    except Exception:  # noqa: BLE001 -- hashing must never break submit
        logger.warning("Patch-hash computation failed; binding check disabled for this submit", exc_info=True)
        return None


async def _acquire_lock_with_timeout(
    lock: asyncio.Lock,
    *,
    timeout: float = _SUBMIT_LOCK_TIMEOUT_S,
) -> None:
    """Acquire a submit lock with a bounded wait (#29).

    Uses ``asyncio.timeout`` (not ``wait_for``) so a timeout cancels a
    pending acquire without leaving the mutex held on an
    acquire-then-timeout race (#20).

    Args:
        lock: Token or scan mutex to acquire.
        timeout: Maximum seconds to wait.

    Raises:
        HTTPException: 503 ``submit_in_progress`` with ``Retry-After``
            when the wait times out.
    """
    try:
        async with asyncio.timeout(timeout):
            await lock.acquire()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=_coded_detail(
                "submit_in_progress",
                "Another submit is in progress for this operation; retry shortly.",
            ),
            headers={"Retry-After": str(int(timeout))},
        ) from exc


def _effective_submit_token(request: Request | None, body_token: str | None) -> str | None:
    """Resolve the idempotency token for a submit call.

    Prefers the ``Idempotency-Key`` header, falling back to the
    ``submit_token`` body field from ``POST /approve`` (N19). Header and
    body paths share the same ``>= 32 hex char`` entropy enforcement at
    validation time (#13).

    Args:
        request: Incoming FastAPI request (for header lookup).
        body_token: ``submit_token`` from the request body.

    Returns:
        Stripped token string, or None when neither is provided.
    """
    header_token: str | None = None
    if request is not None:
        with contextlib.suppress(Exception):
            raw = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
            if raw and raw.strip():
                header_token = raw.strip()
    if header_token:
        return header_token
    if body_token and body_token.strip():
        return body_token.strip()
    return None


def clear_submit_idempotency_store() -> None:
    """Clear the in-memory submit idempotency cache (test helper)."""
    _SUBMIT_IDEMPOTENCY_STORE.clear()
    _SUBMIT_INFLIGHT_LOCKS.clear()
    _SCAN_LOCKS.clear()
