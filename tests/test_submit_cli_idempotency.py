"""Tests for ``apme submit`` idempotency-key handling (replay path)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apme_engine.cli.gateway_client import GatewayClient
from apme_engine.cli.parser import build_parser
from apme_engine.cli.submit_cmd import run_submit
from apme_engine.submit_token import (
    SUBMIT_TOKEN_MAX_CHARS,
    SUBMIT_TOKEN_MIN_HEX_CHARS,
    generate_submit_token,
    is_valid_submit_token,
)


def _args(**overrides: object) -> argparse.Namespace:
    """Build a Namespace mimicking parsed ``submit`` args.

    Args:
        **overrides: Argument overrides.

    Returns:
        Namespace for ``run_submit``.
    """
    defaults: dict[str, object] = {
        "project_id": "proj-1",
        "branch": None,
        "no_pr": False,
        "activity_id": None,
        "idempotency_key": None,
        "gateway_url": None,
        "json": False,
        "no_ansi": False,
        "verbose": 0,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestSubmitTokenRules:
    """The token predicate shared by CLI and Gateway."""

    def test_generated_token_is_accepted(self) -> None:
        """A fresh uuid4 token satisfies the rule."""
        token = generate_submit_token()
        assert len(token) == 32
        assert is_valid_submit_token(token)

    def test_uuid_spelling_with_dashes_accepted(self) -> None:
        """Dashed UUIDs work; dashes are not counted as entropy."""
        assert is_valid_submit_token("12345678-1234-1234-1234-123456789012")

    def test_short_or_non_hex_rejected(self) -> None:
        """Short, guessable, or non-hex tokens are rejected client-side."""
        for token in ["", "abc", "a" * (SUBMIT_TOKEN_MIN_HEX_CHARS - 1), "z" * 40, "  ", "  " + "a" * 31 + "  "]:
            assert not is_valid_submit_token(token), token

    def test_over_limit_rejected(self) -> None:
        """Tokens longer than the bound are rejected even if all hex."""
        assert not is_valid_submit_token("a" * (SUBMIT_TOKEN_MAX_CHARS + 1))


class TestSubmitParser:
    """The CLI surface exposes the replay flag."""

    def test_parser_accepts_idempotency_key(self) -> None:
        """``--idempotency-key`` parses into ``args.idempotency_key``."""
        args = build_parser().parse_args(["submit", "proj-1", "--idempotency-key", "a" * 32])
        assert args.idempotency_key == "a" * 32

    def test_parser_default_is_none(self) -> None:
        """Without the flag the key stays unset so run_submit generates one."""
        args = build_parser().parse_args(["submit", "proj-1"])
        assert args.idempotency_key is None


class TestGatewayClientSubmit:
    """Client-side token plumbing."""

    @patch("apme_engine.cli.gateway_client.httpx.post")
    def test_caller_supplied_key_is_reused(self, mock_post: MagicMock) -> None:
        """The same key on a retry means a Gateway replay, not a second push.

        Args:
            mock_post: Patched ``httpx.post``.
        """
        response = MagicMock()
        response.json.return_value = {"branch_name": "apme/fix"}
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        token = "f" * 32
        GatewayClient(base_url="http://test:8080").submit_operation("project", idempotency_key=token)

        assert mock_post.call_args.kwargs["headers"]["Idempotency-Key"] == token

    def test_weak_key_rejected_before_sending(self) -> None:
        """A guessable key fails locally instead of consuming a Gateway round trip."""
        with patch("apme_engine.cli.gateway_client.httpx.post") as mock_post:
            with pytest.raises(ValueError, match="Invalid idempotency key"):
                GatewayClient(base_url="http://test:8080").submit_operation("project", idempotency_key="weak")
            mock_post.assert_not_called()


class TestRunSubmitReplayHint:
    """A failed submit reports the key needed to replay it."""

    def test_timeout_prints_replay_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Timeouts are the ambiguous case, so the key must be visible.

        Args:
            capsys: Capture fixture for stderr.
        """
        token = "a" * 32
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            client_cls.return_value.submit_operation.side_effect = httpx.ReadTimeout("slow")
            with pytest.raises(SystemExit):
                run_submit(_args(idempotency_key=token))
        err = capsys.readouterr().err
        assert f"--idempotency-key {token}" in err

    def test_service_unavailable_prints_replay_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A 503 after a partial attempt is replayable with the same key.

        Args:
            capsys: Capture fixture for stderr.
        """
        token = "b" * 32
        request = httpx.Request("POST", "http://test:8080/api/v1/projects/proj-1/operation/submit")
        response = httpx.Response(
            503, text=json.dumps({"detail": {"code": "busy", "message": "locked"}}), request=request
        )
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            client_cls.return_value.submit_operation.side_effect = httpx.HTTPStatusError(
                "busy", request=request, response=response
            )
            with pytest.raises(SystemExit):
                run_submit(_args(idempotency_key=token))
        assert f"--idempotency-key {token}" in capsys.readouterr().err

    def test_generated_key_is_passed_to_client(self) -> None:
        """Without a flag a fresh key is minted and handed to the client."""
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            client_cls.return_value.submit_operation.return_value = {"branch_name": "b", "commit_sha": "c"}
            run_submit(_args())
        key = client_cls.return_value.submit_operation.call_args.kwargs["idempotency_key"]
        assert is_valid_submit_token(key)

    def test_weak_flag_key_exits_without_calling_gateway(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An explicitly weak key fails fast with a usable message.

        Args:
            capsys: Capture fixture for stderr.
        """
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            with pytest.raises(SystemExit):
                run_submit(_args(idempotency_key="weak"))
            client_cls.return_value.submit_operation.assert_not_called()
        assert "is not usable" in capsys.readouterr().err


