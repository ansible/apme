"""Tests for NodeState, ContentNode.record_state, and ContentNode.update_from_yaml (ADR-044 Phase 3)."""

from __future__ import annotations

import json

import pytest

from apme_engine.engine.models import ViolationDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeState,
    NodeType,
    _content_hash,
    _detect_indent,
    _node_from_dict,
    _node_to_dict,
    _reindent,
    _violation_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_YAML = """\
- name: Install nginx
  ansible.builtin.apt:
    name: nginx
    state: present
  when: ansible_os_family == "Debian"
  register: install_result
  become: true
  tags:
    - packages
"""

_TASK_YAML_FQCN_FIXED = """\
- name: Install nginx
  ansible.builtin.package:
    name: nginx
    state: present
  when: ansible_os_family == "Debian"
  register: install_result
  become: true
  tags:
    - packages
"""

_TASK_YAML_WITH_COMMENT = """\
- name: Install nginx
  apt:  # TODO: use FQCN
    name: nginx
    state: present
"""


def _make_task(yaml_lines: str = _TASK_YAML) -> ContentNode:
    identity = NodeIdentity(
        path="site.yml/plays[0]/tasks[0]",
        node_type=NodeType.TASK,
    )
    return ContentNode(
        identity=identity,
        file_path="site.yml",
        line_start=5,
        line_end=13,
        name="Install nginx",
        module="ansible.builtin.apt",
        module_options={"name": "nginx", "state": "present"},
        when_expr='ansible_os_family == "Debian"',
        register="install_result",
        become={"become": True},
        tags=["packages"],
        yaml_lines=yaml_lines,
    )


# ---------------------------------------------------------------------------
# NodeState
# ---------------------------------------------------------------------------


class TestNodeState:
    """Tests for the ``NodeState`` frozen dataclass."""

    def test_frozen(self) -> None:
        """NodeState instances must be immutable."""
        ns = NodeState(
            id="test@0",
            pass_number=0,
            phase="scanned",
            yaml_lines="- name: foo\n",
            content_hash=_content_hash("- name: foo\n"),
            timestamp="2026-03-30T00:00:00+00:00",
        )
        with pytest.raises(AttributeError):
            ns.phase = "transformed"  # type: ignore[misc]

    def test_content_hash_deterministic(self) -> None:
        """Same text must produce the same hash."""
        text = "- name: test\n"
        assert _content_hash(text) == _content_hash(text)

    def test_content_hash_differs(self) -> None:
        """Different text must produce different hashes."""
        assert _content_hash("a") != _content_hash("b")


# ---------------------------------------------------------------------------
# record_state
# ---------------------------------------------------------------------------


class TestRecordState:
    """Tests for ``ContentNode.record_state``."""

    def test_basic_record(self) -> None:
        """Recording a state appends to progression and updates state."""
        node = _make_task()
        ns = node.record_state(0, "scanned")

        assert ns is node.state
        assert len(node.progression) == 1
        assert node.progression[0] is ns
        assert ns.pass_number == 0
        assert ns.phase == "scanned"
        assert ns.yaml_lines == node.yaml_lines
        assert ns.content_hash == _content_hash(node.yaml_lines)
        assert ns.timestamp  # non-empty

    def test_multiple_records(self) -> None:
        """Multiple calls accumulate an ordered progression."""
        node = _make_task()
        ns0 = node.record_state(0, "scanned")
        ns1 = node.record_state(0, "transformed")
        ns2 = node.record_state(1, "scanned")

        assert len(node.progression) == 3
        assert node.progression == [ns0, ns1, ns2]
        assert node.state is ns2

    def test_state_captures_current_yaml(self) -> None:
        """After update_from_yaml, record_state captures the new content."""
        node = _make_task()
        node.record_state(0, "scanned")

        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        ns = node.record_state(0, "transformed")

        assert ns.yaml_lines == _TASK_YAML_FQCN_FIXED
        assert ns.content_hash == _content_hash(_TASK_YAML_FQCN_FIXED)
        assert node.progression[0].yaml_lines == _TASK_YAML
        assert node.progression[1].yaml_lines == _TASK_YAML_FQCN_FIXED


# ---------------------------------------------------------------------------
# update_from_yaml
# ---------------------------------------------------------------------------


