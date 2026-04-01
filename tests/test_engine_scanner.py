"""Tests for apme_engine.engine.scanner."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from apme_engine.engine.models import (
    LoadType,
)
from apme_engine.engine.scanner import Config, SingleScan


class TestConfig:
    """Tests for Config."""

    def test_defaults_no_config_file(self, tmp_path: Path) -> None:
        """Config with missing file uses defaults.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg = Config(path=str(tmp_path / "nonexistent.yml"))
        assert cfg.data_dir != ""
        assert cfg.rules_dir != ""
        assert cfg.logger_key != ""
        assert cfg.log_level == "info"
        assert cfg.disable_default_rules is False

    def test_from_yaml_file(self, tmp_path: Path) -> None:
        """Config loads from YAML file.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("data_dir: /custom/data\nlog_level: debug\n")
        cfg = Config(path=str(cfg_file))
        assert cfg.data_dir == "/custom/data"
        assert cfg.log_level == "debug"

    def test_env_overrides(self, tmp_path: Path) -> None:
        """Environment variables override config.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        with patch.dict(os.environ, {"ARI_DATA_DIR": "/env/data", "ARI_LOG_LEVEL": "warning"}):
            cfg = Config(path=str(tmp_path / "missing.yml"))
        assert cfg.data_dir == "/env/data"
        assert cfg.log_level == "warning"

    def test_explicit_values_override_all(self, tmp_path: Path) -> None:
        """Explicit constructor values override file and env.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg = Config(
            path=str(tmp_path / "missing.yml"),
            data_dir="/explicit/data",
            rules_dir="/explicit/rules",
            log_level="error",
        )
        assert cfg.data_dir == "/explicit/data"
        assert cfg.log_level == "error"

    def test_bad_config_file_raises(self, tmp_path: Path) -> None:
        """Invalid YAML config raises ValueError.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg_file = tmp_path / "bad.yml"
        cfg_file.write_text("invalid: yaml: content: [[[")
        with pytest.raises(ValueError, match="failed to load"):
            Config(path=str(cfg_file))

    def test_disable_default_rules(self, tmp_path: Path) -> None:
        """disable_default_rules loads from config.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("disable_default_rules: true\n")
        cfg = Config(path=str(cfg_file))
        assert cfg.disable_default_rules is True

    def test_rules_from_env(self, tmp_path: Path) -> None:
        """ARI_RULES env var sets rules list.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        with patch.dict(os.environ, {"ARI_RULES": "rule1,rule2,rule3"}):
            cfg = Config(path=str(tmp_path / "missing.yml"))
        assert cfg.rules == ["rule1", "rule2", "rule3"]

    def test_get_single_config_from_yaml(self, tmp_path: Path) -> None:
        """logger_key loads from YAML.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("logger_key: custom_logger\n")
        cfg = Config(path=str(cfg_file))
        assert cfg.logger_key == "custom_logger"


class TestSingleScanInit:
    """Tests for SingleScan initialization."""

    def test_collection_type_sets_paths(self) -> None:
        """Collection type sets type and name."""
        ss = SingleScan(
            type=LoadType.COLLECTION,
            name="ns.col",
            root_dir="/tmp/ari-data",
        )
        assert ss.type == LoadType.COLLECTION
        assert ss.name == "ns.col"

    def test_role_type_sets_paths(self) -> None:
        """Role type sets type and name."""
        ss = SingleScan(
            type=LoadType.ROLE,
            name="myrole",
            root_dir="/tmp/ari-data",
        )
        assert ss.type == LoadType.ROLE

    def test_project_type(self) -> None:
        """Project type sets type and name."""
        ss = SingleScan(
            type=LoadType.PROJECT,
            name="https://github.com/org/repo",
            root_dir="/tmp/ari-data",
        )
        assert ss.type == LoadType.PROJECT

    def test_playbook_type_with_yaml(self) -> None:
        """Playbook type with yaml sets playbook_yaml and target."""
        ss = SingleScan(
            type=LoadType.PLAYBOOK,
            name="myplaybook",
            playbook_yaml="---\n- hosts: all\n  tasks: []\n",
            playbook_only=True,
            root_dir="/tmp/ari-data",
        )
        assert ss.type == LoadType.PLAYBOOK
        assert ss.playbook_yaml != ""
        assert ss.target_playbook_name == "myplaybook"

    def test_taskfile_type_with_yaml(self) -> None:
        """Taskfile type with yaml sets taskfile_yaml and target."""
        ss = SingleScan(
            type=LoadType.TASKFILE,
            name="mytaskfile",
            taskfile_yaml="---\n- name: Test\n  ansible.builtin.debug:\n    msg: hello\n",
            taskfile_only=True,
            root_dir="/tmp/ari-data",
        )
        assert ss.type == LoadType.TASKFILE
        assert ss.target_taskfile_name == "mytaskfile"

    def test_default_fields(self) -> None:
        """SingleScan has empty findings, result by default."""
        ss = SingleScan(
            type=LoadType.COLLECTION,
            name="test",
            root_dir="/tmp/data",
        )
        assert ss.findings is None
        assert ss.result is None
        assert ss.hierarchy_payload == {}
