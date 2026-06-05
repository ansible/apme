"""Unit tests for the gateway suppression endpoints (ADR-055)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, init_db


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_suppression_server_computed_fingerprint(client: AsyncClient) -> None:
    """POST /suppressions computes the canonical fingerprint when original_yaml is provided.

    Args:
        client: Async HTTP test client.
    """
    from apme_engine.fingerprint import compute_fingerprint

    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "- name: test task\n  debug:\n    msg: hello\n",
            "reason": "Not applicable",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    expected_fp = compute_fingerprint("L001", "- name: test task\n  debug:\n    msg: hello\n")
    assert body["fingerprint_hash"] == expected_fp
    assert body["rule_id"] == "L001"
    assert body["reason"] == "Not applicable"


async def test_create_suppression_client_hash_fallback(client: AsyncClient) -> None:
    """POST /suppressions accepts a pre-computed hash when original_yaml is absent.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L002",
            "fingerprint_hash": "abcdef1234567890" * 4,
            "reason": "Legacy client",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["fingerprint_hash"] == "abcdef1234567890" * 4


async def test_create_suppression_duplicate_returns_409(client: AsyncClient) -> None:
    """POST /suppressions returns 409 on duplicate fingerprint+scope.

    Args:
        client: Async HTTP test client.
    """
    payload = {
        "rule_id": "L001",
        "original_yaml": "- name: dup\n  debug:\n    msg: hi\n",
        "reason": "first",
    }
    resp1 = await client.post("/api/v1/suppressions", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/api/v1/suppressions", json=payload)
    assert resp2.status_code == 409


async def test_create_suppression_requires_hash_or_yaml(client: AsyncClient) -> None:
    """POST /suppressions returns 422 when neither hash nor original_yaml is provided.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={"rule_id": "L001", "reason": "nothing"},
    )
    assert resp.status_code == 422


async def test_list_suppressions(client: AsyncClient) -> None:
    """GET /suppressions returns created records.

    Args:
        client: Async HTTP test client.
    """
    await client.post(
        "/api/v1/suppressions",
        json={"rule_id": "L001", "original_yaml": "x: 1\n", "scope": "global", "reason": "a"},
    )
    await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L002",
            "original_yaml": "y: 2\n",
            "scope": "project:aabbccdd11223344aabbccdd11223344",
            "reason": "b",
        },
    )

    resp = await client.get("/api/v1/suppressions")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp_scoped = await client.get("/api/v1/suppressions?scope=global")
    assert resp_scoped.status_code == 200
    assert len(resp_scoped.json()) == 1


async def test_delete_suppression(client: AsyncClient) -> None:
    """DELETE /suppressions/{id} removes the record.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/suppressions",
        json={"rule_id": "L001", "original_yaml": "z: 3\n", "reason": "temp"},
    )
    suppression_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/suppressions/{suppression_id}")
    assert del_resp.status_code == 204

    list_resp = await client.get("/api/v1/suppressions")
    assert len(list_resp.json()) == 0


async def test_fingerprint_matches_engine_module(client: AsyncClient) -> None:
    """Server fingerprint is identical to apme_engine.fingerprint.compute_fingerprint.

    Args:
        client: Async HTTP test client.
    """
    from apme_engine.fingerprint import compute_fingerprint

    yaml_content = "- name: Install packages\n  ansible.builtin.dnf:\n    name: httpd\n"
    resp = await client.post(
        "/api/v1/suppressions",
        json={"rule_id": "native:M005", "original_yaml": yaml_content, "reason": "test"},
    )
    assert resp.status_code == 201
    assert resp.json()["fingerprint_hash"] == compute_fingerprint("native:M005", yaml_content)


async def test_create_suppression_invalid_mode_returns_422(client: AsyncClient) -> None:
    """POST /suppressions returns 422 for invalid fingerprint_mode.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "x: 1\n",
            "fingerprint_mode": "bogus",
            "reason": "test",
        },
    )
    assert resp.status_code == 422


async def test_create_suppression_rule_module_rejected(client: AsyncClient) -> None:
    """POST /suppressions returns 422 for rule_module mode (unsupported).

    rule_module suppressions cannot be honored by read paths because
    violation rows do not store resolved module FQCNs.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "x: 1\n",
            "fingerprint_mode": "rule_module",
            "module_fqcn": "ansible.builtin.debug",
            "reason": "test",
        },
    )
    assert resp.status_code == 422
    assert "rule_module" in resp.json()["detail"]


