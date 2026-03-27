"""Tests for the sbom CLI subcommand (CLI-01 through CLI-05).

Covers: parser registration, flag parsing, summary rendering, run_sbom handler
for stdout/file output, and gRPC error handling.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.cli.parser import build_parser


# ── Mock response objects ────────────────────────────────────────────


@dataclass
class MockComponentDetail:
    """Mimics SbomComponentDetail proto for testing."""

    type: str = "collection"
    name: str = ""
    version: str = ""
    license: str = ""
    name_inferred: bool = False
    version_missing: bool = False


@dataclass
class MockSbomResponse:
    """Mimics SbomResponse proto for testing."""

    sbom_json: bytes = b'{"bomFormat":"CycloneDX"}'
    collection_count: int = 0
    package_count: int = 0
    role_count: int = 0
    total_count: int = 0
    components: list[MockComponentDetail] = field(default_factory=list)


# ── Parser tests ─────────────────────────────────────────────────────


class TestParserSbom:
    """Parser registration and flag tests."""

    def test_parser_sbom_subcommand(self) -> None:
        """CLI-01: sbom subcommand is registered with target arg."""
        p = build_parser()
        args = p.parse_args(["sbom", "."])
        assert args.command == "sbom"
        assert args.target == "."

    def test_parser_sbom_default_target(self) -> None:
        """CLI-01: target defaults to '.' when omitted."""
        p = build_parser()
        args = p.parse_args(["sbom"])
        assert args.command == "sbom"
        assert args.target == "."

    def test_parser_sbom_with_output(self) -> None:
        """CLI-03: --output/-o flag parses correctly."""
        p = build_parser()
        args = p.parse_args(["sbom", ".", "-o", "out.json"])
        assert args.output == "out.json"

    def test_parser_sbom_with_session(self) -> None:
        """CLI-02: --session flag parses correctly."""
        p = build_parser()
        args = p.parse_args(["sbom", ".", "--session", "my-session"])
        assert args.session == "my-session"

    def test_parser_sbom_with_summary(self) -> None:
        """CLI-05: --summary flag sets True."""
        p = build_parser()
        args = p.parse_args(["sbom", "--summary"])
        assert args.summary is True

    def test_parser_sbom_with_refresh(self) -> None:
        """--refresh flag sets True."""
        p = build_parser()
        args = p.parse_args(["sbom", ".", "--refresh"])
        assert args.refresh is True

    def test_parser_sbom_verbose_from_global(self) -> None:
        """Verbose flag inherited from global_opts parent."""
        p = build_parser()
        args = p.parse_args(["sbom", "-v", "."])
        assert args.verbose == 1


# ── Summary rendering tests ─────────────────────────────────────────


class TestRenderSummary:
    """Tests for _render_summary output formatting."""

    def test_render_summary_counts_only(self) -> None:
        """CLI-05: Default summary is counts-only one-liner."""
        from apme_engine.cli.sbom_cmd import _render_summary

        resp = MockSbomResponse(
            collection_count=3,
            package_count=10,
            role_count=2,
            total_count=15,
        )
        buf = io.StringIO()
        _render_summary(resp, verbose=0, file=buf)
        output = buf.getvalue()
        assert "SBOM: 3 collections, 10 packages, 2 roles (15 total)" in output

    def test_render_summary_verbose(self) -> None:
        """CLI-05: Verbose summary shows grouped table with flags."""
        from apme_engine.cli.sbom_cmd import _render_summary

        resp = MockSbomResponse(
            collection_count=1,
            package_count=1,
            role_count=1,
            total_count=3,
            components=[
                MockComponentDetail(
                    type="collection",
                    name="community.general",
                    version="9.0.0",
                    license="GPL-3.0-or-later",
                ),
                MockComponentDetail(
                    type="package",
                    name="ansible-core",
                    version="2.18.0",
                    license="Apache-2.0",
                ),
                MockComponentDetail(
                    type="role",
                    name="my_role",
                    version="",
                    license="",
                    name_inferred=True,
                    version_missing=True,
                ),
            ],
        )
        buf = io.StringIO()
        _render_summary(resp, verbose=1, file=buf)
        output = buf.getvalue()
        assert "Collections (1):" in output
        assert "Packages (1):" in output
        assert "Roles (1):" in output
        assert "community.general" in output
        assert "9.0.0" in output
        assert "GPL-3.0-or-later" in output
        assert "my_role [inferred]" in output
        # version_missing shows "-"
        lines = output.split("\n")
        role_line = [l for l in lines if "my_role" in l][0]
        # version column should show "-"
        assert "-" in role_line


# ── run_sbom handler tests ───────────────────────────────────────────


class TestRunSbom:
    """Tests for the run_sbom handler function."""

    def _make_args(self, **overrides: object) -> MagicMock:
        """Create mock args namespace with defaults."""
        args = MagicMock()
        args.command = "sbom"
        args.target = "."
        args.output = None
        args.session = "test-session"
        args.summary = False
        args.refresh = False
        args.verbose = 0
        args.no_ansi = False
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    @patch("apme_engine.cli.sbom_cmd.resolve_primary")
    @patch("apme_engine.cli.sbom_cmd.yield_scan_chunks")
    @patch("apme_engine.cli.sbom_cmd._resolve_session_id", return_value="test-session")
    def test_run_sbom_stdout(
        self,
        mock_session: MagicMock,
        mock_chunks: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """CLI-04: SBOM JSON written to stdout when no --output."""
        from apme_engine.cli.sbom_cmd import run_sbom

        sbom_bytes = b'{"bomFormat":"CycloneDX","specVersion":"1.5"}'
        mock_response = MockSbomResponse(sbom_json=sbom_bytes, total_count=5)
        mock_stub = MagicMock()
        mock_stub.GenerateSbom.return_value = mock_response
        mock_channel = MagicMock()
        mock_resolve.return_value = (mock_channel, "localhost:50051")
        mock_chunks.return_value = iter([MagicMock(options=MagicMock())])

        # Patch PrimaryStub to return our mock; use a mock stdout with buffer
        mock_stdout = MagicMock()
        buf = io.BytesIO()
        mock_stdout.buffer = buf
        mock_stdout.write = MagicMock()  # for stderr summary render

        with patch("apme_engine.cli.sbom_cmd.primary_pb2_grpc.PrimaryStub", return_value=mock_stub):
            with patch("apme_engine.cli.sbom_cmd.sys") as mock_sys:
                mock_sys.stdout = mock_stdout
                mock_sys.stderr = MagicMock()
                mock_sys.exit = sys.exit
                run_sbom(self._make_args())

        output = buf.getvalue()
        assert sbom_bytes in output

    @patch("apme_engine.cli.sbom_cmd.resolve_primary")
    @patch("apme_engine.cli.sbom_cmd.yield_scan_chunks")
    @patch("apme_engine.cli.sbom_cmd._resolve_session_id", return_value="test-session")
    def test_run_sbom_output_file(
        self,
        mock_session: MagicMock,
        mock_chunks: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
    ) -> None:
        """CLI-03: SBOM JSON written to file when --output specified."""
        from apme_engine.cli.sbom_cmd import run_sbom

        sbom_bytes = b'{"bomFormat":"CycloneDX"}'
        mock_response = MockSbomResponse(sbom_json=sbom_bytes)
        mock_stub = MagicMock()
        mock_stub.GenerateSbom.return_value = mock_response
        mock_channel = MagicMock()
        mock_resolve.return_value = (mock_channel, "localhost:50051")
        mock_chunks.return_value = iter([MagicMock(options=MagicMock())])

        out_file = tmp_path / "sbom.json"

        with patch("apme_engine.cli.sbom_cmd.primary_pb2_grpc.PrimaryStub", return_value=mock_stub):
            run_sbom(self._make_args(output=str(out_file)))

        assert out_file.read_bytes() == sbom_bytes

    @patch("apme_engine.cli.sbom_cmd.resolve_primary")
    @patch("apme_engine.cli.sbom_cmd.yield_scan_chunks")
    @patch("apme_engine.cli.sbom_cmd._resolve_session_id", return_value="test-session")
    def test_run_sbom_grpc_error(
        self,
        mock_session: MagicMock,
        mock_chunks: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """INT-01: gRPC error produces exit code 1 and stderr message."""
        from apme_engine.cli.sbom_cmd import run_sbom

        mock_stub = MagicMock()
        rpc_error = MagicMock(spec=["details"])
        rpc_error.details.return_value = "session not found"
        # Create a proper RpcError subclass
        error = type("TestRpcError", (Exception,), {"details": rpc_error.details})()
        # Make it also a grpc.RpcError
        import grpc as _grpc

        error.__class__ = type("TestRpcError", (_grpc.RpcError,), {"details": rpc_error.details})
        mock_stub.GenerateSbom.side_effect = error

        mock_channel = MagicMock()
        mock_resolve.return_value = (mock_channel, "localhost:50051")
        mock_chunks.return_value = iter([MagicMock(options=MagicMock())])

        with patch("apme_engine.cli.sbom_cmd.primary_pb2_grpc.PrimaryStub", return_value=mock_stub):
            with pytest.raises(SystemExit) as exc_info:
                run_sbom(self._make_args())
            assert exc_info.value.code == 1

    @patch("apme_engine.cli.sbom_cmd.resolve_primary")
    @patch("apme_engine.cli.sbom_cmd._resolve_session_id", return_value="test-session")
    def test_run_sbom_summary_only(
        self,
        mock_session: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """CLI-05: --summary calls GenerateSbom with summary_only and renders table."""
        from apme_engine.cli.sbom_cmd import run_sbom

        mock_response = MockSbomResponse(
            collection_count=2,
            package_count=5,
            role_count=1,
            total_count=8,
            components=[
                MockComponentDetail(type="collection", name="community.general", version="9.0.0"),
            ],
        )
        mock_stub = MagicMock()
        mock_stub.GenerateSbom.return_value = mock_response
        mock_channel = MagicMock()
        mock_resolve.return_value = (mock_channel, "localhost:50051")

        with patch("apme_engine.cli.sbom_cmd.primary_pb2_grpc.PrimaryStub", return_value=mock_stub):
            # Capture stdout
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                run_sbom(self._make_args(summary=True))

        output = buf.getvalue()
        assert "Collections" in output
        assert "community.general" in output
