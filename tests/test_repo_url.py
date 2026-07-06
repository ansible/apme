"""Tests for repository URL normalization."""

from __future__ import annotations

from apme_gateway.scm.repo_url import normalize_repo_url


def test_normalize_repo_url_strips_git_suffix() -> None:
    """Strip a trailing ``.git`` suffix from HTTPS repo URLs."""
    assert normalize_repo_url("https://github.com/acme-scm/amazon.aws.git") == "https://github.com/acme-scm/amazon.aws"


def test_normalize_repo_url_lowercases_host() -> None:
    """Lowercase the URL host while trimming trailing slashes."""
    assert normalize_repo_url("https://GitHub.com/acme-scm/release-222/") == "https://github.com/acme-scm/release-222"


def test_normalize_repo_url_preserves_path_case() -> None:
    """Preserve owner and repository path casing."""
    assert normalize_repo_url("https://github.com/Org/MyRepo") == "https://github.com/Org/MyRepo"
