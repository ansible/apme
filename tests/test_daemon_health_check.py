"""Tests for daemon HTTP health-check body validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apme_engine.daemon.health_check import _http_health_body_ok, check_http_health


def test_http_health_body_ok_accepts_documented_json() -> None:
    """Galaxy Proxy ``{"status": "ok"}`` is healthy."""
    assert _http_health_body_ok('{"status": "ok"}') is True


def test_http_health_body_ok_rejects_not_ok_substring() -> None:
    """Bodies that merely contain the substring ``ok`` are unhealthy."""
    assert _http_health_body_ok('{"status": "not ok"}') is False
    assert _http_health_body_ok("not ok") is False


def test_http_health_body_ok_rejects_bare_ok() -> None:
    """Bare ok strings are not the documented Galaxy Proxy contract."""
    assert _http_health_body_ok("ok") is False
    assert _http_health_body_ok("OK\n") is False


def test_check_http_health_rejects_not_ok_body() -> None:
    """HTTP 200 with ``not ok`` must not report ok=True."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status": "not ok"}'
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("apme_engine.daemon.health_check.httpx.Client", return_value=mock_client):
        result = check_http_health("http://127.0.0.1:8765")

    assert result["ok"] is False