class TestUpdateFromYaml:
    """Tests for ``ContentNode.update_from_yaml``."""

    def test_module_rename(self) -> None:
        """Updating YAML with a renamed module key updates the module field."""
        node = _make_task()
        assert node.module == "ansible.builtin.apt"

        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.module == "ansible.builtin.package"
        assert node.yaml_lines == _TASK_YAML_FQCN_FIXED

    def test_extracts_module_options(self) -> None:
        """Module options are re-extracted from the new YAML."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.module_options == {"name": "nginx", "state": "present"}

    def test_extracts_when(self) -> None:
        """When expression is re-extracted."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.when_expr == 'ansible_os_family == "Debian"'

    def test_extracts_register(self) -> None:
        """Register is re-extracted."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.register == "install_result"

    def test_extracts_become(self) -> None:
        """Become settings are re-extracted."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.become == {"become": True}

    def test_extracts_tags(self) -> None:
        """Tags list is re-extracted."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert node.tags == ["packages"]

    def test_clears_removed_fields(self) -> None:
        """Fields absent in new YAML are cleared to defaults."""
        node = _make_task()
        assert node.register == "install_result"
        assert node.tags == ["packages"]

        minimal_yaml = "- name: Minimal\n  ansible.builtin.debug:\n    msg: hi\n"
        node.update_from_yaml(minimal_yaml)

        assert node.register is None
        assert node.tags == []
        assert node.become is None
        assert node.when_expr is None

    def test_extracts_name(self) -> None:
        """Name field is re-extracted."""
        node = _make_task()
        node.update_from_yaml("- name: Changed name\n  ansible.builtin.debug:\n    msg: hi\n")
        assert node.name == "Changed name"

    def test_set_fact_extraction(self) -> None:
        """set_fact module options populate set_facts field."""
        node = _make_task()
        sf_yaml = "- name: Set facts\n  ansible.builtin.set_fact:\n    my_var: hello\n    cacheable: true\n"
        node.update_from_yaml(sf_yaml)
        assert node.module == "ansible.builtin.set_fact"
        assert node.set_facts == {"my_var": "hello"}

    def test_when_list(self) -> None:
        """List-form when is extracted as list of strings."""
        node = _make_task()
        yaml = "- name: Multi-when\n  ansible.builtin.debug:\n    msg: hi\n  when:\n    - foo\n    - bar\n"
        node.update_from_yaml(yaml)
        assert node.when_expr == ["foo", "bar"]

    def test_environment_extraction(self) -> None:
        """Environment dict is extracted."""
        node = _make_task()
        yaml = "- name: With env\n  ansible.builtin.command: echo hi\n  environment:\n    PATH: /usr/bin\n"
        node.update_from_yaml(yaml)
        assert node.environment == {"PATH": "/usr/bin"}

    def test_no_log_extraction(self) -> None:
        """no_log boolean is extracted."""
        node = _make_task()
        yaml = "- name: Secret\n  ansible.builtin.debug:\n    msg: hi\n  no_log: true\n"
        node.update_from_yaml(yaml)
        assert node.no_log is True

    def test_ignore_errors_extraction(self) -> None:
        """ignore_errors boolean is extracted."""
        node = _make_task()
        yaml = "- name: Risky\n  ansible.builtin.command: exit 1\n  ignore_errors: true\n"
        node.update_from_yaml(yaml)
        assert node.ignore_errors is True

    def test_delegate_to_extraction(self) -> None:
        """delegate_to string is extracted."""
        node = _make_task()
        yaml = "- name: Delegated\n  ansible.builtin.command: hostname\n  delegate_to: localhost\n"
        node.update_from_yaml(yaml)
        assert node.delegate_to == "localhost"

    def test_options_rebuilt(self) -> None:
        """node.options is rebuilt from parsed YAML, excluding name/module/block keys."""
        node = _make_task()
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        assert "when" in node.options
        assert "register" in node.options
        assert "become" in node.options
        assert "tags" in node.options
        assert node.options["register"] == "install_result"
        assert "name" not in node.options
        assert "ansible.builtin.package" not in node.options

    def test_options_cleared_on_minimal(self) -> None:
        """Minimal YAML produces minimal options."""
        node = _make_task()
        minimal = "- name: Minimal\n  ansible.builtin.debug:\n    msg: hi\n"
        node.update_from_yaml(minimal)
        assert "register" not in node.options
        assert "when" not in node.options

    def test_string_module_options_normalized(self) -> None:
        """Non-dict module args (e.g. command: echo foo) are normalized to _raw."""
        node = _make_task()
        yaml = "- name: Run cmd\n  ansible.builtin.command: echo hello\n"
        node.update_from_yaml(yaml)
        assert node.module_options == {"_raw": "echo hello"}

    def test_action_keyword_not_treated_as_module(self) -> None:
        """'action' is a meta key and must not be misidentified as a module name."""
        node = _make_task()
        yaml = "- name: Use action\n  action: ansible.builtin.debug\n"
        node.update_from_yaml(yaml)
        assert node.module != "action"

    def test_unparseable_yaml_preserves_text(self) -> None:
        """Unparseable YAML still updates yaml_lines but leaves fields alone."""
        node = _make_task()
        original_module = node.module
        bad_yaml = "  - :\n    : [invalid"
        node.update_from_yaml(bad_yaml)
        assert node.yaml_lines == bad_yaml
        assert node.module == original_module

    def test_identity_unchanged(self) -> None:
        """update_from_yaml never touches structural identity fields."""
        node = _make_task()
        orig_identity = node.identity
        orig_file = node.file_path
        orig_start = node.line_start
        orig_end = node.line_end

        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)

        assert node.identity is orig_identity
        assert node.file_path == orig_file
        assert node.line_start == orig_start
        assert node.line_end == orig_end

    def test_loop_extraction(self) -> None:
        """Loop field is extracted from YAML."""
        node = _make_task()
        yaml = "- name: Loopy\n  ansible.builtin.debug:\n    msg: '{{ item }}'\n  loop:\n    - a\n    - b\n"
        node.update_from_yaml(yaml)
        assert node.loop == ["a", "b"]

    def test_notify_extraction(self) -> None:
        """Notify list is extracted from YAML."""
        node = _make_task()
        yaml = "- name: Restart\n  ansible.builtin.service:\n    name: nginx\n  notify: restart nginx\n"
        node.update_from_yaml(yaml)
        assert node.notify == ["restart nginx"]


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestNodeStateSerialization:
    """Tests for NodeState serialization in _node_to_dict / _node_from_dict."""

    def test_round_trip_without_progression(self) -> None:
        """Nodes without progression round-trip cleanly."""
        node = _make_task()
        d = _node_to_dict(node)
        restored = _node_from_dict(d)

        assert restored.state is None
        assert restored.progression == []
        assert restored.module == node.module
        assert restored.yaml_lines == node.yaml_lines

    def test_round_trip_with_progression(self) -> None:
        """Nodes with progression entries round-trip cleanly."""
        node = _make_task()
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        node.record_state(0, "transformed")

        d = _node_to_dict(node)

        assert "state" in d
        assert "progression" in d
        assert len(d["progression"]) == 2  # type: ignore[arg-type]

        restored = _node_from_dict(d)

        assert restored.state is not None
        assert restored.state.phase == "transformed"
        assert restored.state.pass_number == 0
        assert len(restored.progression) == 2
        assert restored.progression[0].phase == "scanned"
        assert restored.progression[1].phase == "transformed"

    def test_json_serializable(self) -> None:
        """Serialized dict must be JSON-encodable."""
        node = _make_task()
        node.record_state(0, "scanned")
        d = _node_to_dict(node)
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

    def test_empty_progression_not_serialized(self) -> None:
        """Nodes with no progression omit the progression key."""
        node = _make_task()
        d = _node_to_dict(node)
        assert "progression" not in d
        assert "state" not in d

    def test_state_reconciled_from_progression(self) -> None:
        """When progression exists, state is reconciled to progression[-1]."""
        node = _make_task()
        node.record_state(0, "scanned")
        node.record_state(0, "transformed")
        d = _node_to_dict(node)

        # Corrupt state to point to first entry, not last
        d["state"] = d["progression"][0]  # type: ignore[index]

        restored = _node_from_dict(d)
        assert restored.state is not None
        assert restored.state.phase == "transformed"
        assert restored.state is restored.progression[-1]

    def test_violation_ledger_round_trip(self) -> None:
        """Violation ledger survives serialization round-trip."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)
        node.record_state(0, "scanned")

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id, "message": "Use FQCN"}
        graph.register_violations([v], 0)

        d = _node_to_dict(node)
        assert "violation_ledger" in d

        restored = _node_from_dict(d)
        key = _violation_key(v)
        assert key in restored.violation_ledger
        rec = restored.violation_ledger[key]
        assert rec.status == "open"
        assert rec.violation["rule_id"] == "M001"

    def test_violation_ledger_fixed_round_trip(self) -> None:
        """Fixed violations round-trip with attribution."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="deterministic", pass_number=1)

        d = _node_to_dict(node)
        restored = _node_from_dict(d)
        key = _violation_key(v)
        rec = restored.violation_ledger[key]
        assert rec.status == "fixed"
        assert rec.fixed_by == "deterministic"
        assert rec.fixed_in_pass == 1


# ---------------------------------------------------------------------------
# ContentGraph.apply_transform
# ---------------------------------------------------------------------------

_TASK_YAML_SHORT = """\
- name: Install nginx
  apt:
    name: nginx
    state: present
