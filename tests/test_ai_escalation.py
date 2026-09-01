"""Tests for AI escalation: AISkipped, discover_abbenay, JSON extraction, best practices."""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from apme.v1.engine_pb2 import FixOptions
from apme_engine.daemon.engine_server import EngineServicer
from apme_engine.remediation.abbenay_provider import (
    _build_node_prompt,
    _build_validation_prompt,
    _extract_json_object,
    _get_best_practices_for_rules,
    _load_ai_prompts,
    _load_best_practices,
    discover_abbenay,
    make_abbenay_client,
)
from apme_engine.remediation.ai_context import AINodeContext
from apme_engine.remediation.ai_provider import (
    AISkipped,
)

# ---------------------------------------------------------------------------
# AISkipped tests
# ---------------------------------------------------------------------------


class TestAISkipped:
    """Tests for the AISkipped dataclass."""

    def test_create_skipped(self) -> None:
        """AISkipped fields are set correctly."""
        s = AISkipped(
            rule_id="P002",
            line=45,
            reason="Cannot determine valid params for custom module.",
            suggestion="Remove 'invalid_param' if not valid.",
        )
        assert s.rule_id == "P002"
        assert s.line == 45
        assert "custom module" in s.reason
        assert "invalid_param" in s.suggestion


# ---------------------------------------------------------------------------
# discover_abbenay tests
# ---------------------------------------------------------------------------


class TestDiscoverAbbenay:
    """Tests for Abbenay daemon auto-discovery."""

    def test_discover_from_xdg(self, tmp_path: Path) -> None:
        """Discovers socket via XDG_RUNTIME_DIR.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sock_dir = tmp_path / "abbenay"
        sock_dir.mkdir()
        sock_file = sock_dir / "daemon.sock"
        sock_file.touch()

        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp_path)}):
            result = discover_abbenay()

        assert result == f"unix://{sock_file}"

    def test_discover_from_tmp(self, tmp_path: Path) -> None:
        """Falls back to /tmp/abbenay/daemon.sock when XDG and /run/user paths miss.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sock_dir = tmp_path / "abbenay"
        sock_dir.mkdir(parents=True)
        sock_file = sock_dir / "daemon.sock"
        sock_file.touch()

        orig_path = Path

        def _path_factory(*args: object, **kwargs: object) -> Path:
            if args == ("/tmp/abbenay/daemon.sock",):
                return sock_file
            if (
                len(args) == 1
                and isinstance(args[0], str)
                and "/run/user/" in args[0]
                and args[0].endswith("/abbenay/daemon.sock")
            ):
                return orig_path(tmp_path / "no-run-user-sock" / "daemon.sock")
            return orig_path(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "apme_engine.remediation.abbenay_provider.Path",
                side_effect=_path_factory,
            ),
        ):
            result = discover_abbenay()

        assert result == f"unix://{sock_file}"

    def test_discover_returns_none(self, tmp_path: Path) -> None:
        """Returns None when no socket exists.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        orig_path = Path

        def _path_factory(*args: object, **kwargs: object) -> Path:
            if len(args) == 1 and isinstance(args[0], str) and args[0].endswith("/abbenay/daemon.sock"):
                return orig_path(tmp_path / "missing" / "daemon.sock")
            return orig_path(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp_path)}),
            patch(
                "apme_engine.remediation.abbenay_provider.Path",
                side_effect=_path_factory,
            ),
        ):
            result = discover_abbenay()

        assert result is None


class TestMakeAbbenayClient:
    """Tests for unix:// vs host:port client construction."""

    def test_unix_addr_uses_socket_path_kwarg(self) -> None:
        """unix:// URIs are passed as a bare socket_path, not a positional URI."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            client = make_abbenay_client("unix:///tmp/abbenay-run/abbenay/daemon.sock")

        assert isinstance(client, _Client)
        assert client.args == ()
        assert client.kwargs == {"socket_path": "/tmp/abbenay-run/abbenay/daemon.sock"}

    def test_tcp_addr_uses_host_port(self) -> None:
        """host:port URIs use host and port kwargs."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            client = make_abbenay_client("127.0.0.1:50057")

        assert isinstance(client, _Client)
        assert client.args == ()
        assert client.kwargs == {"host": "127.0.0.1", "port": 50057}

    def test_port_only_addr_defaults_localhost(self) -> None:
        """``:port`` is localhost, matching the old ListAIModels shorthand."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            client = make_abbenay_client(":50057")

        assert isinstance(client, _Client)
        assert client.kwargs == {"host": "localhost", "port": 50057}

    def test_unbracketed_ipv6_is_host_only(self) -> None:
        """Bare IPv6 is not split on colons."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            client = make_abbenay_client("::1")

        assert isinstance(client, _Client)
        assert client.kwargs == {"host": "::1"}

    def test_bracketed_ipv6_with_port(self) -> None:
        """``[ipv6]:port`` splits host and port."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            client = make_abbenay_client("[::1]:50057")

        assert isinstance(client, _Client)
        assert client.kwargs == {"host": "::1", "port": 50057}

    def test_invalid_port_raises_value_error(self) -> None:
        """Empty or non-numeric ports raise ValueError instead of crashing later."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"abbenay_grpc": stub}):
            with pytest.raises(ValueError, match="invalid Abbenay port"):
                make_abbenay_client("[::1]:")
            with pytest.raises(ValueError, match="invalid Abbenay port"):
                make_abbenay_client("[::1]:abc")
            with pytest.raises(ValueError, match="invalid Abbenay port"):
                make_abbenay_client("127.0.0.1:abc")


