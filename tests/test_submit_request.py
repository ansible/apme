"""Unit tests for SubmitRequest branch-name validation (finding #44)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apme_gateway.api.schemas import SubmitRequest


def test_branch_name_defaults_to_none() -> None:
    """Omitted branch_name selects the auto-generated default."""
    assert SubmitRequest().branch_name is None


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "name",
    [
        "apme/remediate-abc123",
        "feature/my-fix_2.0",
        "release-1",
        # Whole-string suffix rules do not apply mid-path: git accepts a
        # non-final ``.`` component and ``HEAD`` as a path prefix.
        "a./b",
        "HEAD/foo",
    ],
)
def test_branch_name_accepts_safe_names(name: str) -> None:
    """Safe branch names validate unchanged.

    Args:
        name: Candidate branch name.
    """
    assert SubmitRequest(branch_name=name).branch_name == name


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "name",
    [
        "",
        "   ",
        "../escape",
        "a/b/../../c",
        "has space",
        "semi;colon",
        "x" * 101,
        "back\\slash",
        "/leading-slash",
        "trailing-slash/",
        "doubled//slash",
        "trailing-dot.",
        "name.lock",
        # ``git check-ref-format`` rejects ``.lock`` on any component and
        # reserves the bare name ``HEAD`` — both must fail fast here.
        "a.lock/b",
        "HEAD",
        "feat@{x}",
        "-leading-dash",
        ".hidden/component",
    ],
)
def test_branch_name_rejects_unsafe_names(name: str) -> None:
    """Traversal, blank, overlong, and out-of-charset names fail with 422-shape errors.

    Args:
        name: Candidate branch name.
    """
    with pytest.raises(ValidationError):
        SubmitRequest(branch_name=name)


def test_branch_name_accepts_100_char_name() -> None:
    """The length boundary itself remains usable."""
    name = "a" * 100
    assert SubmitRequest(branch_name=name).branch_name == name


@pytest.mark.parametrize("branch_name", ["../escape", "a/b/../../c", "x" * 101])  # type: ignore[untyped-decorator]
def test_project_branch_validators_share_submit_rules(branch_name: str) -> None:
    """Project create/update branches reject what SubmitRequest rejects.

    Args:
        branch_name: Candidate branch name.
    """
    from apme_gateway.api.schemas import CreateProjectRequest, UpdateProjectRequest

    with pytest.raises(ValidationError):
        CreateProjectRequest(name="n", repo_url="https://github.com/o/r.git", branch=branch_name)
    with pytest.raises(ValidationError):
        UpdateProjectRequest(branch=branch_name)