"""


class TestApplyTransform:
    """Tests for ``ContentGraph.apply_transform``."""

    def _build_graph(self) -> tuple[ContentGraph, str]:
        graph = ContentGraph()
        node = _make_task(yaml_lines=_TASK_YAML_SHORT)
        node.module = "apt"
        graph.add_node(node)
        return graph, node.node_id

    async def test_transform_applied(self) -> None:
        """A transform that modifies the CommentedMap updates yaml_lines and typed fields."""

        def rename_to_fqcn(task, violation):  # type: ignore[no-untyped-def]
            from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

            mk = get_module_key(task)
            if mk == "apt":
                rename_key(task, mk, "ansible.builtin.apt")
                return True
            return False

        graph, nid = self._build_graph()
        applied = await graph.apply_transform(nid, rename_to_fqcn, {})
        assert applied is True

        node = graph.get_node(nid)
        assert node is not None
        assert node.module == "ansible.builtin.apt"
        assert "ansible.builtin.apt" in node.yaml_lines
        assert "apt:" not in node.yaml_lines or "ansible.builtin.apt" in node.yaml_lines

    async def test_noop_transform(self) -> None:
        """A transform returning False leaves the node unchanged."""

        def noop(task, violation):  # type: ignore[no-untyped-def]
            return False

        graph, nid = self._build_graph()
        original_yaml = graph.get_node(nid).yaml_lines  # type: ignore[union-attr]
        applied = await graph.apply_transform(nid, noop, {})
        assert applied is False
        assert graph.get_node(nid).yaml_lines == original_yaml  # type: ignore[union-attr]

    async def test_dirty_tracking(self) -> None:
        """Applying a transform marks the node as dirty."""

        def always_change(task, violation):  # type: ignore[no-untyped-def]
            task["tags"] = ["changed"]
            return True

        graph, nid = self._build_graph()
        assert graph.dirty_nodes == frozenset()
        await graph.apply_transform(nid, always_change, {})
        assert nid in graph.dirty_nodes

    async def test_clear_dirty(self) -> None:
        """clear_dirty resets the dirty set."""

        def always_change(task, violation):  # type: ignore[no-untyped-def]
            task["tags"] = ["changed"]
            return True

        graph, nid = self._build_graph()
        await graph.apply_transform(nid, always_change, {})
        assert len(graph.dirty_nodes) == 1
        graph.clear_dirty()
        assert graph.dirty_nodes == frozenset()

    async def test_no_document_marker(self) -> None:
        """Serialized yaml_lines must not contain a '---' document marker."""

        def add_tag(task, violation):  # type: ignore[no-untyped-def]
            task["tags"] = ["test"]
            return True

        graph, nid = self._build_graph()
        await graph.apply_transform(nid, add_tag, {})
        node = graph.get_node(nid)
        assert node is not None
        assert not node.yaml_lines.startswith("---")

    async def test_nonexistent_node(self) -> None:
        """Applying to a missing node returns False."""
        graph = ContentGraph()
        applied = await graph.apply_transform("nonexistent", lambda t, v: True, {})
        assert applied is False

    async def test_progression_integration(self) -> None:
        """apply_transform + record_state produces correct progression."""

        def add_tag(task, violation):  # type: ignore[no-untyped-def]
            task["tags"] = ["added"]
            return True

        graph, nid = self._build_graph()
        node = graph.get_node(nid)
        assert node is not None
        node.record_state(0, "scanned")
        await graph.apply_transform(nid, add_tag, {})
        node.record_state(0, "transformed")

        assert len(node.progression) == 2
        assert node.progression[0].phase == "scanned"
        assert node.progression[1].phase == "transformed"
        assert "added" in node.progression[1].yaml_lines

    async def test_async_transform_fn(self) -> None:
        """An async transform function is awaited transparently."""

        async def async_rename(task, violation):  # type: ignore[no-untyped-def]
            from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

            mk = get_module_key(task)
            if mk == "apt":
                rename_key(task, mk, "ansible.builtin.apt")
                return True
            return False

        graph, nid = self._build_graph()
        applied = await graph.apply_transform(nid, async_rename, {})
        assert applied is True

        node = graph.get_node(nid)
        assert node is not None
        assert node.module == "ansible.builtin.apt"


# ---------------------------------------------------------------------------
# TransformRegistry NodeTransformFn
# ---------------------------------------------------------------------------


class TestRegistryNodeTransform:
    """Tests for NodeTransformFn support in TransformRegistry."""

    def test_register_node_transform(self) -> None:
        """Node transforms are registered and discoverable."""
        from apme_engine.remediation.registry import TransformRegistry

        reg = TransformRegistry()
        reg.register("TEST001", node=lambda t, v: True)
        assert "TEST001" in reg
        assert reg.get_node_transform("TEST001") is not None

    def test_apply_node(self) -> None:
        """apply_node calls the node transform directly."""
        from ruamel.yaml.comments import CommentedMap

        from apme_engine.remediation.registry import TransformRegistry

        reg = TransformRegistry()
        reg.register("TEST001", node=lambda t, v: True)

        task = CommentedMap({"name": "test", "ansible.builtin.debug": {"msg": "hi"}})
        assert reg.apply_node("TEST001", task, {}) is True

    def test_apply_node_missing(self) -> None:
        """apply_node returns False for unregistered rule."""
        from ruamel.yaml.comments import CommentedMap

        from apme_engine.remediation.registry import TransformRegistry

        reg = TransformRegistry()
        task = CommentedMap({"name": "test"})
        assert reg.apply_node("NOPE", task, {}) is False

    def test_rule_ids_includes_node(self) -> None:
        """rule_ids includes node-registered rules."""
        from apme_engine.remediation.registry import TransformRegistry

        reg = TransformRegistry()
        reg.register("A001", node=lambda t, v: True)
        reg.register("B001", node=lambda t, v: False)
        assert "A001" in reg.rule_ids
        assert "B001" in reg.rule_ids
        assert len(reg) == 2


# ---------------------------------------------------------------------------
# NodeState id, approved, source fields
# ---------------------------------------------------------------------------


class TestNodeStateFields:
    """Tests for NodeState id, approved, and source fields (ADR-044 Phase 3)."""

    def test_record_state_id_format(self) -> None:
        """record_state generates id as '{node_id}@{seq}'."""
        node = _make_task()
        ns = node.record_state(0, "scanned")
        assert ns.id == f"{node.node_id}@0"

    def test_record_state_id_increments(self) -> None:
        """Each entry gets a distinct monotonic id."""
        node = _make_task()
        ns0 = node.record_state(0, "scanned")
        ns1 = node.record_state(1, "transformed")
        assert ns0.id != ns1.id
        assert ns0.id.endswith("@0")
        assert ns1.id.endswith("@1")

    def test_record_state_id_unique_within_pass(self) -> None:
        """Multiple entries in the same pass get distinct IDs."""
        node = _make_task()
        ns0 = node.record_state(0, "scanned")
        ns1 = node.record_state(0, "transformed")
        assert ns0.id != ns1.id
        assert ns0.id.endswith("@0")
        assert ns1.id.endswith("@1")

    def test_record_state_default_unapproved(self) -> None:
        """All entries start unapproved (pending)."""
        node = _make_task()
        ns = node.record_state(0, "scanned")
        assert ns.approved is False

    def test_record_state_source(self) -> None:
        """Source is stored as metadata."""
        node = _make_task()
        ns = node.record_state(0, "transformed", source="deterministic")
        assert ns.source == "deterministic"

    def test_record_state_source_default(self) -> None:
        """Default source is empty string."""
        node = _make_task()
        ns = node.record_state(0, "scanned")
        assert ns.source == ""


# ---------------------------------------------------------------------------
# ContentGraph approval operations
# ---------------------------------------------------------------------------


class TestApprovalOperations:
    """Tests for approve_pending, approve_node, reject_node (ADR-044 Phase 3)."""

    def _build_graph_with_progression(self) -> tuple[ContentGraph, ContentNode]:
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        node.record_state(1, "transformed", source="deterministic")
        return graph, node

    def test_approve_pending_all(self) -> None:
        """approve_pending() approves all entries across the graph."""
        graph, node = self._build_graph_with_progression()
        assert all(not s.approved for s in node.progression)

        count = graph.approve_pending()
        assert count == 2
        assert all(s.approved for s in node.progression)

    def test_approve_pending_scoped(self) -> None:
        """approve_pending(node_id) only approves that node."""
        graph, node1 = self._build_graph_with_progression()
        node2 = _make_task(yaml_lines=_TASK_YAML_SHORT)
        node2.module = "copy"
        # Give node2 a distinct identity
        node2_identity = NodeIdentity(
            path="site.yml/plays[0]/tasks[1]",
            node_type=NodeType.TASK,
        )
        node2 = ContentNode(
            identity=node2_identity,
            file_path="site.yml",
            line_start=14,
            line_end=18,
            module="copy",
            yaml_lines=_TASK_YAML_SHORT,
        )
        graph.add_node(node2)
        node2.record_state(0, "scanned")

        graph.approve_pending(node1.node_id)
        assert all(s.approved for s in node1.progression)
        assert not node2.progression[0].approved

    def test_approve_node_convenience(self) -> None:
        """approve_node returns True when entries are approved."""
        graph, node = self._build_graph_with_progression()
        assert graph.approve_node(node.node_id) is True
        assert all(s.approved for s in node.progression)

    def test_approve_node_already_approved(self) -> None:
        """approve_node returns False when already approved."""
        graph, node = self._build_graph_with_progression()
        graph.approve_pending()
        assert graph.approve_node(node.node_id) is False

    def test_reject_node_truncates(self) -> None:
        """reject_node removes unapproved entries and restores state."""
        graph, node = self._build_graph_with_progression()
        # Approve first entry, leave second pending
        from dataclasses import replace

        node.progression[0] = replace(node.progression[0], approved=True)

        result = graph.reject_node(node.node_id)
        assert result is True
        assert len(node.progression) == 1
        assert node.progression[0].approved is True
        assert node.state is node.progression[0]
        assert node.yaml_lines == node.progression[0].yaml_lines

    def test_reject_node_all_approved(self) -> None:
        """reject_node returns False when all entries are approved."""
        graph, node = self._build_graph_with_progression()
        graph.approve_pending()
        assert graph.reject_node(node.node_id) is False

    def test_reject_node_cascades(self) -> None:
        """Rejecting the first unapproved entry also removes subsequent entries."""
        graph, node = self._build_graph_with_progression()
        # Add a third entry (AI transform)
        node.update_from_yaml("- name: AI fixed\n  ansible.builtin.apt:\n    name: nginx\n")
        node.record_state(2, "ai_transformed", source="ai")

        # Approve first entry only
        from dataclasses import replace

        node.progression[0] = replace(node.progression[0], approved=True)

        assert len(node.progression) == 3
        graph.reject_node(node.node_id)
        assert len(node.progression) == 1

    def test_reject_node_no_approved_retains_baseline(self) -> None:
        """reject_node with no approved entries retains the baseline."""
        graph, node = self._build_graph_with_progression()
        original_yaml = node.progression[0].yaml_lines

        result = graph.reject_node(node.node_id)
        assert result is True
        assert len(node.progression) == 1
        assert node.state is node.progression[0]
        assert node.yaml_lines == original_yaml

    def test_reject_nonexistent_node(self) -> None:
        """reject_node returns False for missing node."""
        graph = ContentGraph()
        assert graph.reject_node("missing") is False

    def test_approve_updates_node_state(self) -> None:
        """After approve_pending, node.state reflects the last progression entry."""
        graph, node = self._build_graph_with_progression()
        graph.approve_pending()
        assert node.state is node.progression[-1]
        assert node.state.approved is True


# ---------------------------------------------------------------------------
# ContentGraph.collect_violations
# ---------------------------------------------------------------------------


class TestCollectViolations:
    """Tests for ``ContentGraph.collect_violations`` (via violation ledger)."""

    def test_empty_graph(self) -> None:
        """Empty graph returns no violations."""
        graph = ContentGraph()
        assert graph.collect_violations() == []

    def test_no_violations_registered(self) -> None:
        """Nodes without registered violations contribute nothing."""
        graph = ContentGraph()
        graph.add_node(_make_task())
        assert graph.collect_violations() == []

    def test_collects_open_violations(self) -> None:
        """Open violations are returned by collect_violations."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id, "message": "Use FQCN"}
        graph.register_violations([v], 0)

        result = graph.collect_violations()
        assert len(result) == 1
        assert result[0]["rule_id"] == "M001"

    def test_collects_across_nodes(self) -> None:
        """Violations from multiple nodes are combined."""
        graph = ContentGraph()
        node1 = _make_task()
        graph.add_node(node1)
        node2 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=14,
            line_end=18,
            yaml_lines="- name: Task2\n  ansible.builtin.debug:\n    msg: hi\n",
        )
        graph.add_node(node2)

        v1: ViolationDict = {"rule_id": "M001", "path": node1.node_id}
        v2: ViolationDict = {"rule_id": "L005", "path": node2.node_id}
        graph.register_violations([v1, v2], 0)

        result = graph.collect_violations()
        assert len(result) == 2
        rule_ids = {v["rule_id"] for v in result}
        assert rule_ids == {"M001", "L005"}

    def test_fixed_not_in_collect(self) -> None:
        """Fixed violations are excluded from collect_violations."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="deterministic", pass_number=1)

        assert graph.collect_violations() == []

    def test_query_fixed(self) -> None:
        """query_violations(status='fixed') returns fixed violations."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="deterministic", pass_number=1)

        fixed = graph.query_violations(status="fixed")
        assert len(fixed) == 1
        assert fixed[0]["rule_id"] == "M001"

    def test_query_by_fixed_by(self) -> None:
        """query_violations filters by fixed_by attribution."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v1: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        v2: ViolationDict = {"rule_id": "L005", "path": node.node_id}
        graph.register_violations([v1, v2], 0)
        graph.resolve_violations(node.node_id, {"L005"}, fixed_by="deterministic", pass_number=1)

        det = graph.query_violations(status="fixed", fixed_by="deterministic")
        assert len(det) == 1
        assert det[0]["rule_id"] == "M001"

    def test_proposed_state(self) -> None:
        """Proposed violations are neither in collect nor in fixed queries."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="ai", pass_number=1, status="proposed")

        assert graph.collect_violations() == []
        assert graph.query_violations(status="fixed") == []
        proposed = graph.query_violations(status="proposed")
        assert len(proposed) == 1

    def test_approve_proposed(self) -> None:
        """approve_proposed transitions proposed to fixed."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="ai", pass_number=1, status="proposed")

        count = graph.approve_proposed(node.node_id)
        assert count == 1

        fixed = graph.query_violations(status="fixed")
        assert len(fixed) == 1
        assert fixed[0]["rule_id"] == "M001"

    def test_decline_proposed(self) -> None:
        """decline_proposed transitions proposed to declined."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="ai", pass_number=1, status="proposed")

        count = graph.decline_proposed(node.node_id)
        assert count == 1

        declined = graph.query_violations(status="declined")
        assert len(declined) == 1
        key = _violation_key(v)
        rec = node.violation_ledger[key]
        assert rec.fixed_by is None
        assert rec.fixed_in_pass is None

    def test_reopen_regression(self) -> None:
        """A fixed violation that reappears is reopened."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)

        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        graph.resolve_violations(node.node_id, set(), fixed_by="deterministic", pass_number=1)
        assert graph.collect_violations() == []

        graph.register_violations([v], 2)
        assert len(graph.collect_violations()) == 1


# ---------------------------------------------------------------------------
# ContentGraph.collect_step_diffs
# ---------------------------------------------------------------------------


class TestCollectStepDiffs:
    """Tests for ``ContentGraph.collect_step_diffs``."""

    def test_empty_graph(self) -> None:
        """Empty graph returns no step diffs."""
        graph = ContentGraph()
        assert graph.collect_step_diffs() == []

    def test_same_content_no_diff(self) -> None:
        """No diff produced when content_hash is unchanged between entries."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "scanned")
        assert graph.collect_step_diffs() == []

    def test_content_change_produces_diff(self) -> None:
        """A content change between progression entries produces a diff record."""
        graph = ContentGraph()
        node = _make_task()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN_FIXED)
        node.record_state(1, "transformed", source="deterministic")

        diffs = graph.collect_step_diffs()
        assert len(diffs) == 1
        d = diffs[0]
        assert d["node_id"] == node.node_id
        assert d["phase"] == "transformed"
        assert d["source"] == "deterministic"
        assert isinstance(d["diff"], str)
        assert len(d["diff"]) > 0