class TestResolveAiProvider:
    """Invalid APME_ABBENAY_ADDR must not crash FixSession setup."""

    def test_invalid_tcp_addr_returns_none(self) -> None:
        """ValueError from a bad port degrades to no AI provider."""

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        stub = types.ModuleType("abbenay_grpc")
        stub.AbbenayClient = _Client  # type: ignore[attr-defined]
        opts = FixOptions(enable_ai=True, ai_model="openai/gpt-4o")
        with (
            patch.dict(sys.modules, {"abbenay_grpc": stub}),
            patch.dict(os.environ, {"APME_ABBENAY_ADDR": "[::1]:abc"}),
        ):
            result = EngineServicer._resolve_ai_provider(opts)
        assert result is None


# ---------------------------------------------------------------------------
# _extract_json_object tests
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    """Tests for _extract_json_object which handles LLM preamble stripping."""

    def test_clean_json(self) -> None:
        """Parses clean JSON directly."""
        data = _extract_json_object('{"patches": []}')
        assert data == {"patches": []}

    def test_markdown_fences(self) -> None:
        """Strips markdown code fences."""
        data = _extract_json_object('```json\n{"patches": []}\n```')
        assert data == {"patches": []}

    def test_thinking_preamble(self) -> None:
        """Strips reasoning text before the JSON object."""
        text = (
            "Looking at the violations, I need to analyze the task context.\n\n"
            '{"patches": [{"rule_id": "M001", "line_start": 1, "line_end": 1, '
            '"fixed_lines": "fixed\\n", "explanation": "ok", "confidence": 0.9}]}'
        )
        data = _extract_json_object(text)
        assert data is not None
        assert len(data["patches"]) == 1
        assert data["patches"][0]["rule_id"] == "M001"

    def test_trailing_text(self) -> None:
        """Ignores text after the JSON object."""
        text = '{"patches": []} \n\nLet me know if you need anything else.'
        data = _extract_json_object(text)
        assert data == {"patches": []}

    def test_preamble_and_trailing(self) -> None:
        """Strips both preamble and trailing text."""
        text = 'Here is the fix:\n{"skipped": []}\nHope that helps!'
        data = _extract_json_object(text)
        assert data == {"skipped": []}

    def test_nested_braces(self) -> None:
        """Handles nested objects correctly."""
        inner = json.dumps(
            {
                "patches": [
                    {
                        "rule_id": "L026",
                        "line_start": 1,
                        "line_end": 2,
                        "fixed_lines": "- name: test\n",
                        "explanation": "ok",
                        "confidence": 0.9,
                    }
                ]
            }
        )
        text = f"Analysis complete.\n{inner}\nDone."
        data = _extract_json_object(text)
        assert data is not None
        assert data["patches"][0]["rule_id"] == "L026"

    def test_no_json(self) -> None:
        """Returns None when no JSON object is found."""
        assert _extract_json_object("no json here at all") is None

    def test_braces_in_strings(self) -> None:
        """Does not split on braces inside JSON string values."""
        text = '{"patches": [], "note": "use {item} syntax"}'
        data = _extract_json_object(text)
        assert data is not None
        assert data["note"] == "use {item} syntax"

    def test_empty_response(self) -> None:
        """Returns None for empty input."""
        assert _extract_json_object("") is None
        assert _extract_json_object("   ") is None


# ---------------------------------------------------------------------------
# Best practices tests
# ---------------------------------------------------------------------------


class TestBestPractices:
    """Tests for best practices mapping loading."""

    def test_load_best_practices(self) -> None:
        """Best practices YAML loads successfully."""
        bp = _load_best_practices()
        assert "universal" in bp
        assert "fqcn" in bp
        assert len(bp["universal"]) > 5

    def test_get_best_practices_for_fqcn(self) -> None:
        """Returns FQCN-specific practices for M001."""
        result = _get_best_practices_for_rules(["M001"])
        assert "FQCN" in result

    def test_get_best_practices_for_unknown_rule(self) -> None:
        """Returns universal practices for unknown rules."""
        result = _get_best_practices_for_rules(["UNKNOWN999"])
        assert "idempotent" in result.lower() or "YAML" in result

    def test_get_best_practices_for_multiple_rules(self) -> None:
        """Returns combined practices for multiple rule categories."""
        result = _get_best_practices_for_rules(["M001", "L011"])
        assert "FQCN" in result


