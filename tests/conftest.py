"""Pytest configuration and shared fixtures."""

import importlib
from pathlib import Path

import pytest

# Skip gateway tests when the gateway optional deps are not installed.
if not importlib.util.find_spec("sqlalchemy"):
    collect_ignore_glob = ["gateway/*"]

from apme_engine.engine.models import YAMLDict


@pytest.fixture  # type: ignore[untyped-decorator]
def repo_root() -> Path:
    """Project root (ansible-forward).

    Returns:
        Path to project root.
    """
    return Path(__file__).resolve().parent.parent


@pytest.fixture  # type: ignore[untyped-decorator]
def opa_bundle_path(repo_root: Path) -> Path:
    """Path to OPA bundle directory (built-in validator bundle).

    Args:
        repo_root: Repository root path fixture.

    Returns:
        Path to OPA bundle.

    """
    return repo_root / "src" / "apme_engine" / "validators" / "opa" / "bundle"


@pytest.fixture  # type: ignore[untyped-decorator]
def sample_hierarchy_payload() -> YAMLDict:
    """Minimal valid OPA input (hierarchy payload).

    Returns:
        YAMLDict with scan_id, hierarchy, metadata.
    """
    return {
        "scan_id": "test-scan-1",
        "hierarchy": [
            {
                "root_key": "playbook :/examples/pb.yml",
                "root_type": "playbook",
                "root_path": "/examples/pb.yml",
                "nodes": [
                    {
                        "type": "playcall",
                        "key": "playcall#x",
                        "file": "/examples/pb.yml",
                        "line": [1, 3],
                        "defined_in": "/examples/pb.yml",
                        "name": "",
                        "options": {},
                    },
                    {
                        "type": "taskcall",
                        "key": "taskcall#y",
                        "file": "/examples/pb.yml",
                        "line": [5, 7],
                        "defined_in": "/examples/pb.yml",
                        "module": "ansible.builtin.shell",
                        "annotations": [],
                        "name": "",
                        "options": {},
                        "module_options": {},
                    },
                ],
            },
        ],
        "metadata": {"type": "playbook", "name": "/examples/pb.yml", "collection_name": "", "role_name": ""},
    }


@pytest.fixture  # type: ignore[untyped-decorator]
def opa_eval_result_with_violations() -> YAMLDict:
    """OPA eval JSON output format with a list of violations.

    Returns:
        YAMLDict with result containing violations.
    """
    return {
        "result": [
            {
                "expressions": [
                    {
                        "value": [
                            {
                                "rule_id": "task-name",
                                "level": "warning",
                                "message": "Task using shell module should have a name",
                                "file": "/examples/pb.yml",
                                "line": 5,
                                "path": "taskcall#y",
                            },
                        ]
                    }
                ]
            }
        ]
    }


@pytest.fixture  # type: ignore[untyped-decorator]
def opa_eval_result_empty() -> YAMLDict:
    """OPA eval JSON with empty violations.

    Returns:
        YAMLDict with empty result.
    """
    return {"result": [{"expressions": [{"value": []}]}]}
