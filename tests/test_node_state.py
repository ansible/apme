"""Tests for NodeState, ContentNode.record_state, and ContentNode.update_from_yaml (ADR-044 Phase 3)."""

from __future__ import annotations

import json

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeState,
    NodeType,
    _content_hash,
    _node_from_dict,
    _node_to_dict,
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
            violations=("L007",),
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
        ns = node.record_state(0, "scanned", ("R108",))

        assert ns is node.state
        assert len(node.progression) == 1
        assert node.progression[0] is ns
        assert ns.pass_number == 0
        assert ns.phase == "scanned"
        assert ns.violations == ("R108",)
        assert ns.yaml_lines == node.yaml_lines
        assert ns.content_hash == _content_hash(node.yaml_lines)
        assert ns.timestamp  # non-empty

    def test_multiple_records(self) -> None:
        """Multiple calls accumulate an ordered progression."""
        node = _make_task()
        ns0 = node.record_state(0, "scanned", ("L007",))
        ns1 = node.record_state(0, "transformed")
        ns2 = node.record_state(1, "scanned")

        assert len(node.progression) == 3
        assert node.progression == [ns0, ns1, ns2]
        assert node.state is ns2

    def test_empty_violations_default(self) -> None:
        """Default violations is an empty tuple."""
        node = _make_task()
        ns = node.record_state(0, "original")
        assert ns.violations == ()

    def test_state_captures_current_yaml(self) -> None:
        """After update_from_yaml, record_state captures the new content."""
        node = _make_task()
        node.record_state(0, "scanned", ("M001",))

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
        node.record_state(0, "scanned", ("R108", "L007"))
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
        assert restored.progression[0].violations == ("R108", "L007")
        assert restored.progression[0].phase == "scanned"
        assert restored.progression[1].violations == ()
        assert restored.progression[1].phase == "transformed"

    def test_json_serializable(self) -> None:
        """Serialized dict must be JSON-encodable."""
        node = _make_task()
        node.record_state(0, "scanned", ("L045",))
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
        node.record_state(0, "scanned", ("L007",))
        node.record_state(0, "transformed")
        d = _node_to_dict(node)

        # Corrupt state to point to first entry, not last
        d["state"] = d["progression"][0]  # type: ignore[index]

        restored = _node_from_dict(d)
        assert restored.state is not None
        assert restored.state.phase == "transformed"
        assert restored.state is restored.progression[-1]

    def test_tuple_violations_accepted(self) -> None:
        """_node_state_from_dict accepts tuple violations (not just list)."""
        from apme_engine.engine.content_graph import _node_state_from_dict

        d: dict[str, object] = {
            "pass_number": 0,
            "phase": "scanned",
            "yaml_lines": "",
            "content_hash": "",
            "violations": ("L007", "R108"),
            "timestamp": "",
        }
        ns = _node_state_from_dict(d)
        assert ns.violations == ("L007", "R108")


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
        node.record_state(0, "scanned", ("L026",))
        await graph.apply_transform(nid, add_tag, {})
        node.record_state(0, "transformed")

        assert len(node.progression) == 2
        assert node.progression[0].phase == "scanned"
        assert node.progression[0].violations == ("L026",)
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
        from apme_engine.remediation.registry import TransformResult

        reg.register("B001", fn=lambda c, v: TransformResult("", False))
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
        ns0 = node.record_state(0, "scanned", ("M001",))
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
        node.record_state(0, "scanned", ("M001",))
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
        node2.record_state(0, "scanned", ("M001",))

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