async def test_create_suppression_invalid_hash_format_returns_422(client: AsyncClient) -> None:
    """POST /suppressions returns 422 when fingerprint_hash is not valid hex SHA-256.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "fingerprint_hash": "not-a-valid-sha256",
            "reason": "test",
        },
    )
    assert resp.status_code == 422
    assert "64-character" in resp.json()["detail"]


async def test_create_suppression_empty_yaml_full_mode_returns_422(client: AsyncClient) -> None:
    """POST /suppressions rejects empty original_yaml in full mode to prevent collision.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "",
            "fingerprint_mode": "full",
            "reason": "test",
        },
    )
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"].lower()


async def test_create_suppression_comment_only_yaml_full_mode_returns_422(client: AsyncClient) -> None:
    """POST /suppressions rejects comment-only YAML in full mode (normalizes to empty).

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "# just a comment\n",
            "fingerprint_mode": "full",
            "reason": "test",
        },
    )
    assert resp.status_code == 422
    assert "normalizes to empty" in resp.json()["detail"]


async def test_create_suppression_normalizes_uppercase_hash(client: AsyncClient) -> None:
    """POST /suppressions normalizes uppercase hex to lowercase.

    Args:
        client: Async HTTP test client.
    """
    upper_hash = "ABCDEF1234567890" * 4
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "fingerprint_hash": upper_hash,
            "reason": "test",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["fingerprint_hash"] == upper_hash.lower()


async def test_create_suppression_invalid_scope_returns_422(client: AsyncClient) -> None:
    """POST /suppressions returns 422 for invalid scope values.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": "x: 1\n",
            "scope": "invalid-scope",
            "reason": "test",
        },
    )
    assert resp.status_code == 422


async def test_list_suppressions_pagination(client: AsyncClient) -> None:
    """GET /suppressions supports limit/offset pagination.

    Args:
        client: Async HTTP test client.
    """
    for i in range(5):
        await client.post(
            "/api/v1/suppressions",
            json={"rule_id": f"L{i:03d}", "original_yaml": f"key: {i}\n", "reason": "test"},
        )

    resp = await client.get("/api/v1/suppressions?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get("/api/v1/suppressions?limit=2&offset=4")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_activity_detail_suppressed_flag(client: AsyncClient) -> None:
    """GET /activity/{id} annotates violations with suppressed=True when matched.

    Seeds a session, scan, and violations, creates a suppression matching
    one violation, then verifies the suppressed flag on each violation.

    Args:
        client: Async HTTP test client.
    """
    from apme_gateway.db import get_session
    from apme_gateway.db.models import Scan, Session, Violation

    yaml_a = "- name: Task A\n  debug:\n    msg: hello\n"
    yaml_b = "- name: Task B\n  command: whoami\n"

    async with get_session() as db:
        session = Session(
            session_id="sess0001",
            project_path="/tmp/test-project",
            first_seen="2024-01-01T00:00:00Z",
            last_seen="2024-01-01T00:00:00Z",
        )
        db.add(session)
        await db.flush()

        scan = Scan(
            scan_id="scan0001",
            session_id="sess0001",
            project_path="/tmp/test-project",
            source="cli",
            trigger="cli",
            created_at="2024-01-01T00:00:00Z",
            scan_type="check",
            total_violations=2,
            auto_fixable=0,
            ai_candidate=0,
            manual_review=2,
        )
        db.add(scan)
        await db.flush()

        v1 = Violation(
            scan_id="scan0001",
            rule_id="L001",
            level="warning",
            message="First violation",
            original_yaml=yaml_a,
        )
        v2 = Violation(
            scan_id="scan0001",
            rule_id="L002",
            level="error",
            message="Second violation",
            original_yaml=yaml_b,
        )
        db.add_all([v1, v2])
        await db.commit()

    create_resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "L001",
            "original_yaml": yaml_a,
            "reason": "Acknowledged",
        },
    )
    assert create_resp.status_code == 201

    resp = await client.get("/api/v1/activity/scan0001")
    assert resp.status_code == 200
    body = resp.json()
    violations = body["violations"]
    assert len(violations) == 2

    suppressed_map = {v["rule_id"]: v["suppressed"] for v in violations}
    assert suppressed_map["L001"] is True
    assert suppressed_map["L002"] is False


