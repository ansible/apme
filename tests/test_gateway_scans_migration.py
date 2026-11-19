"""Unit tests for the additive ``scans`` column migration (ADR-050)."""

from __future__ import annotations

from apme_gateway.db import _scans_column_migrations

_ALL_COLUMNS = {"pr_url", "branch_name", "commit_sha", "submit_token", "scm_provider"}


def test_scans_column_migrations_are_idempotent_on_postgres() -> None:
    """PostgreSQL upgrades tolerate a concurrent replica's committed ALTER.

    Two replicas can inspect the same pre-upgrade table, so the generated
    statements must be no-ops when the column already exists.
    """
    statements = _scans_column_migrations("postgresql", {"pr_url", "branch_name"})
    assert len(statements) == 3
    assert all("ADD COLUMN IF NOT EXISTS" in stmt for stmt in statements)
    assert _scans_column_migrations("postgresql", _ALL_COLUMNS) == []


def test_scans_column_migrations_keep_plain_alter_on_sqlite() -> None:
    """SQLite has no ``IF NOT EXISTS`` clause for columns."""
    statements = _scans_column_migrations("sqlite", set())
    assert len(statements) == 5
    assert all("IF NOT EXISTS" not in stmt for stmt in statements)
    assert "ALTER TABLE scans ADD COLUMN submit_token TEXT DEFAULT NULL" in statements
