"""Unit tests for ADR-062 proposal grouping."""

from __future__ import annotations

from apme_gateway.proposals.grouping import (
    SOURCE_AI_CANDIDATE,
    SOURCE_DETERMINISTIC,
    analytics_increments,
    group_violations,
    merge_outcomes,
    review_status_for_proposal,
    rule_group_fingerprint,
)


def test_group_same_path_merges_rules() -> None:
    """Violations sharing path become one coupled proposal."""
    violations = [
        {
            "id": 1,
            "rule_id": "L013",
            "file": "tasks/main.yml",
            "path": "tasks/main.yml::task[0]",
            "line": 10,
            "remediation_class": 1,
            "fixed_yaml": "command: echo hi\n",
            "original_yaml": "shell: echo hi\n",
        },
        {
            "id": 2,
            "rule_id": "L007",
            "file": "tasks/main.yml",
            "path": "tasks/main.yml::task[0]",
            "line": 10,
            "remediation_class": 1,
            "fixed_yaml": "command: echo hi\n",
            "original_yaml": "shell: echo hi\n",
        },
    ]
    props = group_violations(violations)
    assert len(props) == 1
    assert props[0].coupled is True
    assert set(props[0].rule_ids) == {"L007", "L013"}
    assert set(props[0].violation_ids) == {1, 2}
    assert props[0].source == SOURCE_DETERMINISTIC
    assert props[0].gate == "tier1"
    assert props[0].status == "pending"  # no review_status → do not invent approved
    assert props[0].path == "tasks/main.yml::task[0]"


def test_group_empty_path_is_singleton() -> None:
    """Empty path yields one proposal per violation."""
    violations = [
        {"id": 1, "rule_id": "L001", "file": "a.yml", "path": "", "line": 1, "remediation_class": 2},
        {"id": 2, "rule_id": "L002", "file": "a.yml", "path": "", "line": 2, "remediation_class": 2},
    ]
    props = group_violations(violations)
    assert len(props) == 2
    assert all(not p.coupled for p in props)
    assert all(p.source == SOURCE_AI_CANDIDATE for p in props)


def test_classify_prefers_remediation_class_over_fixed_yaml() -> None:
    """AI-candidate with leftover fixed_yaml stays AI, not Tier 1."""
    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L099",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 2,
                "fixed_yaml": "x: 1\n",
                "original_yaml": "x: 0\n",
            }
        ]
    )
    assert props[0].source == SOURCE_AI_CANDIDATE
    assert props[0].gate == "ai"
    assert props[0].tier == 2


def test_group_stable_proposal_id() -> None:
    """Same inputs produce the same proposal_id."""
    violations = [
        {
            "id": 9,
            "rule_id": "M001",
            "file": "pb.yml",
            "path": "pb.yml::play[0]#task[1]",
            "remediation_class": 1,
            "fixed_yaml": "x: 1\n",
        }
    ]
    a = group_violations(violations)
    b = group_violations(violations)
    assert a[0].proposal_id == b[0].proposal_id
    assert a[0].proposal_id.startswith("prop-tier1-")


def test_merge_outcomes_by_file_rule() -> None:
    """ProposalOutcome overlays status onto matching grouped proposal."""

    class _Outcome:
        proposal_id = ""
        rule_id = "L007"
        file = "tasks/main.yml"
        status = "rejected"
        confidence = 0.9
        tier = 2

    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "tasks/main.yml",
                "path": "tasks/main.yml::task[0]",
                "remediation_class": 2,
            }
        ]
    )
    merged = merge_outcomes(props, [_Outcome()])
    assert merged[0].status == "declined"
    assert merged[0].confidence == 0.9
    assert merged[0].source in {SOURCE_AI_CANDIDATE, "ai"}
    assert merged[0].gate == "ai"
    assert merged[0].tier == 2


def test_merge_outcomes_tier2_overrides_deterministic_source() -> None:
    """Outcome tier>=2 realigns source/gate even when pre-grouped as Tier 1."""
    from dataclasses import replace

    class _Outcome:
        proposal_id = "p1"
        rule_id = "L007"
        file = "a.yml"
        status = "approved"
        confidence = 0.8
        tier = 2

    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "x\n",
            }
        ]
    )
    assert props[0].source == "deterministic"
    # Simulate a mixed-bucket / outcome overlay that raises tier.
    props = [replace(props[0], proposal_id="prop-tier1-deadbeef")]
    merged = merge_outcomes(props, [_Outcome()])
    assert merged[0].tier == 2
    assert merged[0].source == "ai"
    assert merged[0].gate == "ai"
    assert merged[0].proposal_id == "prop-ai-deadbeef"
    assert merged[0].stamp_rule_ids == ("L007",)


def test_merge_outcomes_duplicate_file_rule_does_not_overwrite() -> None:
    """Two outcomes for the same file+rule claim distinct proposals."""

    class _Outcome:
        def __init__(self, status: str, confidence: float) -> None:
            self.proposal_id = ""
            self.rule_id = "L007"
            self.file = "a.yml"
            self.status = status
            self.confidence = confidence
            self.tier = 2

    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "",
                "line": 1,
                "remediation_class": 2,
            },
            {
                "id": 2,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "",
                "line": 2,
                "remediation_class": 2,
            },
        ]
    )
    merged = merge_outcomes(props, [_Outcome("approved", 0.9), _Outcome("rejected", 0.2)])
    statuses = {p.status for p in merged}
    assert statuses == {"approved", "declined"}
    confidences = {p.confidence for p in merged}
    assert confidences == {0.9, 0.2}