class TestSubmitTokenlessAndAmbiguous:
    """Tokenless opt-out and ambiguous-failure reporting."""

    @pytest.mark.parametrize("status", [500, 502, 504])  # type: ignore[untyped-decorator]
    def test_ambiguous_status_reports_replay_hint(self, status: int, capsys: pytest.CaptureFixture[str]) -> None:
        """A publish may have landed on 500/502/504, so the key is reported.

        Args:
            status: Gateway response status.
            capsys: Capture fixture for stderr.
        """
        token = "c" * 32
        request = httpx.Request("POST", "http://test:8080/api/v1/projects/proj-1/operation/submit")
        response = httpx.Response(status, text="upstream failed", request=request)
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            client_cls.return_value.submit_operation.side_effect = httpx.HTTPStatusError(
                "boom", request=request, response=response
            )
            with pytest.raises(SystemExit):
                run_submit(_args(idempotency_key=token))
        assert f"--idempotency-key {token}" in capsys.readouterr().err

    def test_no_idempotency_key_sends_no_header(self) -> None:
        """``--no-idempotency-key`` is the deliberate unclaimable path."""
        with (
            patch("apme_engine.cli.gateway_client.httpx.post") as mock_post,
            patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls,
        ):
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"branch_name": "b"})
            mock_post.return_value.raise_for_status = MagicMock()
            client_cls.return_value.submit_operation.return_value = {"branch_name": "b", "commit_sha": "c"}
            run_submit(_args(no_idempotency_key=True))
        assert client_cls.return_value.submit_operation.call_args.kwargs["tokenless"] is True
        assert client_cls.return_value.submit_operation.call_args.kwargs["idempotency_key"] is None

    def test_no_idempotency_key_conflicts_with_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Both flags together are ambiguous and rejected before any call.

        Args:
            capsys: Capture fixture for stderr.
        """
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            with pytest.raises(SystemExit):
                run_submit(_args(no_idempotency_key=True, idempotency_key="d" * 32))
            client_cls.return_value.submit_operation.assert_not_called()
        assert "cannot be combined" in capsys.readouterr().err

    def test_explicit_empty_key_is_rejected_not_regenerated(self, capsys: pytest.CaptureFixture[str]) -> None:
        """An explicit empty key must not be replaced by a fresh one.

        Regenerating would silently push a second branch instead of failing the
        invocation whose replay identity the caller meant to control.

        Args:
            capsys: Capture fixture for stderr.
        """
        with patch("apme_engine.cli.submit_cmd.GatewayClient") as client_cls:
            with pytest.raises(SystemExit):
                run_submit(_args(idempotency_key="   "))
            client_cls.return_value.submit_operation.assert_not_called()
        assert "is not usable" in capsys.readouterr().err

    def test_tokenless_client_sends_no_idempotency_header(self) -> None:
        """The client omits the header entirely for a tokenless submit."""
        with patch("apme_engine.cli.gateway_client.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"branch_name": "b"})
            mock_post.return_value.raise_for_status = MagicMock()
            GatewayClient(base_url="http://test:8080").submit_operation("project", tokenless=True)
        assert "Idempotency-Key" not in mock_post.call_args.kwargs["headers"]

    def test_tokenless_client_rejects_a_key(self) -> None:
        """Tokenless and a key are contradictory; the client refuses."""
        with pytest.raises(ValueError, match="tokenless submit cannot carry"):
            GatewayClient(base_url="http://test:8080").submit_operation(
                "project", idempotency_key="e" * 32, tokenless=True
            )

    def test_parser_exposes_no_idempotency_key(self) -> None:
        """The CLI flag exists and defaults to off."""
        parser = build_parser()
        args = parser.parse_args(["submit", "proj-1", "--no-idempotency-key"])
        assert args.no_idempotency_key is True
        assert parser.parse_args(["submit", "proj-1"]).no_idempotency_key is False
