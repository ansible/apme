"""Tests for ``apme sbom`` CLI subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apme_engine.cli.gateway_client import GatewayClient
from apme_engine.cli.sbom_cmd import run_sbom

# ── GatewayClient ──────────────────────────────────────────────


class TestGatewayClient:
    """Unit tests for GatewayClient."""

    def test_default_base_url(self) -> None:
        """Falls back to localhost:8080 when no env or arg is set."""
        with patch.dict("os.environ", {}, clear=True):
            client = GatewayClient()
        assert client.base_url == "http://localhost:8080"

    def test_env_var_overrides_default(self) -> None:
        """APME_GATEWAY_URL env var takes precedence over the default."""
        with patch.dict("os.environ", {"APME_GATEWAY_URL": "http://gw:9090"}):
            client = GatewayClient()
        assert client.base_url == "http://gw:9090"

    def test_explicit_url_overrides_env(self) -> None:
        """Explicit base_url argument wins over env var."""
        with patch.dict("os.environ", {"APME_GATEWAY_URL": "http://gw:9090"}):
            client = GatewayClient(base_url="http://custom:1234")
        assert client.base_url == "http://custom:1234"

    @patch("apme_engine.cli.gateway_client.httpx.get")
    def test_get_sbom_success(self, mock_get: MagicMock) -> None:
        """Successful SBOM fetch returns parsed JSON.

        Args:
            mock_get: Patched ``httpx.get``.
        """
        expected = {"bomFormat": "CycloneDX", "specVersion": "1.5"}
        mock_resp = MagicMock()
        mock_resp.json.return_value = expected
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client = GatewayClient(base_url="http://test:8080")
        result = client.get_sbom("my-project")

        assert result == expected
        mock_get.assert_called_once_with(
            "http://test:8080/api/v1/projects/my-project/sbom",
            params={"format": "cyclonedx"},
            timeout=30,
        )

    @patch("apme_engine.cli.gateway_client.httpx.get")
    def test_get_sbom_404_raises(self, mock_get: MagicMock) -> None:
        """HTTPStatusError propagates when project is not found.

        Args:
            mock_get: Patched ``httpx.get``.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_resp,
        )
        mock_get.return_value = mock_resp

        client = GatewayClient(base_url="http://test:8080")
        with pytest.raises(httpx.HTTPStatusError):
            client.get_sbom("missing")


# ── run_sbom ───────────────────────────────────────────────────


