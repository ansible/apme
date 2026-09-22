"""Tests for ADR index generation status normalization."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from generate_adr_index import _normalize_status  # noqa: E402


def test_normalize_status_partially_implemented_before_implemented() -> None:
    """Partial status must not match the generic implemented substring."""
    assert _normalize_status("Partially Implemented (webhooks and token auth pending)") == "Partially Implemented"


def test_normalize_status_accepted_with_implemented_in_detail() -> None:
    """Accepted lines that mention shipped work stay Accepted."""
    assert _normalize_status("Accepted (daemon launcher implementation pending)") == "Accepted"


def test_normalize_status_acceptedness_does_not_match() -> None:
    """Substring 'accepted' inside unrelated words must not bucket as Accepted."""
    assert _normalize_status("Acceptedness review pending") == "Proposed"


def test_normalize_status_canonical_values() -> None:
    """Canonical single-word statuses map to expected buckets."""
    assert _normalize_status("Implemented") == "Implemented"
    assert _normalize_status("Partially Implemented") == "Partially Implemented"
    assert _normalize_status("Accepted") == "Accepted"
    assert _normalize_status("Proposed") == "Proposed"
    assert _normalize_status("Superseded by ADR-022") == "Superseded"