# ---------------------------------------------------------------------------
# Indent helpers
# ---------------------------------------------------------------------------

_INDENT0 = """\
- name: Install nginx
  ansible.builtin.apt:
    name: nginx
"""

_INDENT4 = """\
    - name: Install nginx
      ansible.builtin.apt:
        name: nginx
"""

_INDENT8 = """\
        - name: Install nginx
          ansible.builtin.apt:
            name: nginx
"""


class TestDetectIndent:
    """Tests for _detect_indent."""

    def test_zero_indent(self) -> None:
        """Detects zero indent on root-level YAML."""
        assert _detect_indent(_INDENT0) == 0

    def test_four_indent(self) -> None:
        """Detects four-space indent on play-level tasks."""
        assert _detect_indent(_INDENT4) == 4

    def test_eight_indent(self) -> None:
        """Detects eight-space indent on block children."""
        assert _detect_indent(_INDENT8) == 8

    def test_empty_string(self) -> None:
        """Returns 0 for empty input."""
        assert _detect_indent("") == 0

    def test_blank_lines_skipped(self) -> None:
        """Skips leading blank lines when detecting indent."""
        assert _detect_indent("\n\n    - name: test\n") == 4


class TestReindent:
    """Tests for _reindent."""

    def test_add_indent(self) -> None:
        """Adds 4-space indent to root-level content."""
        result = _reindent(_INDENT0, 4)
        assert _detect_indent(result) == 4
        assert "    - name: Install nginx\n" in result
        assert "      ansible.builtin.apt:\n" in result

    def test_remove_indent(self) -> None:
        """Removes indent from 4-space content to root level."""
        result = _reindent(_INDENT4, 0)
        assert _detect_indent(result) == 0
        assert "- name: Install nginx\n" in result

    def test_noop_when_matching(self) -> None:
        """Returns input unchanged when indent already matches."""
        result = _reindent(_INDENT4, 4)
        assert result == _INDENT4

    def test_blank_lines_preserved(self) -> None:
        """Blank lines pass through without modification."""
        text = "\n    - name: test\n\n    - name: test2\n"
        result = _reindent(text, 0)
        assert result.startswith("\n- name: test\n")

    def test_shift_deeper(self) -> None:
        """Shifts 4-space content to 8-space depth."""
        result = _reindent(_INDENT4, 8)
        assert _detect_indent(result) == 8