def test_analytics_increments_normalize_outcome_source() -> None:
    """GroupedProposal SOURCE_OUTCOME normalizes like Mapping input."""
    from dataclasses import replace

    props = group_violations([{"id": 1, "rule_id": "L001", "file": "a.yml", "path": "", "remediation_class": 0}])
    outcome_prop = replace(props[0], source="outcome", tier=0, status="approved")
    rows = analytics_increments(outcome_prop)
    assert rows
    assert all(r["source"] == "deterministic" for r in rows)

    map_rows = analytics_increments(
        {
            "status": "approved",
            "rule_id": "L001",
            "rule_ids": ["L001"],
            "source": "outcome",
            "tier": 0,
            "gate": "",
            "coupled": False,
        }
    )
    assert map_rows[0]["source"] == rows[0]["source"]


def test_violation_accepts_review_status_filters_mixed_bucket() -> None:
    """Review stamps only matching remediation_class rows in mixed buckets."""
    from types import SimpleNamespace

    from apme_gateway.proposals.grouping import violation_accepts_review_status

    fixed = SimpleNamespace(fixed_yaml="x\n", remediation_class=1)
    ai = SimpleNamespace(fixed_yaml="", remediation_class=2)
    manual = SimpleNamespace(fixed_yaml="", remediation_class=3)
    assert violation_accepts_review_status("deterministic", fixed) is True
    assert violation_accepts_review_status("deterministic", ai) is False
    assert violation_accepts_review_status("deterministic", manual) is False
    assert violation_accepts_review_status("ai", ai) is True
    assert violation_accepts_review_status("ai", fixed) is False
    assert violation_accepts_review_status("ai", manual) is False
    assert violation_accepts_review_status("ai-candidate", manual) is False
    assert violation_accepts_review_status("outcome", fixed) is True
    assert violation_accepts_review_status("outcome", manual) is False
    assert violation_accepts_review_status("unknown", fixed) is False
    assert violation_accepts_review_status("unknown", ai) is False
    # Post-apply rem_class rewrite: AI approve stamps fixed AUTO_FIXABLE;
    # AI decline stamps remaining MANUAL_REVIEW.
    assert violation_accepts_review_status("ai", fixed, decision="approved") is True
    assert violation_accepts_review_status("ai", manual, decision="declined") is True
    assert violation_accepts_review_status("ai", fixed, decision="declined") is False
    assert violation_accepts_review_status("ai", manual, decision="approved") is False
    # fixed_yaml alone without AUTO_FIXABLE is not enough for AI approve.
    bare_fixed = SimpleNamespace(fixed_yaml="x\n", remediation_class=0)
    assert violation_accepts_review_status("ai", bare_fixed, decision="approved") is False


def test_analytics_increments_pure_and_group() -> None:
    """Coupled decline emits per-rule coupled rows plus group fingerprint."""
    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "x\n",
            },
            {
                "id": 2,
                "rule_id": "L013",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "x\n",
            },
        ]
    )
    # Force declined for analytics shape test.
    from dataclasses import replace

    declined = replace(props[0], status="declined")
    rows = analytics_increments(declined)
    rule_rows = [r for r in rows if r["is_group"] == 0]
    group_rows = [r for r in rows if r["is_group"] == 1]
    assert len(rule_rows) == 2
    assert all(r["coupled"] == 1 and r["declined_delta"] == 1 for r in rule_rows)
    assert len(group_rows) == 1
    assert group_rows[0]["rule_id"] == rule_group_fingerprint(["L007", "L013"])


def test_review_status_mapping() -> None:
    """Source+status maps to granular review_status."""
    assert review_status_for_proposal("deterministic", "approved") == "deterministic_approved"
    assert review_status_for_proposal("ai", "declined") == "ai_declined"
    assert review_status_for_proposal("ai-candidate", "rejected") == "ai_declined"
    assert review_status_for_proposal("deterministic", "pending") is None


def test_merge_outcomes_comma_joined_rule_id() -> None:
    """Coupled engine outcomes with comma-joined rule_id match per-rule queues."""

    class _Outcome:
        proposal_id = "ai-0000"
        rule_id = "L007,L013"
        file = "a.yml"
        status = "approved"
        confidence = 0.95
        tier = 2

    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 2,
            },
            {
                "id": 2,
                "rule_id": "L013",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 2,
            },
        ]
    )
    merged = merge_outcomes(props, [_Outcome()])
    assert len(merged) == 1
    assert merged[0].status == "approved"
    assert merged[0].source == "ai"
    assert merged[0].confidence == 0.95


def test_group_violations_does_not_invent_approved_without_review_status() -> None:
    """Rebuild leaves pending when fixed_yaml exists but review_status is NULL."""
    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L013",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "command: x\n",
                "original_yaml": "shell: x\n",
            }
        ]
    )
    assert props[0].status == "pending"


def test_group_violations_mixed_declines_rebuild_as_declined() -> None:
    """Same-path Tier-1 and AI declines become separate lane proposals."""
    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "x\n",
                "review_status": "deterministic_declined",
            },
            {
                "id": 2,
                "rule_id": "L013",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 2,
                "review_status": "ai_declined",
            },
        ]
    )
    assert len(props) == 2
    by_source = {p.source: p for p in props}
    assert by_source["deterministic"].status == "declined"
    assert by_source["ai-candidate"].status == "declined"


def test_group_violations_splits_mixed_rem_class_path() -> None:
    """AUTO_FIXABLE and AI_CANDIDATE on the same path are separate proposals."""
    props = group_violations(
        [
            {
                "id": 1,
                "rule_id": "L007",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 1,
                "fixed_yaml": "x\n",
            },
            {
                "id": 2,
                "rule_id": "L013",
                "file": "a.yml",
                "path": "a.yml::t[0]",
                "remediation_class": 2,
            },
        ]
    )
    assert len(props) == 2
    sources = {p.source for p in props}
    assert sources == {"deterministic", "ai-candidate"}
