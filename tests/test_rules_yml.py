"""Tests for CLI ``.apme/rules.yml`` loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.cli._rules_yml import RULES_YML_PATH, load_rule_configs_from_project
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.severity_defaults import Severity


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing ``.apme/rules.yml`` returns an empty config list.

    Args:
        tmp_path: Pytest temporary directory.
    """
    assert load_rule_configs_from_project(tmp_path) == []


def test_loads_rules(tmp_path: Path) -> None:
    """Parses enabled/disabled rules with severity and enforced flags.

    Args:
        tmp_path: Pytest temporary directory.
    """
    apme = tmp_path / ".apme"
    apme.mkdir()
    (apme / "rules.yml").write_text(
        """
rules:
  L026:
    enabled: false
  L047:
    severity: critical
    enforced: true
""",
        encoding="utf-8",
    )
    cfgs = load_rule_configs_from_project(tmp_path)
    assert len(cfgs) == 2

    by_id = {c.rule_id: c for c in cfgs}
    assert by_id["L026"].enabled is False
    assert by_id["L026"].severity == 0

    assert by_id["L047"].enabled is True
    assert by_id["L047"].enforced is True
    assert by_id["L047"].severity == int(Severity.CRITICAL)


def test_yaml_error_warns_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Malformed YAML prints a warning and returns empty.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest capture fixture for stderr inspection.
    """
    apme = tmp_path / ".apme"
    apme.mkdir()
    (apme / "rules.yml").write_text("{ not valid", encoding="utf-8")
    assert load_rule_configs_from_project(tmp_path) == []
    err = capsys.readouterr().err
    assert "Warning" in err
    assert "rules.yml" in err


def test_severity_unspecified_warns_and_skips(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``severity: unspecified`` warns and does not set severity on the proto.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest capture fixture for stderr inspection.
    """
    apme = tmp_path / ".apme"
    apme.mkdir()
    (apme / "rules.yml").write_text(
        "rules:\n  L026:\n    severity: unspecified\n",
        encoding="utf-8",
    )
    cfgs = load_rule_configs_from_project(tmp_path)
    assert len(cfgs) == 1
    assert cfgs[0].severity == 0
    err = capsys.readouterr().err
    assert "has no effect" in err


def test_rules_key_optional_returns_empty(tmp_path: Path) -> None:
    """YAML without a ``rules`` key returns empty without error.

    Args:
        tmp_path: Pytest temporary directory.
    """
    apme = tmp_path / ".apme"
    apme.mkdir()
    (apme / "rules.yml").write_text("other: 1\n", encoding="utf-8")
    assert load_rule_configs_from_project(tmp_path) == []


def test_rules_yml_path_constant() -> None:
    """``RULES_YML_PATH`` matches the expected ``.apme/rules.yml``."""
    assert Path(".apme") / "rules.yml" == RULES_YML_PATH


def test_yield_scan_chunks_includes_rule_configs_on_scan_options(tmp_path: Path) -> None:
    """``yield_scan_chunks`` populates ``ScanOptions.rule_configs`` from parsed configs.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "site.yml").write_text("---\n- hosts: localhost\n", encoding="utf-8")
    apme = tmp_path / ".apme"
    apme.mkdir()
    (apme / "rules.yml").write_text(
        "rules:\n  L026:\n    enabled: false\n",
        encoding="utf-8",
    )
    cfgs = load_rule_configs_from_project(tmp_path)
    chunks = list(yield_scan_chunks(tmp_path, rule_configs=cfgs))
    assert chunks
    opts0 = chunks[0].options
    assert opts0 is not None
    assert len(opts0.rule_configs) == 1
    assert opts0.rule_configs[0].rule_id == "L026"
    assert opts0.rule_configs[0].enabled is False