class TestIndentPreservation:
    """Verify apply_transform preserves file-level indent."""

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_transform_preserves_indent(self) -> None:
        """A node at indent 4 stays at indent 4 after a transform."""
        from ruamel.yaml.comments import CommentedMap

        graph = ContentGraph()
        yaml_text = "    - name: install nginx\n      apt:\n        name: nginx\n"
        node = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            line_end=12,
            yaml_lines=yaml_text,
            indent_depth=4,
            module="apt",
        )
        graph.add_node(node)

        def rename_module(task_map: CommentedMap, _violation: ViolationDict) -> bool:
            task_map["ansible.builtin.apt"] = task_map.pop("apt")
            return True

        violation: ViolationDict = {"rule_id": "L042", "path": node.node_id}

        applied = await graph.apply_transform(node.node_id, rename_module, violation)
        assert applied

        assert _detect_indent(node.yaml_lines) == 4
        for line in node.yaml_lines.splitlines():
            if line.strip():
                assert line.startswith("    "), f"Line not at indent 4: {line!r}"

    @pytest.mark.asyncio  # type: ignore[untyped-decorator]
    async def test_zero_indent_unchanged(self) -> None:
        """A node at indent 0 stays at indent 0 (standalone task file)."""
        from ruamel.yaml.comments import CommentedMap

        graph = ContentGraph()
        yaml_text = "- name: install nginx\n  apt:\n    name: nginx\n"
        node = ContentNode(
            identity=NodeIdentity(path="tasks/install.yml/tasks[0]", node_type=NodeType.TASK),
            file_path="tasks/install.yml",
            line_start=1,
            line_end=3,
            yaml_lines=yaml_text,
            indent_depth=0,
            module="apt",
        )
        graph.add_node(node)

        def rename_module(task_map: CommentedMap, _violation: ViolationDict) -> bool:
            task_map["ansible.builtin.apt"] = task_map.pop("apt")
            return True

        violation: ViolationDict = {"rule_id": "L042", "path": node.node_id}

        applied = await graph.apply_transform(node.node_id, rename_module, violation)
        assert applied
        assert _detect_indent(node.yaml_lines) == 0

    def test_serialization_round_trip_preserves_indent(self) -> None:
        """indent_depth survives node serialization and deserialization."""
        node = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            yaml_lines="    - name: test\n",
            indent_depth=4,
        )
        d = _node_to_dict(node)
        assert d["indent_depth"] == 4

        restored = _node_from_dict(d)
        assert restored.indent_depth == 4


