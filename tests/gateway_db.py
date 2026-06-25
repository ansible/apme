"""Shared helpers for Gateway PostgreSQL-backed tests."""

from __future__ import annotations

import os

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://apme:apme@127.0.0.1:5432/apme_test"

GATEWAY_DB_TEST_MODULES = frozenset(
    {
        "tests.test_gateway_db",
        "tests.test_gateway_api",
        "tests.test_gateway_dependencies",
        "tests.test_gateway_galaxy_servers",
        "tests.test_gateway_graph",
        "tests.test_gateway_notifications",
        "tests.test_gateway_projects",
        "tests.test_gateway_pull_request",
        "tests.test_gateway_scan",
        "tests.test_gateway_servicer",
        "tests.test_gateway_suppressions",
        "tests.test_proposal_draft",
        "tests.test_proposal_flush",
        "tests.test_rule_catalog",
        "tests.test_sbom_endpoint",
    }
)


def test_database_url() -> str:
    """Return the PostgreSQL URL used by Gateway unit tests.

    Returns:
        URL from ``APME_TEST_DATABASE_URL`` or the local default.
    """
    return os.environ.get("APME_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL).strip()
