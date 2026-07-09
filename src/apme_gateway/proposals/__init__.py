"""Proposal working-set package (ADR-062)."""

from apme_gateway.proposals.flush import (
    discard_scan_proposals,
    flush_proposals_for_project,
    flush_proposals_for_scan,
    replace_scan_proposals,
)
from apme_gateway.proposals.grouping import group_violations, merge_outcomes

__all__ = [
    "discard_scan_proposals",
    "flush_proposals_for_project",
    "flush_proposals_for_scan",
    "group_violations",
    "merge_outcomes",
    "replace_scan_proposals",
]
