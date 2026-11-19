"""Unit tests for the additive ``scans`` column migration (ADR-050)."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from apme_gateway.db import _add_column_clause, _migrate_notification_outbox_table, _scans_column_migrations

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


def test_add_column_clause_is_concurrency_safe_on_postgres() -> None:
    """Concurrent replicas must not fail startup on a duplicate column."""
    assert _add_column_clause("postgresql") == "ADD COLUMN IF NOT EXISTS"
    assert _add_column_clause("sqlite") == "ADD COLUMN"


def test_notification_outbox_migration_adds_content_hash_and_cleans_orphans() -> None:
    """The outbox migration is additive and reruns without change."""
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE scans (scan_id TEXT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE notification_outbox (id INTEGER PRIMARY KEY, scan_id TEXT, sent_at TEXT)"))
            conn.execute(text("INSERT INTO scans (scan_id) VALUES ('scan-1')"))
            conn.execute(text("INSERT INTO notification_outbox (scan_id) VALUES ('scan-1'), ('scan-gone')"))

            _migrate_notification_outbox_table(conn)

            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(notification_outbox)"))}
            assert "content_hash" in columns
            assert conn.execute(text("SELECT scan_id FROM notification_outbox")).scalars().all() == ["scan-1"]

            # A second replica's startup run finds the column and the index.
            _migrate_notification_outbox_table(conn)
            assert conn.execute(text("SELECT count(*) FROM notification_outbox")).scalar_one() == 1
    finally:
        engine.dispose()
