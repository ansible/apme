"""Tests for integrated engine scanner hierarchy payload (build_hierarchy_payload, _node_to_dict, apply_rules)."""

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from apme_engine.engine.scanner import SingleScan


def _import_single_scan() -> type["SingleScan"] | None:
    """Import SingleScan from apme_engine.engine; return None if import fails.

    Returns:
        SingleScan class or None.
    """
    try:
        from apme_engine.engine.scanner import SingleScan

        return SingleScan
    except Exception:
        return None


@pytest.fixture  # type: ignore[untyped-decorator]
def single_scan_with_mock_contexts() -> "SingleScan":
    """SingleScan with minimal mock contexts so build_hierarchy_payload runs.

    Returns:
        SingleScan instance with mock playcall and taskcall.
    """
    SingleScan = _import_single_scan()
    if SingleScan is None:
        pytest.skip("apme_engine.engine not importable (missing deps)")
    scan = SingleScan(type="playbook", name="test.yml", root_dir="/tmp", rules_dir="")  # type: ignore[misc]
    # Mock context: root_key, sequence of nodes
    mock_spec = MagicMock()
    mock_spec.defined_in = "/path/to/play.yml"
    mock_spec.line_num_in_file = [10, 12]
    mock_spec.line_number = None
    mock_spec.name = ""
    mock_task = MagicMock()
    mock_task.type = "taskcall"
    mock_task.key = "taskcall#key1"
    mock_task.spec = mock_spec
    mock_task.name = ""
    mock_task.resolved_name = "ansible.builtin.shell"
    mock_task.resolved_action = "ansible.builtin.shell"
    mock_task.annotations = []
    mock_play = MagicMock()
    mock_play.type = "playcall"
    mock_play.key = "playcall#play1"
    mock_play.spec = mock_spec
    mock_ctx = MagicMock()
    mock_ctx.root_key = "playbook :/path/to/play.yml"
    mock_ctx.sequence = [mock_play, mock_task]
    scan.contexts = [mock_ctx]
    return scan


class TestScannerHierarchy:
    """Tests for integrated engine scanner build_hierarchy_payload and apply_rules."""

    def test_build_hierarchy_payload_structure(self, single_scan_with_mock_contexts: "SingleScan") -> None:
        """build_hierarchy_payload returns dict with scan_id, hierarchy, metadata.

        Args:
            single_scan_with_mock_contexts: Fixture providing a SingleScan with mocked contexts.

        """
        scan = single_scan_with_mock_contexts
        payload = scan.build_hierarchy_payload(scan_id="fixed-id")
        assert payload["scan_id"] == "fixed-id"
        assert "hierarchy" in payload
        hierarchy = cast(list[dict[str, object]], payload["hierarchy"])
        assert len(hierarchy) == 1
        tree = hierarchy[0]
        assert tree["root_key"] == "playbook :/path/to/play.yml"
        assert tree["root_type"] == "playbook"
        assert tree["root_path"] == "/path/to/play.yml"
        nodes = cast(list[dict[str, object]], tree["nodes"])
        assert len(nodes) == 2
        metadata = cast(dict[str, object], payload["metadata"])
        assert metadata["type"] == "playbook"
        assert metadata["name"] == "test.yml"

    def test_build_hierarchy_payload_node_serialization(self, single_scan_with_mock_contexts: "SingleScan") -> None:
        """_node_to_dict serializes playcall and taskcall with file, line, module.

        Args:
            single_scan_with_mock_contexts: Fixture providing a SingleScan with mocked contexts.

        """
        scan = single_scan_with_mock_contexts
        payload = scan.build_hierarchy_payload(scan_id="x")
        hierarchy = cast(list[dict[str, object]], payload["hierarchy"])
        tree = hierarchy[0]
        nodes = cast(list[dict[str, object]], tree["nodes"])
        play_node = nodes[0]
        assert play_node["type"] == "playcall"
        assert play_node["key"] == "playcall#play1"
        assert play_node["file"] == "/path/to/play.yml"
        assert play_node["line"] == [10, 12]
        assert "module" not in play_node
        assert "name" in play_node
        assert "options" in play_node
        task_node = nodes[1]
        assert task_node["type"] == "taskcall"
        assert task_node["module"] == "ansible.builtin.shell"
        assert task_node["annotations"] == []
        assert task_node["name"] is None
        assert task_node["options"] == {}
        assert task_node["module_options"] == {}

    def test_build_hierarchy_payload_empty_scan_id_generates_timestamp(
        self, single_scan_with_mock_contexts: "SingleScan"
    ) -> None:
        """When scan_id is empty, build_hierarchy_payload uses timestamp.

        Args:
            single_scan_with_mock_contexts: Fixture providing a SingleScan with mocked contexts.

        """
        scan = single_scan_with_mock_contexts
        payload = scan.build_hierarchy_payload()
        scan_id = str(payload["scan_id"])
        assert scan_id != ""
        assert len(scan_id) >= 14  # YYYYMMDDHHMMSS

    def test_build_hierarchy_payload_empty_contexts_returns_empty_trees(self) -> None:
        """When contexts is empty, hierarchy is empty list."""
        SingleScan = _import_single_scan()
        if SingleScan is None:
            pytest.skip("apme_engine.engine not importable")
        scan = SingleScan(type="playbook", name="test.yml", root_dir="/tmp", rules_dir="")  # type: ignore[misc]
        scan.contexts = []
        payload = scan.build_hierarchy_payload(scan_id="id")
        assert payload["hierarchy"] == []

    def test_apply_rules_sets_findings_and_hierarchy_payload(
        self, single_scan_with_mock_contexts: "SingleScan"
    ) -> None:
        """apply_rules builds hierarchy_payload and sets findings with it in report.

        Args:
            single_scan_with_mock_contexts: Fixture providing a SingleScan with mocked contexts.

        """
        scan = single_scan_with_mock_contexts
        scan.apply_rules()
        assert scan.hierarchy_payload != {}
        assert scan.findings is not None
        assert "hierarchy_payload" in scan.findings.report
        assert scan.findings.report["hierarchy_payload"] == scan.hierarchy_payload
        assert scan.result is None

    def test_node_to_dict_no_spec(self, single_scan_with_mock_contexts: "SingleScan") -> None:
        """_node_to_dict handles node without spec (file/line empty).

        Args:
            single_scan_with_mock_contexts: Fixture providing a SingleScan with mocked contexts.

        """
        scan = single_scan_with_mock_contexts
        node = MagicMock()
        node.type = "playcall"
        node.key = "k"
        node.spec = None
        d = scan._node_to_dict(node)
        assert d["file"] == ""
        assert d["line"] is None
        assert d["defined_in"] == ""