def _make_args(**kwargs: int | bool | str | None) -> argparse.Namespace:
    """Build a Namespace mimicking parsed CLI args.

    Args:
        **kwargs: Overrides for default argument values.

    Returns:
        argparse.Namespace ready for ``run_sbom``.
    """
    defaults: dict[str, int | bool | str | None] = {
        "project_id": "test-project",
        "format": "cyclonedx",
        "output": None,
        "gateway_url": None,
        "no_ansi": False,
        "verbose": 0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestRunSbom:
    """Tests for the run_sbom CLI entry point."""

    @patch("apme_engine.cli.sbom_cmd.GatewayClient")
    def test_prints_json_to_stdout(
        self,
        mock_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """SBOM JSON is printed to stdout when no --output is given.

        Args:
            mock_cls: Patched ``GatewayClient`` class.
            capsys: Pytest capture fixture.
        """
        bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}
        mock_cls.return_value.get_sbom.return_value = bom

        run_sbom(_make_args())

        captured = capsys.readouterr()
        assert json.loads(captured.out) == bom

    @patch("apme_engine.cli.sbom_cmd.GatewayClient")
    def test_writes_to_file(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        """SBOM is written to a file when --output is specified.

        Args:
            mock_cls: Patched ``GatewayClient`` class.
            tmp_path: Pytest-provided temporary directory.
        """
        bom = {"bomFormat": "CycloneDX"}
        mock_cls.return_value.get_sbom.return_value = bom

        outfile = tmp_path / "sbom.json"
        run_sbom(_make_args(output=str(outfile)))

        assert outfile.exists()
        assert json.loads(outfile.read_text()) == bom

    @patch("apme_engine.cli.sbom_cmd.GatewayClient")
    def test_http_error_exits(self, mock_cls: MagicMock) -> None:
        """HTTPStatusError causes sys.exit(1).

        Args:
            mock_cls: Patched ``GatewayClient`` class.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Project not found"
        mock_cls.return_value.get_sbom.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_resp,
        )

        with pytest.raises(SystemExit) as exc_info:
            run_sbom(_make_args())
        assert exc_info.value.code == 1

    @patch("apme_engine.cli.sbom_cmd.GatewayClient")
    def test_connect_error_exits(self, mock_cls: MagicMock) -> None:
        """ConnectError causes sys.exit(1).

        Args:
            mock_cls: Patched ``GatewayClient`` class.
        """
        mock_cls.return_value.get_sbom.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(SystemExit) as exc_info:
            run_sbom(_make_args())
        assert exc_info.value.code == 1

    @patch("apme_engine.cli.sbom_cmd.GatewayClient")
    def test_gateway_url_arg_used(self, mock_cls: MagicMock) -> None:
        """``--gateway-url`` is forwarded to GatewayClient.

        Args:
            mock_cls: Patched ``GatewayClient`` class.
        """
        mock_cls.return_value.get_sbom.return_value = {}

        run_sbom(_make_args(gateway_url="http://custom:9999"))

        mock_cls.assert_called_once_with(base_url="http://custom:9999")


class TestBuildLocalSbom:
    """Tests for the local ``--path`` manifest SBOM builder (#4)."""

    def test_missing_dir_exits_2(self, tmp_path: Path) -> None:
        """Nonexistent --path exits 2.

        Args:
            tmp_path: Pytest temp dir.
        """
        from apme_engine.cli.sbom_cmd import _build_local_sbom

        with pytest.raises(SystemExit) as exc_info:
            _build_local_sbom(tmp_path / "nope")
        assert exc_info.value.code == 2

    def test_file_path_exits_2(self, tmp_path: Path) -> None:
        """File --path exits 2 instead of scanning its parent.

        Args:
            tmp_path: Pytest temp dir.
        """
        from apme_engine.cli.sbom_cmd import _build_local_sbom

        target = tmp_path / "site.yml"
        target.write_text("- hosts: all\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            _build_local_sbom(target)
        assert exc_info.value.code == 2

    def test_requirements_yml_str_and_dict_entries(self, tmp_path: Path) -> None:
        """String entries get unknown version; dict entries keep name/version.

        Args:
            tmp_path: Pytest temp dir.
        """
        from typing import cast

        from apme_engine.cli.sbom_cmd import _build_local_sbom

        (tmp_path / "requirements.yml").write_text(
            "collections:\n  - ns.plain\n  - name: ns.pinned\n    version: 1.2.3\n",
            encoding="utf-8",
        )
        bom = _build_local_sbom(tmp_path)
        components = cast(list[dict[str, object]], bom["components"])
        by_name = {c["name"]: c for c in components}
        assert by_name["ns.plain"]["version"] == "unknown"
        assert by_name["ns.pinned"]["version"] == "1.2.3"

    def test_private_source_preserved_in_purl(self, tmp_path: Path) -> None:
        """Dict entries with source keep their repository identity (#N0).

        Args:
            tmp_path: Pytest temp dir.
        """
        from typing import cast

        from apme_engine.cli.sbom_cmd import _build_local_sbom

        (tmp_path / "requirements.yml").write_text(
            "collections:\n  - name: acme.widget\n    version: 2.0.0\n"
            "    source: https://automation.example.com/api/galaxy/\n",
            encoding="utf-8",
        )
        bom = _build_local_sbom(tmp_path)
        components = cast(list[dict[str, object]], bom["components"])
        assert len(components) == 1
        assert components[0]["purl"] == (
            "pkg:generic/acme.widget@2.0.0?repository_url=https://automation.example.com/api/galaxy/"
        )

    def test_malformed_yaml_continues(self, tmp_path: Path) -> None:
        """Unparseable requirements.yml is skipped, not fatal.

        Args:
            tmp_path: Pytest temp dir.
        """
        from apme_engine.cli.sbom_cmd import _build_local_sbom

        (tmp_path / "requirements.yml").write_text("collections: [unclosed\n", encoding="utf-8")
        bom = _build_local_sbom(tmp_path)
        assert bom["bomFormat"] == "CycloneDX"
        assert bom["components"] == []

    def test_requirements_txt_core_pin_and_dedup(self, tmp_path: Path) -> None:
        """ansible-core pin surfaces once as the framework component.

        Args:
            tmp_path: Pytest temp dir.
        """
        from apme_engine.cli.sbom_cmd import _build_local_sbom

        (tmp_path / "requirements.txt").write_text(
            "ansible-core==2.17.0\nansible-core==2.17.0\nrequests==2.31.0\n",
            encoding="utf-8",
        )
        from typing import cast

        bom = _build_local_sbom(tmp_path)
        components = cast(list[dict[str, object]], bom["components"])
        names = [c["name"] for c in components]
        assert names.count("ansible-core") == 1
        assert components[0]["type"] == "framework"
        props = cast(list[dict[str, object]], bom["properties"])
        assert any(p["name"] == "apme:sbom-source" for p in props)