class TestApplyYaml:
    """Tests for ContentGraph.apply_yaml public API."""

    def test_nonexistent_node_returns_false(self) -> None:
        """apply_yaml returns False for a node that doesn't exist."""
        graph = ContentGraph()
        assert graph.apply_yaml("no-such-node", "- name: test\n") is False

    def test_noop_returns_false(self) -> None:
        """apply_yaml returns False when content is identical (no dirty mark)."""
        graph = ContentGraph()
        node = _make_task(yaml_lines="- name: test\n")
        node.indent_depth = 0
        graph.add_node(node)

        result = graph.apply_yaml(node.node_id, "- name: test\n")
        assert result is False
        assert node.node_id not in graph.dirty_nodes

    def test_content_change_returns_true(self) -> None:
        """apply_yaml returns True and marks dirty when content changes."""
        graph = ContentGraph()
        node = _make_task(yaml_lines="- name: old\n")
        node.indent_depth = 0
        graph.add_node(node)

        result = graph.apply_yaml(node.node_id, "- name: new\n")
        assert result is True
        assert node.node_id in graph.dirty_nodes
        assert "new" in node.yaml_lines

    def test_indent_correction_at_zero(self) -> None:
        """apply_yaml corrects indentation even when indent_depth is 0."""
        graph = ContentGraph()
        node = _make_task(yaml_lines="- name: test\n")
        node.indent_depth = 0
        graph.add_node(node)

        result = graph.apply_yaml(node.node_id, "  - name: changed\n")
        assert result is True
        assert _detect_indent(node.yaml_lines) == 0

    def test_indent_correction_at_depth(self) -> None:
        """apply_yaml reindents to match node's indent_depth."""
        graph = ContentGraph()
        node = _make_task(yaml_lines="    - name: test\n")
        node.indent_depth = 4
        graph.add_node(node)

        result = graph.apply_yaml(node.node_id, "- name: changed\n")
        assert result is True
        assert _detect_indent(node.yaml_lines) == 4
