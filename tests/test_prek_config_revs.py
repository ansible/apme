"""Unit tests for scripts/prek_config_revs.py (prek/pre-commit rev sync)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = REPO_ROOT / "scripts" / "prek_config_revs.py"


def _load_module() -> ModuleType:
    """Load ``scripts/prek_config_revs.py`` as a module.

    Returns:
        Loaded module object.
    """
    spec = importlib.util.spec_from_file_location("prek_config_revs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def mod() -> ModuleType:
    """Loaded prek_config_revs module.

    Returns:
        Imported ``prek_config_revs`` module.
    """
    return _load_module()


_SAMPLE_TOML = """\
[[repos]]
repo = "https://github.com/astral-sh/ruff-pre-commit"
rev = "v0.15.5"
hooks = [{ id = "ruff" }]

[[repos]]
repo = "local"
hooks = [{ id = "noop", name = "noop", language = "fail", entry = "true" }]
"""

_SAMPLE_YAML = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.5
    hooks:
      - id: ruff
  - repo: local
    hooks:
      - id: noop
        name: noop
        language: fail
        entry: true
"""


def test_load_revs_from_lines_skips_local(mod: ModuleType) -> None:
    """Line parser extracts remote revs and ignores local repos.

    Args:
        mod: Loaded ``prek_config_revs`` module.
    """
    revs = mod._load_revs_from_lines(
        _SAMPLE_TOML,
        repo_re=mod._TOML_REPO_RE,
        rev_re=mod._TOML_REV_RE,
    )
    assert revs == {"https://github.com/astral-sh/ruff-pre-commit": "v0.15.5"}


def test_compare_shared_revs_detects_drift(mod: ModuleType) -> None:
    """Mismatched shared revs are reported; matching ones are not.

    Args:
        mod: Loaded ``prek_config_revs`` module.
    """
    mismatches = mod.compare_shared_revs(
        {"https://example.com/a": "1", "https://example.com/b": "2"},
        {"https://example.com/a": "1", "https://example.com/b": "9"},
    )
    assert mismatches == [("https://example.com/b", "2", "9")]


def test_replace_revs_in_text_updates_toml(mod: ModuleType) -> None:
    """Rewrite only matching TOML repo rev pins.

    Args:
        mod: Loaded ``prek_config_revs`` module.
    """
    updated, n = mod._replace_revs_in_text(
        _SAMPLE_TOML,
        repo_re=mod._TOML_REPO_RE,
        rev_re=mod._TOML_REV_RE,
        target_revs={"https://github.com/astral-sh/ruff-pre-commit": "v9.9.9"},
        rev_formatter='rev = "{rev}"',
    )
    assert n == 1
    assert 'rev = "v9.9.9"' in updated


def test_load_yaml_revs_non_mapping_falls_back(mod: ModuleType, tmp_path: Path) -> None:
    """Non-mapping YAML roots do not crash; line parser still finds repos.

    Args:
        mod: Loaded ``prek_config_revs`` module.
        tmp_path: Temporary directory for fixture files.
    """
    path = tmp_path / "cfg.yaml"
    path.write_text("- not: a mapping\n", encoding="utf-8")
    # No repo/rev lines → empty map (and no AttributeError on .get).
    assert mod.load_yaml_revs(path) == {}

    path.write_text(_SAMPLE_YAML, encoding="utf-8")
    assert mod.load_yaml_revs(path) == {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.15.5",
    }


def test_load_yaml_revs_yaml_error_falls_back(mod: ModuleType, tmp_path: Path) -> None:
    """Invalid YAML does not traceback; line parser recovers repo/rev lines.

    Args:
        mod: Loaded ``prek_config_revs`` module.
        tmp_path: Temporary directory for fixture files.
    """
    path = tmp_path / "cfg.yaml"
    path.write_text(
        "<<<<<<< HEAD\n"
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.15.5\n"
        "=======\n"
        "broken: [unclosed\n"
        ">>>>>>> other\n",
        encoding="utf-8",
    )
    assert mod.load_yaml_revs(path) == {
        "https://github.com/astral-sh/ruff-pre-commit": "v0.15.5",
    }


def test_cmd_check_aligned(mod: ModuleType, tmp_path: Path) -> None:
    """Aligned configs exit 0.

    Args:
        mod: Loaded ``prek_config_revs`` module.
        tmp_path: Temporary directory for fixture files.
    """
    toml_path = tmp_path / "prek.toml"
    yaml_path = tmp_path / ".pre-commit-config.yaml"
    toml_path.write_text(_SAMPLE_TOML, encoding="utf-8")
    yaml_path.write_text(_SAMPLE_YAML, encoding="utf-8")
    assert mod.cmd_check(toml_path, yaml_path) == 0


def test_cmd_check_drift(mod: ModuleType, tmp_path: Path) -> None:
    """Divergent shared revs exit 1.

    Args:
        mod: Loaded ``prek_config_revs`` module.
        tmp_path: Temporary directory for fixture files.
    """
    toml_path = tmp_path / "prek.toml"
    yaml_path = tmp_path / ".pre-commit-config.yaml"
    toml_path.write_text(_SAMPLE_TOML, encoding="utf-8")
    yaml_path.write_text(
        _SAMPLE_YAML.replace("v0.15.5", "v0.99.0"),
        encoding="utf-8",
    )
    assert mod.cmd_check(toml_path, yaml_path) == 1


def test_cmd_check_no_shared_repos(mod: ModuleType, tmp_path: Path) -> None:
    """Empty intersection is an error, not a silent pass.

    Args:
        mod: Loaded ``prek_config_revs`` module.
        tmp_path: Temporary directory for fixture files.
    """
    toml_path = tmp_path / "prek.toml"
    yaml_path = tmp_path / ".pre-commit-config.yaml"
    toml_path.write_text(
        '[[repos]]\nrepo = "https://github.com/a/one"\nrev = "1"\n',
        encoding="utf-8",
    )
    yaml_path.write_text(
        "repos:\n  - repo: https://github.com/b/two\n    rev: 2\n",
        encoding="utf-8",
    )
    assert mod.cmd_check(toml_path, yaml_path) == 1
