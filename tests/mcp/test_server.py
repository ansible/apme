from __future__ import annotations

from apme_mcp import server


def test_server_registers_core_tools() -> None:
    assert callable(server.start_project_scan)
    assert callable(server.start_remediation)
    assert callable(server.approve_proposals)
    assert callable(server.submit_remediation)
