"""Proposal working-set package (ADR-062)."""

from apme_gateway.proposals.draft import (
    abandon_project_drafts,
    apply_draft_updates,
    commit_gate_decisions,
    project_has_draft_proposals,
    upsert_live_proposal_stubs,
)
from apme_gateway.proposals.flush import (
    discard_scan_proposals,
    flush_proposals_for_project,
    flush_proposals_for_scan,
    replace_scan_proposals,
)
from apme_gateway.proposals.grouping import group_violations, merge_outcomes

__all__ = [
    "abandon_project_drafts",
    "apply_draft_updates",
    "commit_gate_decisions",
    "discard_scan_proposals",
    "flush_proposals_for_project",
    "flush_proposals_for_scan",
    "group_violations",
    "merge_outcomes",
    "project_has_draft_proposals",
    "replace_scan_proposals",
    "upsert_live_proposal_stubs",
]