# ---------------------------------------------------------------------------
# Per-rule AI prompt hint tests
# ---------------------------------------------------------------------------


class TestLoadAiPrompts:
    """Tests for _load_ai_prompts() frontmatter parsing and prompt injection."""

    def test_loads_from_real_rule_docs(self) -> None:
        """Loads ai_prompt hints from seeded rule docs."""
        _load_ai_prompts.cache_clear()
        prompts = _load_ai_prompts()
        assert "R108" in prompts
        assert "privilege" in prompts["R108"].lower()
        assert "R101" in prompts
        assert "M006" in prompts
        _load_ai_prompts.cache_clear()

    def test_loads_from_temp_dir(self, tmp_path: Path) -> None:
        """Parses ai_prompt from a synthetic rule doc in a temp directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        rule_md = tmp_path / "T999_test.md"
        rule_md.write_text(
            "---\nrule_id: T999\nai_prompt: |\n  Test hint for T999.\n---\n# T999\n",
            encoding="utf-8",
        )
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            prompts = _load_ai_prompts()
        assert prompts == {"T999": "Test hint for T999."}
        _load_ai_prompts.cache_clear()

    def test_skips_missing_ai_prompt(self, tmp_path: Path) -> None:
        """Rules without ai_prompt are not included in the map.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        rule_md = tmp_path / "L999.md"
        rule_md.write_text(
            "---\nrule_id: L999\ndescription: no hint\n---\n",
            encoding="utf-8",
        )
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            prompts = _load_ai_prompts()
        assert "L999" not in prompts
        _load_ai_prompts.cache_clear()

    def test_bad_yaml_logged_and_skipped(self, tmp_path: Path) -> None:
        """Malformed frontmatter is warned and skipped.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        rule_md = tmp_path / "BAD.md"
        rule_md.write_text("---\n: : :\n---\n", encoding="utf-8")
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            prompts = _load_ai_prompts()
        assert prompts == {}
        _load_ai_prompts.cache_clear()

    def test_node_prompt_includes_guidance(self, tmp_path: Path) -> None:
        """Rule guidance section appears in the node prompt when ai_prompt exists.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        rule_md = tmp_path / "R999.md"
        rule_md.write_text(
            "---\nrule_id: R999\nai_prompt: |\n  Custom guidance.\n---\n",
            encoding="utf-8",
        )
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            ctx = AINodeContext(
                node_id="task-1",
                node_type="task",
                file_path="test.yml",
                yaml_lines="- name: test\n  ansible.builtin.debug:\n    msg: hi",
                violations=[{"rule_id": "R999", "message": "test violation"}],
            )
            prompt = _build_node_prompt(ctx)
        assert "Rule-Specific Guidance" in prompt
        assert "Custom guidance." in prompt
        _load_ai_prompts.cache_clear()

    def test_validation_prompt_includes_guidance(self, tmp_path: Path) -> None:
        """Rule guidance section appears in the validation prompt.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        rule_md = tmp_path / "R888.md"
        rule_md.write_text(
            "---\nrule_id: R888\nai_prompt: |\n  Validate carefully.\n---\n",
            encoding="utf-8",
        )
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            ctx = AINodeContext(
                node_id="task-2",
                node_type="task",
                file_path="test.yml",
                yaml_lines="- name: test\n  ansible.builtin.command: whoami",
                violations=[{"rule_id": "R888", "message": "test finding"}],
            )
            prompt = _build_validation_prompt(ctx)
        assert "Rule-Specific Guidance" in prompt
        assert "Validate carefully." in prompt
        _load_ai_prompts.cache_clear()

    def test_no_guidance_when_no_hints(self, tmp_path: Path) -> None:
        """No guidance section when no rules have ai_prompt.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        _load_ai_prompts.cache_clear()
        with patch(
            "apme_engine.remediation.abbenay_provider._RULE_DOC_DIRS",
            [tmp_path],
        ):
            ctx = AINodeContext(
                node_id="task-3",
                node_type="task",
                file_path="test.yml",
                yaml_lines="- name: test\n  ansible.builtin.debug:\n    msg: hi",
                violations=[{"rule_id": "ZZZZ", "message": "unknown rule"}],
            )
            prompt = _build_node_prompt(ctx)
        assert "Rule-Specific Guidance" not in prompt
        _load_ai_prompts.cache_clear()