async def test_dep_health_excludes_suppressed_violations(client: AsyncClient) -> None:
    """GET /dep-health excludes suppressed violations from counts.

    Seeds a project with a scan containing collection_health violations,
    creates a suppression matching one, and verifies the count drops.

    Args:
        client: Async HTTP test client.
    """
    from apme_gateway.db import get_session
    from apme_gateway.db.models import Project, Scan, Session, Violation

    yaml_a = "- name: outdated collection\n  debug:\n    msg: old\n"
    yaml_b = "- name: another collection issue\n  command: ls\n"

    async with get_session() as db:
        project = Project(
            id="proj0001",
            name="test-project",
            repo_url="https://github.com/test/repo",
            branch="main",
            created_at="2024-01-01T00:00:00Z",
        )
        db.add(project)
        await db.flush()

        session = Session(
            session_id="sess0002",
            project_path="/tmp/test-project",
            first_seen="2024-01-01T00:00:00Z",
            last_seen="2024-01-01T00:00:00Z",
        )
        db.add(session)
        await db.flush()

        scan = Scan(
            scan_id="scan0002",
            session_id="sess0002",
            project_id="proj0001",
            project_path="/tmp/test-project",
            source="cli",
            trigger="cli",
            created_at="2024-01-01T00:00:00Z",
            scan_type="check",
            total_violations=2,
            auto_fixable=0,
            ai_candidate=0,
            manual_review=2,
        )
        db.add(scan)
        await db.flush()

        v1 = Violation(
            scan_id="scan0002",
            rule_id="R200",
            level="high",
            message="Outdated collection",
            path="community.general",
            validator_source="collection_health",
            original_yaml=yaml_a,
        )
        v2 = Violation(
            scan_id="scan0002",
            rule_id="R201",
            level="medium",
            message="Another issue",
            path="community.general",
            validator_source="collection_health",
            original_yaml=yaml_b,
        )
        db.add_all([v1, v2])
        await db.commit()

    resp_before = await client.get("/api/v1/dep-health")
    assert resp_before.status_code == 200
    before = resp_before.json()
    coll_before = before["collection_findings"]
    assert len(coll_before) == 1
    assert coll_before[0]["finding_count"] == 2
    assert before["suppressed_count"] == 0

    create_resp = await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "R200",
            "original_yaml": yaml_a,
            "scope": "global",
            "reason": "Acknowledged",
        },
    )
    assert create_resp.status_code == 201

    resp_after = await client.get("/api/v1/dep-health")
    assert resp_after.status_code == 200
    after = resp_after.json()
    coll_after = after["collection_findings"]
    assert coll_after[0]["finding_count"] == 1
    assert after["suppressed_count"] == 1


async def test_dep_health_project_scoped_suppression_no_leak(client: AsyncClient) -> None:
    """Project-scoped suppressions do not leak to other projects in dep-health.

    Creates two projects with identical violations. A project-scoped
    suppression for project A must not affect project B's counts.

    Args:
        client: Async HTTP test client.
    """
    from apme_gateway.db import get_session
    from apme_gateway.db.models import Project, Scan, Session, Violation

    yaml_content = "- name: outdated dep\n  debug:\n    msg: old\n"
    proj_a_id = "aaaabbbbccccddddeeeeffffaaaabbbb"
    proj_b_id = "11112222333344445555666677778888"

    async with get_session() as db:
        for pid, pname in [(proj_a_id, "project-a"), (proj_b_id, "project-b")]:
            db.add(
                Project(
                    id=pid,
                    name=pname,
                    repo_url="https://github.com/test/repo",
                    branch="main",
                    created_at="2024-01-01T00:00:00Z",
                )
            )
        await db.flush()

        session = Session(
            session_id="sess0010",
            project_path="/tmp/multi",
            first_seen="2024-01-01T00:00:00Z",
            last_seen="2024-01-01T00:00:00Z",
        )
        db.add(session)
        await db.flush()

        for idx, pid in enumerate([proj_a_id, proj_b_id]):
            scan = Scan(
                scan_id=f"scan001{idx}",
                session_id="sess0010",
                project_id=pid,
                project_path="/tmp/multi",
                source="cli",
                trigger="cli",
                created_at="2024-01-01T00:00:00Z",
                scan_type="check",
                total_violations=1,
                auto_fixable=0,
                ai_candidate=0,
                manual_review=1,
            )
            db.add(scan)
            await db.flush()
            db.add(
                Violation(
                    scan_id=f"scan001{idx}",
                    rule_id="R200",
                    level="high",
                    message="Outdated",
                    path="community.general",
                    validator_source="collection_health",
                    original_yaml=yaml_content,
                )
            )
        await db.commit()

    resp_before = await client.get("/api/v1/dep-health")
    assert resp_before.status_code == 200
    assert resp_before.json()["collection_findings"][0]["finding_count"] == 2
    assert resp_before.json()["suppressed_count"] == 0

    await client.post(
        "/api/v1/suppressions",
        json={
            "rule_id": "R200",
            "original_yaml": yaml_content,
            "scope": f"project:{proj_a_id}",
            "reason": "project A only",
        },
    )

    resp_after = await client.get("/api/v1/dep-health")
    assert resp_after.status_code == 200
    after = resp_after.json()
    assert after["collection_findings"][0]["finding_count"] == 1
    assert after["suppressed_count"] == 1
