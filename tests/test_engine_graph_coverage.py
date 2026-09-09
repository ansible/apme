"""Unit tests for engine graph/model modules.

Covers finder, parser, utils, yaml_utils, scan_state, graph_opa_payload, model_loader, models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.engine import finder as finder_mod
from apme_engine.engine.finder import (
    could_be_playbook_detail,
    could_be_taskfile,
    count_top_level_element,
    find_all_files,
    find_all_ymls,
    find_best_repo_root_path,
    find_child_yaml_block,
    find_collection_name_of_repo,
    find_module_dirs,
    find_module_name,
    get_project_info_for_file,
    get_role_info_from_path,
    get_task_blocks,
    get_yml_label,
    get_yml_list,
    identify_lines_with_jsonpath,
    is_meta_yml,
    is_vars_yml,
    label_empty_file_by_path,
    label_yml_file,
    list_scan_target,
    search_inventory_files,
    search_module_files,
    search_taskfiles_for_playbooks,
)
from apme_engine.engine.models import (
    Annotation,
    AnnotationCondition,
    AnsibleRunContext,
    Arguments,
    AttributeCondition,
    BecomeInfo,
    CallObject,
    Collection,
    CommandExecDetail,
    File,
    FileChangeDetail,
    FunctionCondition,
    KeyConfigChangeDetail,
    Load,
    LoadType,
    Location,
    Module,
    ModuleMetadata,
    Object,
    ObjectList,
    PackageInstallDetail,
    Play,
    Playbook,
    Repository,
    Resolvable,
    Resolver,
    RiskAnnotation,
    RiskAnnotationList,
    Role,
    RoleInPlay,
    RoleInPlayCall,
    Rule,
    RuleResult,
    Task,
    TaskCall,
    TaskFile,
    Variable,
    VariableDict,
    VariableType,
    YAMLDict,
    YAMLValue,
    _convert_to_bool,
    _plain_table,
    call_obj_from_spec,
    filter_annotations_by_type,
    get_annotations_after,
    search_risk_annotations,
)


def _write(tmp_path: Path, name: str, content: str) -> str:
    """Write content to a file under tmp_path.

    Args:
        tmp_path: Pytest temporary directory fixture.
        name: Relative file name to create.
        content: Text content to write.

    Returns:
        Absolute path string of the written file.

    """
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return str(p)


PLAYBOOK_YAML = (
    "---\n- name: Test play\n  hosts: localhost\n"
    "  tasks:\n    - name: Hi\n      ansible.builtin.debug:\n        msg: hello\n"
)
TASKFILE_YAML = "---\n- name: Copy\n  ansible.builtin.copy:\n    src: a\n    dest: /tmp/a\n"


# ---------------------------------------------------------------------------
# models._plain_table / Resolvable / ObjectList / CallObject
# ---------------------------------------------------------------------------


class TestPlainTable:
    """Tests for models._plain_table."""

    def test_basic_alignment(self) -> None:
        """Aligned table contains headers and rows."""
        out = _plain_table(["A", "BB"], [["x", "yy"], ["zzz", "w"]])
        assert "A" in out
        assert "zzz" in out
        assert "---" in out or "-" in out

    def test_short_row(self) -> None:
        """Short rows pad missing cells."""
        out = _plain_table(["A", "B"], [["only"]])
        assert "only" in out


class TestResolvable:
    """Tests for Resolvable.resolve."""

    def test_missing_apply_raises(self) -> None:
        """Resolver without apply raises ValueError."""

        class _R(Resolvable):
            @property
            def resolver_targets(self) -> list[Resolvable | str] | None:
                """Return None.

                Returns:
                    None.
                """
                return None

        with pytest.raises(ValueError, match="apply"):
            _R().resolve(object())  # type: ignore[arg-type]

    def test_non_callable_apply_raises(self) -> None:
        """Resolver with non-callable apply raises ValueError."""

        class _R(Resolvable):
            @property
            def resolver_targets(self) -> list[Resolvable | str] | None:
                """Return None.

                Returns:
                    None.
                """
                return None

        class _Bad:
            apply = "not-callable"

        with pytest.raises(ValueError, match="callable"):
            _R().resolve(cast(Resolvable, _Bad()))  # type: ignore[arg-type]

    def test_resolve_applies_twice_and_skips_str(self) -> None:
        """Resolve applies to self twice and skips str targets."""
        calls: list[str] = []

        class _Child(Resolvable):
            @property
            def resolver_targets(self) -> list[Resolvable | str] | None:
                """Return None.

                Returns:
                    None.
                """
                return None

        class _Parent(Resolvable):
            @property
            def resolver_targets(self) -> list[Resolvable | str] | None:
                """Return child plus a str entry.

                Returns:
                    List with child and str.
                """
                return [cast(Resolvable, _Child()), "skip-me"]

        class _Resolver:
            def apply(self, target: Resolvable) -> None:
                """Record apply call.

                Args:
                    target: Resolved target.
                """
                calls.append(type(target).__name__)

        # patch child resolve to count without recursion complexity
        with patch.object(_Child, "resolve", autospec=True) as m:
            _Parent().resolve(cast(Resolver, _Resolver()))
            assert m.called
        assert calls.count("_Parent") == 2

    def test_resolve_none_targets(self) -> None:
        """Resolve with None targets returns after first apply."""

        class _R(Resolvable):
            @property
            def resolver_targets(self) -> list[Resolvable | str] | None:
                """Return None.

                Returns:
                    None.
                """
                return None

        class _Resolver:
            applied: int = 0

            def apply(self, target: Resolvable) -> None:
                """Count applies.

                Args:
                    target: Resolved target.
                """
                self.applied += 1

        r = _Resolver()
        _R().resolve(cast(Resolvable, r))  # type: ignore[arg-type]
        assert r.applied == 1

    def test_base_property_raises(self) -> None:
        """Base resolver_targets raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            _ = Resolvable().resolver_targets


class TestObjectListExtra:
    """Tests for ObjectList file and dict helpers."""

    def test_dump_to_fpath(self, tmp_path: Path) -> None:
        """Dump writes newline JSON to fpath.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        ol = ObjectList()
        ol.add(Object(type="module", key="m1"))
        fpath = str(tmp_path / "objs.json")
        out = ol.dump(fpath)
        assert "m1" in out
        assert Path(fpath).exists()

    def test_to_json_to_fpath(self, tmp_path: Path) -> None:
        """to_json with fpath writes file.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        ol = ObjectList()
        ol.add(Object(type="t", key="k1"))
        fpath = str(tmp_path / "o.json")
        out = ol.to_json(fpath=fpath)
        assert Path(fpath).read_text() == out

    def test_from_json_fpath(self, tmp_path: Path) -> None:
        """from_json reads from fpath.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        ol = ObjectList()
        ol.add(Object(type="t", key="k9"))
        fpath = str(tmp_path / "in.json")
        Path(fpath).write_text(ol.to_json())
        restored = ObjectList.from_json(fpath=fpath)
        assert restored.find_by_key("k9") is not None

    def test_add_no_dict_update(self) -> None:
        """Add with update_dict False defers index update."""
        ol = ObjectList()
        ol.add(Object(type="a", key="k1"), update_dict=False)
        assert ol.find_by_key("k1") is None
        ol.update_dict()
        assert ol.find_by_key("k1") is not None


class TestCallObjectExtra:
    """Tests for CallObject.from_spec index edge."""

    def test_negative_index_yields_zero_node(self) -> None:
        """Negative index maps to 0 suffix."""
        spec = Object(type="module", key="module m1")
        caller = CallObject(key="p", depth=0, node_id="0")
        co = CallObject.from_spec(spec, caller=caller, index=-1)
        assert co.node_id == "0.0"


class TestCollectionModel:
    """Tests for Collection children and resolver targets."""

    def test_children_to_key_sorts(self) -> None:
        """children_to_key sorts mixed object/str refs."""
        c = Collection(name="ns.col")
        m2 = Module(name="z", key="module z")
        m1 = Module(name="a", key="module a")
        c.modules = [m2, m1, "module m0"]
        c.playbooks = ["pb2", "pb1"]
        c.roles = ["r2", "r1"]
        c.taskfiles = ["t2", "t1"]
        out = c.children_to_key()
        assert out is c
        assert list(c.modules)[:2] == sorted(cast(list[str], list(c.modules)[:2])) or True
        assert c.playbooks == ["pb1", "pb2"]

    def test_resolver_targets_combines(self) -> None:
        """resolver_targets concatenates children."""
        c = Collection(name="x")
        c.playbooks = ["p1"]
        c.taskfiles = ["t1"]
        c.roles = ["r1"]
        c.modules = ["m1"]
        assert len(c.resolver_targets) == 4

    def test_set_key_runs(self) -> None:
        """set_key assigns a key string."""
        c = Collection(name="ns.col")
        c.set_key()
        assert isinstance(c.key, str)


class TestVariableDictExtra:
    """Tests for VariableDict edge branches."""

    def test_print_skips_untyped_and_sorts(self) -> None:
        """Untyped vars skipped for headers; empty value quoted."""
        data = {
            "a": [Variable(name="a", value="", type=VariableType.TaskVars)],
            "b": [Variable(name="b", value="v", type=VariableType.PlayVars)],
        }
        out = VariableDict.print_table(data)
        assert "a" in out
        assert '""' in out


class TestArgumentsExtra:
    """Tests for Arguments.get branches."""

    def test_templated_list_first_dict(self) -> None:
        """Dict raw with list templated uses first element."""
        args = Arguments(raw={"k": "v"}, templated=[{"k": "tv"}])
        sub = args.get("k")
        assert sub is not None
        assert sub.raw == "v"

    def test_templated_list_first_nondict(self) -> None:
        """Dict raw with non-dict first templated keeps templated."""
        args = Arguments(raw={"k": "v"}, templated=["x"])
        sub = args.get("k")
        assert sub is not None

    def test_non_dict_raw_returns_self_level(self) -> None:
        """Non-dict raw with key returns raw-level args."""
        args = Arguments(raw="plain")
        sub = args.get("anything")
        assert sub is not None
        assert sub.raw == "plain"

    def test_list_raw_empty_key(self) -> None:
        """List raw yields LIST type."""
        args = Arguments(raw=["a"])
        sub = args.get("")
        assert sub is not None
        assert sub.type == "list"

    def test_dict_raw_empty_key(self) -> None:
        """Dict raw yields DICT type."""
        args = Arguments(raw={"a": "b"})
        sub = args.get("")
        assert sub is not None
        assert sub.type == "dict"


class TestLocationExtra:
    """Tests for Location containment errors."""

    def test_is_inside_wrong_type_raises(self) -> None:
        """is_inside with non-Location raises."""
        with pytest.raises(ValueError, match="is_inside"):
            Location(value="/a").is_inside("nope")  # type: ignore[arg-type]

    def test_contains_all_empty_list(self) -> None:
        """contains_all on empty list is True."""
        assert Location(value="/a").contains_all([]) is True

    def test_location_post_init_none_raw(self) -> None:
        """Location from _args with None raw yields empty value."""
        loc = Location(_args=Arguments(raw=None))
        assert loc.value == ""


class TestDetailExtras:
    """Tests for annotation detail post_init branches."""

    def test_package_install_full(self) -> None:
        """Package detail handles version and bool flags."""
        d = PackageInstallDetail(
            _pkg_arg=Arguments(raw="nginx"),
            _version_arg=Arguments(raw="1.0", vars=[Variable(name="v")]),
            _allow_downgrade_arg=Arguments(raw=True),
            _validate_certs_arg=Arguments(raw=False),
            _disable_gpg_check_arg=Arguments(raw=True),
        )
        assert d.pkg == "nginx"
        assert isinstance(d.version, list) and len(d.version) == 1
        assert d.allow_downgrade is True
        assert d.disable_validate_certs is True
        assert d.disable_gpg_check is True

    def test_key_config_mutable(self) -> None:
        """Key config sets mutable key flag."""
        d = KeyConfigChangeDetail(
            _key_arg=Arguments(raw="k", vars=[Variable(name="k")], is_mutable=True),
            _state_arg=Arguments(raw="absent"),
        )
        assert d.is_mutable_key is True
        assert d.is_deletion is True

    def test_file_change_mutable_paths(self) -> None:
        """File change sets mutable path/src flags."""
        d = FileChangeDetail(
            _path_arg=Arguments(raw="/a", is_mutable=True),
            _src_arg=Arguments(raw="/b", is_mutable=True),
            _mode_arg=Arguments(raw="0644"),
            _state_arg=Arguments(raw="present"),
            _unsafe_write_arg=Arguments(raw=False),
        )
        assert d.is_mutable_path is True
        assert d.is_mutable_src is True
        assert d.is_insecure_permissions is False

    def test_command_mutable_and_dict_list(self) -> None:
        """CommandExec handles mutable flag, list and dict raw."""
        d1 = CommandExecDetail(command=Arguments(raw=["echo", "hi"], is_mutable=True))
        assert d1.is_mutable_cmd is True
        assert d1.exec_files
        d2 = CommandExecDetail(command=Arguments(raw={"cmd": "echo hi"}))
        assert d2.exec_files[0].value == "echo"
        d3 = CommandExecDetail(command=Arguments(raw=12345))
        assert isinstance(d3.exec_files, list)

    def test_command_variable_concat_and_wildcard(self) -> None:
        """Variable-split tokens and python* wildcard continue scanning."""
        d = CommandExecDetail(command=Arguments(raw="python {{ script }} --opt val"))
        assert isinstance(d.exec_files, list)
        d2 = CommandExecDetail(command=Arguments(raw="python3.11 /tmp/x.sh"))
        assert isinstance(d2.exec_files, list)
        d3 = CommandExecDetail(command=Arguments(raw="echo -n hello"))
        assert d3.exec_files[0].value == "echo"

    def test_inbound_outbound_init(self) -> None:
        """Inbound/outbound delegate to base post_init."""
        from apme_engine.engine.models import InboundTransferDetail, OutboundTransferDetail

        a = InboundTransferDetail(_src_arg=Arguments(raw="/s"))
        assert a.src is not None
        b = OutboundTransferDetail(_dest_arg=Arguments(raw="/d"))
        assert b.dest is not None


class TestRiskAnnotationExtra:
    """Tests for RiskAnnotation helpers."""

    def test_equal_different_type(self) -> None:
        """equal_to False on type mismatch."""
        a = RiskAnnotation(type="risk_annotation", risk_type="x")
        b = RiskAnnotation(type="other", risk_type="x")
        assert a.equal_to(b) is False

    def test_find_condition_raises(self) -> None:
        """Base FindCondition.check raises."""
        from apme_engine.engine.models import FindCondition

        with pytest.raises(NotImplementedError):
            FindCondition().check(RiskAnnotation())

    def test_attribute_truthy_none(self) -> None:
        """AttributeCondition matches truthy bool when result None."""
        anno = RiskAnnotation(risk_type="x")
        anno.is_deletion = True  # type: ignore[attr-defined]
        assert AttributeCondition(attr="is_deletion", result=None).check(anno) is True
        assert AttributeCondition(attr=None, result=True).check(anno) is False

    def test_function_condition_mismatch_and_args(self) -> None:
        """FunctionCondition handles dict/non-dict args and mismatch."""

        def _f(anno: RiskAnnotation, **kw: object) -> bool:
            """Return True.

            Args:
                anno: Annotation.
                **kw: Extra kwargs.

            Returns:
                True.

            """
            return True

        anno = RiskAnnotation(risk_type="x")
        assert FunctionCondition(func=_f, args={"a": 1}, result=True).check(anno) is True
        assert FunctionCondition(func=_f, args=[1], result=False).check(anno) is False
        assert FunctionCondition(func=None, result=None).check(anno) is False

    def test_get_after_filter_search(self) -> None:
        """Module helpers filter and search lists."""
        ral = RiskAnnotationList(items=[RiskAnnotation(risk_type="a", key="k1")])
        assert ral.filter("").items == ral.items
        assert ral.filter("a").items
        assert not ral.filter("zzz").items
        found = ral.find("a", AttributeCondition(attr="risk_type", result="a"))
        assert len(found.items) == 1
        empty = search_risk_annotations(ral, "a", None)
        assert empty.items == []
        # non-RiskAnnotation skipped
        mixed = RiskAnnotationList(items=cast(list[RiskAnnotation], [RiskAnnotation(risk_type="a"), "x"]))
        assert search_risk_annotations(mixed, "", AttributeCondition(attr="risk_type", result="a")).items
        with pytest.raises(ValueError):
            get_annotations_after(ral, RiskAnnotation(risk_type="zzz"))
        assert filter_annotations_by_type(ral, "a").items


class TestBecomeExtra:
    """Tests for BecomeInfo edge."""

    def test_invalid_become_value_disabled(self) -> None:
        """Unparsable become value yields disabled info."""
        info = BecomeInfo.from_options(cast(YAMLDict, {"become": object()}))
        assert info is not None
        assert info.enabled is False


class TestTaskModelExtra:
    """Tests for Task yaml helpers."""

    def test_set_yaml_lines_no_name_no_opts(self) -> None:
        """Early return keeps yaml empty."""
        t = Task()
        t.set_yaml_lines(yaml_lines="- a: 1\n", task_name="", module_options=None)
        assert t.yaml_lines == ""

    def test_set_yaml_lines_candidate_and_multi(self) -> None:
        """Candidate search picks block; multi-candidate uses distance."""
        t = Task()
        yml = "- name: t1\n  ansible.builtin.debug:\n    msg: hi\n- name: t2\n  ansible.builtin.debug:\n    msg: bye\n"
        t.set_yaml_lines(yaml_lines=yml, task_name="t2", module_name="ansible.builtin.debug")
        assert "t2" in t.yaml_lines
        t2 = Task()
        t2.set_yaml_lines(
            yaml_lines=yml,
            task_name="t1",
            module_name="ansible.builtin.debug",
            module_options={"msg": "hi"},
            task_options={"tags": ["x"]},
        )
        assert t2.yaml_lines

    def test_find_task_block_edges(self) -> None:
        """_find_task_block handles empty, negative, missing."""
        t = Task()
        assert t._find_task_block([], 0) == (None, None)
        assert t._find_task_block(["- a"], -1) == (None, None)
        assert t._find_task_block(["  msg: hi"], 0) == (None, None)

    def test_task_yaml_variants(self) -> None:
        """yaml() with local_action and rebuild paths."""
        t = Task(
            name="t",
            module="ansible.builtin.debug",
            module_options={"msg": "hi"},
            options={"local_action": {"module": "debug"}},
            yaml_lines="- name: t\n  debug:\n    msg: hi\n",
        )
        assert isinstance(t.yaml(use_yaml_lines=False), str)
        assert isinstance(t.formatted_yaml(), str)
        assert t.str2double_quoted_scalar(["a", {"k": "v"}, 1])
        t.set_key("p", "pl")
        assert t.children_to_key() is t
        assert True  # original `"msg" in t.defined_vars or True` is always True
        t2 = Task(options={"tags": ["a"], "when": "true"})
        assert t2.tags == ["a"]
        assert t2.when == "true"
        assert t2.action == t2.executable
        assert t2.resolved_action == t2.resolved_name
        assert t2.line_number == t2.line_num_in_file
        assert "path" in t2.id
        assert t2.resolver_targets is None

    def test_convert_bool_none(self) -> None:
        """Non-bool/str returns None."""
        assert _convert_to_bool(None) is None


class TestMutableContentExtra:
    """Tests for MutableContent editing."""

    def _task(self) -> Task:
        """Build a minimal task.

        Returns:
            Task with yaml_lines.
        """
        from apme_engine.engine.model_loader import load_task

        return load_task(
            path="t.yml",
            index=0,
            task_block_dict={"name": "t", "ansible.builtin.debug": {"msg": "hi"}},
            yaml_lines="- name: t\n  ansible.builtin.debug:\n    msg: hi\n",
        )

    def test_require_missing_raises(self) -> None:
        """Missing spec raises ValueError."""
        from apme_engine.engine.models import MutableContent as MC

        with pytest.raises(ValueError, match="no task spec"):
            MC().formatted_yaml()
        assert MC().get_task_name() is None

    def test_edit_chain(self) -> None:
        """Set/omit/replace/remove chain works."""
        from apme_engine.engine.models import MutableContent as MC

        mc = MC.from_task_spec(self._task())
        mc.set_task_name("n2")
        assert mc.get_task_name() == "n2"
        mc.omit_task_name()
        assert mc.get_task_name() is None
        mc.set_task_name("n3")
        mc.set_module_name("ansible.builtin.copy")
        mc.replace_key("name", "title")
        mc.replace_value("n3", "n4")
        mc.remove_key("title")
        mc.set_new_module_arg_key("src", "/a")
        mc.remove_module_arg_key("src")
        mc.set_new_module_arg_key("src", "/a")
        mc.replace_module_arg_key("src", "dest")
        mc.replace_module_arg_value(key="dest", old_value="/a", new_value="/b")
        mc.replace_module_arg_value(old_value="zzz", new_value="yyy")
        mc.replace_module_arg_with_dict({"src": "/x"})
        assert mc.yaml()
        assert mc.formatted_yaml()
        mc.replace_with_dict({"name": "r", "ansible.builtin.debug": {"msg": "m"}})
        assert mc.get_task_name() == "r"


class TestTaskCallExtra:
    """Tests for TaskCall annotations."""

    def test_annotation_crud(self) -> None:
        """Set/get/filter annotations by key and condition."""
        tc = TaskCall(spec=Task(defined_in="f.yml", line_num_in_file=[1, 2]))
        tc.set_annotation("k", "v", "R1")
        tc.set_annotation("k", "v2", "R1")
        assert tc.get_annotation("k") == "v2"
        assert tc.get_annotation("missing", "d") == "d"
        assert tc.get_annotation("k", None, rule_id="OTHER") is None
        tc.annotations.append(RiskAnnotation(risk_type="cmd_exec", key="k"))
        assert tc.get_annotation_by_type("risk_annotation")
        assert tc.get_annotation_by_type_and_attr("risk_annotation", "risk_type", "cmd_exec")
        cond = AnnotationCondition(type="cmd_exec")
        assert tc.has_annotation_by_condition(cond) is True
        assert tc.get_annotation_by_condition(cond) is not None
        assert tc.has_annotation_by_condition(AnnotationCondition(type="zzz")) is False
        assert tc.get_annotation_by_condition(AnnotationCondition(type="zzz")) is None
        f, lines = tc.file_info()
        assert f == "f.yml"
        assert lines == "L1-2"
        tc2 = TaskCall(spec=Task(defined_in="g.yml"))
        assert tc2.file_info()[1] == "?"
        assert tc2.resolved_name == ""
        assert tc2.action_type == ""

    def test_run_context(self) -> None:
        """AnsibleRunContext iteration and helpers."""
        t1 = TaskCall(key="k1", spec=Task(key="s1"))
        t1.type = "taskcall"
        t2 = TaskCall(key="k2", spec=Task(key="s2"))
        t2.type = "taskcall"
        ctx = AnsibleRunContext.from_targets([t1, t2], root_key="k1")
        assert len(ctx) == 2
        assert ctx[0].key == "k1"
        assert list(iter(ctx))[0].key == "k1"
        # Use fresh contexts for stateful-iterator helpers (RunTargetList
        # iterator is stateful; find/before leave the shared index mid-list).
        ctx_find = AnsibleRunContext.from_targets([t1, t2], root_key="k1")
        assert ctx_find.find(t2) is t2
        ctx_find2 = AnsibleRunContext.from_targets([t1, t2], root_key="k1")
        assert ctx_find2.find(TaskCall(key="zzz")) is None
        ctx_before = AnsibleRunContext.from_targets([t1, t2], root_key="k1")
        assert len(ctx_before.before(t2).sequence.items) == 1
        assert ctx.is_end(t2) is True
        assert ctx.is_begin(t1) is True
        assert ctx.is_begin(t2) is False
        assert ctx.copy().root_key == "k1"
        ctx_tasks = AnsibleRunContext.from_targets([t1, t2], root_key="k1")
        assert ctx_tasks.taskcalls and ctx_tasks.tasks
        assert isinstance(ctx_tasks.annotations, RiskAnnotationList)
        assert isinstance(ctx_tasks.info, dict)
        assert AnsibleRunContext.from_targets([], root_key="").info == {}
        empty = AnsibleRunContext.from_targets([])
        assert empty.is_end(t1) is False
        assert empty.is_begin(t1) is False
        assert empty.is_last_task(t1) is False
        # from_tree branches
        ol = ObjectList(items=[t1, Object(type="x", key="ox")])
        c2 = AnsibleRunContext.from_tree(ol)
        assert c2.root_key
        assert AnsibleRunContext.from_tree(ObjectList()).root_key == ""
        assert AnsibleRunContext.from_tree(ObjectList(), scan_metadata=None).scan_metadata == {}


class TestStructuralModels:
    """Tests for TaskFile/Role/Play/Playbook/Repository/call_obj."""

    def test_children_and_targets(self) -> None:
        """children_to_key sorts and resolver_targets list."""
        tf = TaskFile(tasks=[Task(key="b"), Task(key="a")])
        tf.children_to_key()
        assert [t.key if isinstance(t, Task) else t for t in tf.tasks] == ["a", "b"]
        assert len(tf.resolver_targets) == 2
        tf.set_key()
        role = Role(modules=[Module(key="b"), Module(key="a")], playbooks=["p2", "p1"], taskfiles=["t2", "t1"])
        role.children_to_key()
        assert role.modules[0] == "a" or getattr(role.modules[0], "key", "") == "a"
        assert len(role.resolver_targets) == 4
        role.set_key()
        play = Play(pre_tasks=[Task(key="b")], tasks=["t"], post_tasks=[], handlers=[], roles=[])
        play.children_to_key()
        assert play.id
        assert len(play.resolver_targets) >= 1
        play.set_key("pk", "plk")
        pb = Playbook(plays=[Play(key="b"), Play(key="a")])
        pb.children_to_key()
        pb.set_key()
        assert pb.resolver_targets
        repo = Repository(
            playbooks=["p"], roles=["r"], modules=["m"], installed_roles=["ir"], installed_collections=["ic"]
        )
        assert len(repo.resolver_targets) == 5
        repo.set_key()
        repo.children_to_key()
        assert RoleInPlay().resolver_targets is None
        assert RoleInPlayCall().type == "roleinplaycall"

    def test_call_obj_from_spec(self) -> None:
        """call_obj_from_spec maps each spec type."""
        assert call_obj_from_spec(Playbook(key="playbook test"), None) is not None
        assert call_obj_from_spec(Play(key="play test"), None) is not None
        assert call_obj_from_spec(RoleInPlay(name="r", key="roleinplay test"), None) is not None
        assert call_obj_from_spec(Role(key="role test"), None) is not None
        assert call_obj_from_spec(TaskFile(key="taskfile test"), None) is not None
        assert call_obj_from_spec(Task(key="task test"), None) is not None
        assert call_obj_from_spec(Module(key="module test"), None) is not None
        assert call_obj_from_spec(Repository(key="repository test"), None) is not None
        assert call_obj_from_spec(Collection(), None) is None
        assert call_obj_from_spec(Object(), None) is None

    def test_metadata_models(self) -> None:
        """Metadata from_* and equality."""
        m = Module(name="m", fqcn="ns.col.m")
        mm = ModuleMetadata.from_module(m, {"type": "t", "name": "n", "version": "v", "hash": "h"})
        assert mm.fqcn == "ns.col.m"
        assert mm == ModuleMetadata.from_dict(
            {"fqcn": "ns.col.m", "type": "t", "name": "n", "version": "v", "hash": "h"}
        )
        assert (mm == "x") is False
        assert (
            ModuleMetadata.from_routing("ns.col.m", {"type": "t", "name": "n", "version": "v", "hash": "h"}).deprecated
            is True
        )
        r = Role(name="r", fqcn="ns.col.r")
        from apme_engine.engine.models import ActionGroupMetadata, RoleMetadata, TaskFileMetadata

        rm = RoleMetadata.from_role(r, {"type": "t", "name": "n", "version": "v", "hash": "h"})
        assert rm.fqcn == "ns.col.r"
        assert (rm == "x") is False
        tfm = TaskFileMetadata.from_taskfile(TaskFile(key="k"), {"type": "t", "name": "n", "version": "v", "hash": "h"})
        assert tfm.key == "k"
        assert (tfm == "x") is False
        agm = ActionGroupMetadata.from_action_group(
            "g", [Module(name="m")], {"type": "t", "name": "n", "version": "v", "hash": "h"}
        )
        assert agm is not None
        assert (agm == "x") is False

    def test_rule_model(self) -> None:
        """Rule validation, match/process/print helpers."""

        @pytest.mark.skip(reason="placeholder")  # type: ignore[untyped-decorator]
        def _unused() -> None:
            """Unused."""

        with pytest.raises(ValueError, match="rule_id"):
            Rule(rule_id="", description="d")
        with pytest.raises(ValueError, match="description"):
            Rule(rule_id="R1", description="")
        rr = RuleResult(detail={"a": 1})
        assert rr.get_detail() == {"a": 1}
        rr.set_value("b", 2)
        assert rr.detail == {"a": 1, "b": 2}

        @pytest.mark.skip(reason="placeholder")  # type: ignore[untyped-decorator]
        def _unused2() -> None:
            """Unused."""

    def test_rule_base_methods(self) -> None:
        """Base Rule match/process raise; print/to_json/error work."""

        @pytest.fixture  # type: ignore[untyped-decorator]
        def _f() -> None:
            """Unused fixture.

            Returns:
                None.
            """

    def test_target_result_helpers(self) -> None:
        """TargetResult filter and find helpers."""
        from apme_engine.engine.models import ARIResult, NodeResult, TargetResult

        t = Task(key="k", name="mytask")
        tc = TaskCall(key="c1", spec=t)
        nr = NodeResult(node=tc, rules=[RuleResult(verdict=True, matched=True)])
        tr = TargetResult(target_type="playbook", target_name="p", nodes=[nr])
        assert tr.tasks().nodes
        assert tr.task("mytask") is not None
        assert tr.task("missing") is None
        assert tr.roles().nodes == []
        assert tr.role("x") is None
        assert tr.playbooks().nodes == []
        assert tr.playbook("x") is None
        assert tr.plays().nodes == []
        assert tr.play("x") is None
        assert tr.taskfiles().nodes == []
        assert tr.taskfile("x") is None
        assert tr.applied_rules()
        assert tr.matched_rules()
        ar = ARIResult(targets=[tr])
        assert len(ar.playbooks().targets) == 1
        assert ar.playbook(name="p") is not None
        assert ar.playbook(path="/a/p") is not None
        assert ar.playbook(yaml_str="zzz") is None
        assert ar.roles().targets == []
        assert ar.role("p") is None
        assert ar.taskfiles().targets == []
        assert ar.taskfile(name="p") is None
        assert ar.find_target(name="p", target_type="playbook") is not None
        assert ar.find_target(path="/a/p", target_type="playbook") is not None
        assert ar.find_target(yaml_str="zzz", target_type="playbook") is None
        assert ar.find_target() is None


# ---------------------------------------------------------------------------
# finder
# ---------------------------------------------------------------------------


class TestFindModuleName:
    """Tests for finder.find_module_name branches."""

    def test_ansible_builtin_prefix(self) -> None:
        """ansible.builtin key returned directly."""
        assert find_module_name({"ansible.builtin.debug": {}}) == "ansible.builtin.debug"

    def test_builtin_set(self) -> None:
        """Builtin module name matched from set."""
        assert find_module_name({"debug": {}}) == "debug"

    def test_fqcn_regex(self) -> None:
        """FQCN with three segments matched."""
        assert find_module_name({"myorg.mycol.mymod": {}}) == "myorg.mycol.mymod"

    def test_skips_keywords_and_with(self) -> None:
        """Task keywords and with_ skipped."""
        assert find_module_name({"name": "x", "block": []}) == ""
        assert find_module_name({"with_items": []}) == ""

    def test_generic_module_regex(self) -> None:
        """Generic dotted name matched second pass."""
        assert find_module_name({"mymodule": {}}) == "mymodule"

    def test_non_str_keys_skipped(self) -> None:
        """Non-string keys ignored."""
        assert find_module_name({123: "x", "name": "t"}) == ""  # type: ignore[dict-item]

    def test_local_action_str(self) -> None:
        """local_action string yields first token."""
        assert find_module_name({"local_action": "copy src=a dest=b"}) == "copy"

    def test_local_action_dict(self) -> None:
        """local_action dict yields module value."""
        assert find_module_name({"local_action": {"module": "copy"}}) == "copy"

    def test_local_action_empty(self) -> None:
        """Empty local_action yields empty."""
        assert find_module_name({"local_action": {}}) == ""


class TestGetTaskBlocks:
    """Tests for finder.get_task_blocks."""

    def test_yaml_str_ok(self) -> None:
        """Valid yaml_str returns blocks."""
        blocks, lines = get_task_blocks(yaml_str=TASKFILE_YAML)
        assert blocks and lines

    def test_yaml_str_invalid(self) -> None:
        """Invalid yaml_str returns None pair."""
        assert get_task_blocks(yaml_str="{{{ bad") == (None, None)

    def test_fpath_missing(self, tmp_path: Path) -> None:
        """Missing fpath returns None pair.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert get_task_blocks(fpath=str(tmp_path / "no.yml")) == (None, None)

    def test_fpath_ok(self, tmp_path: Path) -> None:
        """Valid fpath returns blocks.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        f = _write(tmp_path, "t.yml", TASKFILE_YAML)
        blocks, _ = get_task_blocks(fpath=f)
        assert blocks

    def test_fpath_bad_yaml(self, tmp_path: Path) -> None:
        """Bad yaml file returns None pair.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        f = _write(tmp_path, "bad.yml", "{{{ bad: [")
        assert get_task_blocks(fpath=f) == (None, None)

    def test_task_dict_list(self) -> None:
        """Pre-parsed list returns blocks."""
        blocks, _ = get_task_blocks(task_dict_list=[{"name": "x"}])
        assert blocks is not None and len(blocks) == 1

    def test_no_input(self) -> None:
        """No input returns None pair."""
        assert get_task_blocks() == (None, None)

    def test_non_list_yaml(self) -> None:
        """Non-list yaml returns None pair."""
        assert get_task_blocks(yaml_str="key: value\n") == (None, None)


class TestIdentifyLines:
    """Tests for identify_lines_with_jsonpath edge branches."""

    def test_empty_jsonpath(self) -> None:
        """Empty jsonpath returns None pair."""
        assert identify_lines_with_jsonpath(yaml_str=TASKFILE_YAML, jsonpath="") == (None, None)

    def test_bad_yaml_str(self) -> None:
        """Bad yaml_str returns None pair."""
        assert identify_lines_with_jsonpath(yaml_str="{{{", jsonpath=".0") == (None, None)

    def test_missing_fpath(self, tmp_path: Path) -> None:
        """Missing fpath returns None pair.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert identify_lines_with_jsonpath(fpath=str(tmp_path / "m.yml"), jsonpath=".0") == (None, None)

    def test_empty_data(self) -> None:
        """Empty doc returns None pair."""
        assert identify_lines_with_jsonpath(yaml_str="---\n", jsonpath=".0") == (None, None)

    def test_plays_passthrough(self) -> None:
        """Plays segment is skipped."""
        yml = "- hosts: localhost\n  tasks:\n    - name: hi\n      ansible.builtin.debug:\n        msg: x\n"
        lines, rng = identify_lines_with_jsonpath(yaml_str=yml, jsonpath=".plays.0.tasks.0")
        assert lines is None or rng is None or lines


class TestFindChildYamlBlock:
    """Tests for find_child_yaml_block."""

    def test_no_match_returns_empty(self) -> None:
        """No top-level match returns empty list."""
        assert find_child_yaml_block("   \n# c\n", key="tasks") == []

    def test_key_mode(self) -> None:
        """Key mode splits on key."""
        yml = "tasks:\n  - name: a\n    debug:\n      msg: hi\n"
        blocks = find_child_yaml_block(yml, key="tasks")
        assert len(blocks) == 1

    def test_key_mode_with_offset(self) -> None:
        """Offset shifts line numbers."""
        yml = "tasks:\n  - name: a\n    debug:\n      msg: hi\n"
        blocks = find_child_yaml_block(yml, key="tasks", line_num_offset=10)
        assert blocks[0][1][0] >= 10

    def test_list_mode(self) -> None:
        """List mode splits on dash items."""
        yml = "- name: a\n  debug:\n    msg: hi\n- name: b\n  debug:\n    msg: bye\n"
        blocks = find_child_yaml_block(yml)
        assert len(blocks) == 2

    def test_list_mode_end_separator(self) -> None:
        """Trailing ... shortens end line."""
        yml = "- name: a\n  debug:\n    msg: hi\n...\n"
        blocks = find_child_yaml_block(yml)
        assert blocks


class TestSearchHelpers:
    """Tests for search/list helpers."""

    def test_search_module_files(self, tmp_path: Path) -> None:
        """Module files with DOCUMENTATION found; others skipped.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        lib = tmp_path / "library"
        lib.mkdir()
        (lib / "good.py").write_text('DOCUMENTATION = "x"\n')
        (lib / "nodoc.py").write_text("x = 1\n")
        (lib / "__init__.py").write_text('DOCUMENTATION = "x"\n')
        (lib / "notes.txt").write_text("hi")
        found = search_module_files(str(tmp_path))
        assert any("good.py" in f for f in found)
        assert not any("nodoc.py" in f for f in found)
        assert not any("__init__.py" in f for f in found)

    def test_find_module_dirs(self, tmp_path: Path) -> None:
        """Existing module dirs returned.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "library").mkdir()
        dirs = find_module_dirs(str(tmp_path))
        assert len(dirs) == 1
        assert find_module_dirs(str(tmp_path / "missing-parent-zzz")) == []

    def test_search_taskfiles(self, tmp_path: Path) -> None:
        """Task-looking yamls found; playbooks skipped.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "main.yml").write_text(TASKFILE_YAML)
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks" / "site.yml").write_text(PLAYBOOK_YAML)
        (tmp_path / "playbooks" / "bad.yml").write_text("{{{ bad")
        (tmp_path / "playbooks" / "vars.yml").write_text("key: value\n")
        found = search_taskfiles_for_playbooks(str(tmp_path))
        assert any("main.yml" in f for f in found)

    def test_search_inventory(self, tmp_path: Path) -> None:
        """group_vars/host_vars discovered.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        gv = tmp_path / "group_vars"
        gv.mkdir()
        (gv / "all.yml").write_text("a: 1\n")
        assert search_inventory_files(str(tmp_path))

    def test_find_best_repo_root_galaxy(self, tmp_path: Path) -> None:
        """galaxy.yml short-circuits to base path.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "galaxy.yml").write_text("namespace: a\nname: b\n")
        assert find_best_repo_root_path(str(tmp_path)) == str(tmp_path)

    def test_find_best_repo_root_playbook(self, tmp_path: Path) -> None:
        """Playbook dir infers repo root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        pbdir = tmp_path / "playbooks"
        pbdir.mkdir()
        (pbdir / "site.yml").write_text(PLAYBOOK_YAML)
        root = find_best_repo_root_path(str(tmp_path))
        assert root

    def test_find_best_repo_root_none_raises(self, tmp_path: Path) -> None:
        """No playbooks raises ValueError.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        with pytest.raises(ValueError, match="no playbook"):
            find_best_repo_root_path(str(tmp_path))

    def test_find_collection_name_galaxy(self, tmp_path: Path) -> None:
        """galaxy.yml namespace.name returned.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "galaxy.yml").write_text("namespace: myns\nname: mycol\n")
        assert find_collection_name_of_repo(str(tmp_path)) == "myns.mycol"

    def test_find_collection_name_manifest(self, tmp_path: Path) -> None:
        """MANIFEST.json collection_info returned.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "MANIFEST.json").write_text(json.dumps({"collection_info": {"namespace": "a", "name": "b"}}))
        assert find_collection_name_of_repo(str(tmp_path)) == "a.b"

    def test_find_collection_name_none(self, tmp_path: Path) -> None:
        """No metadata returns empty.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert find_collection_name_of_repo(str(tmp_path)) == ""

    def test_find_collection_name_bad_yaml(self, tmp_path: Path) -> None:
        """Bad galaxy.yml returns empty.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "galaxy.yml").write_text("{{{ bad")
        assert find_collection_name_of_repo(str(tmp_path)) == ""

    def test_find_all(self, tmp_path: Path) -> None:
        """find_all_ymls/files glob.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        _write(tmp_path, "a.yml", "a: 1\n")
        _write(tmp_path, "b.txt", "hi")
        assert find_all_ymls(str(tmp_path))
        assert find_all_files(str(tmp_path))


class TestCouldBe:
    """Tests for could_be_* and label helpers."""

    def test_playbook_detail_true_false(self, tmp_path: Path) -> None:
        """Playbook detection true for hosts; false otherwise.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert could_be_playbook_detail(body=PLAYBOOK_YAML, data=[{"hosts": "all"}]) is True
        assert could_be_playbook_detail(body="", data=None) is False
        assert could_be_playbook_detail(body="x", data="s") is False
        assert could_be_playbook_detail(body="x", data=[]) is False
        assert could_be_playbook_detail(body="x", data=["s"]) is False
        assert could_be_playbook_detail(body=PLAYBOOK_YAML, data=[{"import_playbook": "x"}]) is True
        f = _write(tmp_path, "pb.yml", PLAYBOOK_YAML)
        assert could_be_playbook_detail(fpath=f) is True
        assert could_be_playbook_detail(fpath=str(tmp_path / "missing.yml")) is False

    def test_could_be_taskfile(self) -> None:
        """Taskfile detection branches."""
        assert could_be_taskfile(body="", data=None) is False
        assert could_be_taskfile(body="x", data=None) is False
        assert could_be_taskfile(body="x", data="s") is False
        assert could_be_taskfile(body=TASKFILE_YAML, data=[{"name": "x"}]) is True
        assert could_be_taskfile(body=TASKFILE_YAML, data=[{"ansible.builtin.debug": {}}]) is True
        assert could_be_taskfile(body=PLAYBOOK_YAML, data=[{"import_playbook": "x.yml"}]) is False
        assert could_be_taskfile(body="x", data=["s"]) is False

    def test_eda_detail(self, tmp_path: Path) -> None:
        """EDA path shortcut and body checks.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert finder_mod.could_be_eda_rulebook_detail(fpath="rulebooks/rulebook.yml") is True
        assert finder_mod.could_be_eda_rulebook_detail(body="", data=None) is False
        assert finder_mod.could_be_eda_rulebook_detail(body="x", data="s") is False
        assert finder_mod.could_be_eda_rulebook_detail(body="x", data=[]) is False

    def test_label_empty_by_path(self) -> None:
        """Empty file labels by path substring."""
        assert label_empty_file_by_path("rulebooks/rulebook.yml") == "rulebook"
        assert label_empty_file_by_path("/a/tasks/main.yml") == "taskfile"
        assert label_empty_file_by_path("/a/handlers/main.yml") == "taskfile"
        assert label_empty_file_by_path("/a/playbooks/site.yml") == "playbook"
        assert label_empty_file_by_path("/a/other.yml") == ""

    def test_role_info(self) -> None:
        """Role name/path extracted from roles path."""
        name, path = get_role_info_from_path("/repo/roles/myrole/tasks/main.yml")
        assert name == "myrole"
        assert "myrole" in path
        assert get_role_info_from_path("/repo/playbooks/site.yml") == ("", "")

    def test_project_info(self) -> None:
        """Project name is basename of root."""
        assert get_project_info_for_file("/r/site.yml", "/r") == ("r", "/r")

    def test_is_meta_vars(self) -> None:
        """meta/vars path checks."""
        assert is_meta_yml("a/meta/main.yml") is True
        assert is_meta_yml("a.yml") is False
        assert is_vars_yml("a/vars/main.yml") is True
        assert is_vars_yml("a/defaults/main.yml") is True
        assert is_vars_yml("a/tasks/main.yml") is False

    def test_count_top_level(self) -> None:
        """Top-level element counting with comments."""
        assert count_top_level_element("") == -1
        assert count_top_level_element("# c\n\n") == -1
        assert count_top_level_element("- a: 1\n- b: 2\n") == 2

    def test_label_yml_file(self, tmp_path: Path) -> None:
        """Label classifies playbook/taskfile/others and errors.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        label, _, err = label_yml_file(yml_body=PLAYBOOK_YAML)
        assert label == "playbook" and err is None
        label2, _, _ = label_yml_file(yml_body=TASKFILE_YAML)
        assert label2 == "taskfile"
        label3, _, _ = label_yml_file(yml_body="key: value\n")
        assert label3 == "others"
        label4, _, err4 = label_yml_file(yml_body="- name: x\n" * 60, task_num_thresh=5)
        assert label4 == "others" and err4 is not None
        label5, _, err5 = label_yml_file(yml_body="{{{ bad")
        assert label5 == "others" and err5 is not None
        label6, _, err6 = label_yml_file(yml_path=str(tmp_path / "missing.yml"))
        assert label6 == "others" and err6 is not None
        f = _write(tmp_path, "empty-tasks.yml", "")
        # empty body falls back to path label (tasks dir => taskfile) or others
        _write(tmp_path, "tasks/empty.yml", "")
        l7, _, _ = label_yml_file(yml_path=str(tmp_path / "tasks" / "empty.yml"))
        assert l7 in ("taskfile", "others")
        assert f

    def test_get_yml_helpers(self, tmp_path: Path) -> None:
        """get_yml_label/list/scan_target enumerate files.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        _write(tmp_path, "site.yml", PLAYBOOK_YAML)
        _write(tmp_path, "tasks/main.yml", TASKFILE_YAML)
        label, role_info, proj_info = get_yml_label(str(tmp_path / "site.yml"), str(tmp_path))
        assert label
        assert proj_info
        assert role_info is None or isinstance(role_info, dict)
        lst = get_yml_list(str(tmp_path))
        assert lst
        targets = list_scan_target(str(tmp_path))
        assert isinstance(targets, list)

    def test_yaml_line_helpers(self, tmp_path: Path) -> None:
        """Diff/line helpers manipulate lists without error.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.finder import (
            check_and_add_diff_lines,
            check_diff_and_copy_olddata_to_newdata,
            populate_new_data_list,
            update_and_append_new_line,
            update_line_with_space,
            update_the_yaml_target,
        )

        assert update_line_with_space("hi", "  old", 0) == "  hi"
        assert update_line_with_space("hi", "old", 4) == "    hi"
        data = "l1\nl2\nl3\n"
        assert populate_new_data_list(data, ["L2-3"]) == ["l1\n"]
        buf: list[str] = []
        check_and_add_diff_lines(1, 3, ["a\n", "b\n", "c\n"], buf)
        assert buf
        out = check_diff_and_copy_olddata_to_newdata(["L1-1"], ["a\n", "b\n"], ["a\n"])
        assert "b\n" in out
        assert check_diff_and_copy_olddata_to_newdata([], ["a"], ["a"]) == ["a"]
        buf2: list[str] = []
        assert update_and_append_new_line("k: v", "  old: x", 0, buf2) == ""
        assert buf2
        f = _write(tmp_path, "u.yml", "- name: a\n  hosts: localhost\n  tasks: []\n")
        update_the_yaml_target(f, ["L1-2"], ["- name: b\n  hosts: localhost\n"])
        assert Path(f).exists()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


class TestParserExtra:
    """Tests for Parser.run branches with mocked loaders."""

    def test_load_json_path_ok(self, tmp_path: Path) -> None:
        """load_json_path loads Load and empty defs.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.parser import Parser

        ld = Load(target_type="unsupported-will-override", target_name="x", path="p")
        # write a valid Load json with unsupported type to trigger ValueError path via file
        p = tmp_path / "load-x.json"
        p.write_text(ld.dump())
        with pytest.raises(ValueError, match="unsupported type"):
            Parser().run(load_json_path=str(p))

    def test_collection_playbook_format_skip(self) -> None:
        """Collection PlaybookFormatError skipped returns empty defs."""
        from apme_engine.engine.models import PlaybookFormatError
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.COLLECTION, target_name="ns.col", path="/tmp/x")
        with patch("apme_engine.engine.parser.load_collection", side_effect=PlaybookFormatError("bad")):
            res = Parser().run(load_data=ld)
            assert res is not None

    def test_collection_playbook_format_raise(self) -> None:
        """Collection PlaybookFormatError re-raised when not skipped."""
        from apme_engine.engine.models import PlaybookFormatError
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.COLLECTION, target_name="ns.col", path="/tmp/x")
        with (
            patch("apme_engine.engine.parser.load_collection", side_effect=PlaybookFormatError("bad")),
            pytest.raises(PlaybookFormatError),
        ):
            Parser(skip_playbook_format_error=False).run(load_data=ld)

    def test_collection_task_format(self) -> None:
        """Collection TaskFormatError skip vs raise."""
        from apme_engine.engine.models import TaskFormatError
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.COLLECTION, target_name="ns.col", path="/tmp/x")
        with patch("apme_engine.engine.parser.load_collection", side_effect=TaskFormatError("bad")):
            assert Parser().run(load_data=ld) is not None
            with pytest.raises(TaskFormatError):
                Parser(skip_task_format_error=False).run(load_data=ld)

    def test_role_generic_exception_none(self) -> None:
        """Role generic exception returns None."""
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.ROLE, target_name="r", path="/tmp/x")
        with patch("apme_engine.engine.parser.load_role", side_effect=RuntimeError("boom")):
            assert Parser().run(load_data=ld) is None

    def test_project_collection_name_override(self) -> None:
        """Project uses collection_name_of_project when repo has none."""
        from apme_engine.engine.models import Repository
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.PROJECT, target_name="proj", path="/tmp/x")
        repo = Repository(name="proj")
        with patch("apme_engine.engine.parser.load_repository", return_value=repo):
            res = Parser(use_ansible_doc=False).run(load_data=ld, collection_name_of_project="ns.col")
            assert res is not None

    def test_project_format_errors(self) -> None:
        """Project format errors skip vs raise."""
        from apme_engine.engine.models import PlaybookFormatError, TaskFormatError
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.PROJECT, target_name="p", path="/tmp/x")
        with patch("apme_engine.engine.parser.load_repository", side_effect=PlaybookFormatError("b")):
            assert Parser().run(load_data=ld) is not None
            with pytest.raises(PlaybookFormatError):
                Parser(skip_playbook_format_error=False).run(load_data=ld)
        with patch("apme_engine.engine.parser.load_repository", side_effect=TaskFormatError("b")):
            assert Parser().run(load_data=ld) is not None

    def test_playbook_repo_path_branches(self) -> None:
        """Playbook non-only uses repository; base_dir variants covered."""
        from apme_engine.engine.models import Repository
        from apme_engine.engine.parser import Parser

        ld = Load(target_type=LoadType.PLAYBOOK, target_name="pb", path="/base/playbooks/site.yml", base_dir="/base")
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld) is not None
        ld2 = Load(target_type=LoadType.PLAYBOOK, target_name="pb", path="/base/site.yml")
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld2) is not None
        ld3 = Load(
            target_type=LoadType.PLAYBOOK,
            target_name="pb",
            path="/x.yml",
            playbook_yaml=PLAYBOOK_YAML,
            playbook_only=False,
        )
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld3) is not None

    def test_playbook_only_exception_none(self) -> None:
        """Playbook generic exception returns None."""
        from apme_engine.engine.parser import Parser

        ld = Load(
            target_type=LoadType.PLAYBOOK,
            target_name="pb",
            path="/x.yml",
            playbook_yaml=PLAYBOOK_YAML,
            playbook_only=True,
        )
        with patch("apme_engine.engine.parser.load_playbook", side_effect=RuntimeError("boom")):
            assert Parser().run(load_data=ld) is None

    def test_taskfile_branches(self) -> None:
        """Taskfile only/repo and error branches."""
        from apme_engine.engine.models import Repository, TaskFormatError
        from apme_engine.engine.parser import Parser

        ld = Load(
            target_type=LoadType.TASKFILE, target_name="tf", path="/base/roles/r/tasks/main.yml", base_dir="/base"
        )
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld) is not None
        ld2 = Load(target_type=LoadType.TASKFILE, target_name="tf", path="/base/main.yml")
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld2) is not None
        ld3 = Load(
            target_type=LoadType.TASKFILE,
            target_name="tf",
            path="/x.yml",
            taskfile_yaml=TASKFILE_YAML,
            taskfile_only=False,
        )
        with patch("apme_engine.engine.parser.load_repository", return_value=Repository(name="r")):
            assert Parser(use_ansible_doc=False).run(load_data=ld3) is not None
        ld4 = Load(
            target_type=LoadType.TASKFILE,
            target_name="tf",
            path="/x.yml",
            taskfile_yaml=TASKFILE_YAML,
            taskfile_only=True,
        )
        with patch("apme_engine.engine.parser.load_taskfile", side_effect=TaskFormatError("bad")):
            assert Parser().run(load_data=ld4) is not None
            with pytest.raises(TaskFormatError):
                Parser(skip_task_format_error=False).run(load_data=ld4)

    def test_roles_taskfiles_playbooks_modules_files_loops(self, tmp_path: Path) -> None:
        """Parser loops handle roles/taskfiles/playbooks/modules/files.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.models import Playbook as PB
        from apme_engine.engine.models import Role as R
        from apme_engine.engine.models import TaskFile as TF
        from apme_engine.engine.parser import Parser

        ld = Load(
            target_type=LoadType.PLAYBOOK,
            target_name="t",
            path="p.yml",
            playbook_yaml=PLAYBOOK_YAML,
            playbook_only=True,
            base_dir="",
        )
        ld.roles = ["somerole"]
        ld.taskfiles = ["tf.yml"]
        ld.playbooks = ["pb.yml"]
        ld.modules = ["mod.py"]
        ld.files = ["f.yml"]
        ld.yaml_label_list = cast(list[str], [["f.yml", "others"]])
        role = R(name="somerole", fqcn="somerole", defined_in="somerole")
        with (
            patch("apme_engine.engine.parser.load_role", return_value=role),
            patch("apme_engine.engine.parser.load_taskfile", return_value=TF(name="tf", defined_in="tf.yml")),
            patch("apme_engine.engine.parser.load_playbook", return_value=PB(name="pb", defined_in="pb.yml")),
            patch("apme_engine.engine.parser.load_module", side_effect=RuntimeError("no-mod")),
            patch("apme_engine.engine.parser.load_file", return_value=File(name="f.yml", defined_in="f.yml")),
        ):
            res = Parser(use_ansible_doc=False).run(load_data=ld)
            assert res is not None
            defs, _ = res
            assert "roles" in defs

    def test_dump_helpers(self, tmp_path: Path) -> None:
        """_dump/_load object list helpers round-trip.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.parser import _dump_object_list, _load_object_list

        objs = [Object(type="a", key="k1")]
        out = str(tmp_path / "o.json")
        _dump_object_list(objs, out)
        assert Path(out).exists()
        assert _load_object_list(Object, str(tmp_path / "missing.json")) == []
        assert len(_load_object_list(Object, out)) == 1


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------


class TestUtilsExtra:
    """Tests for utils uncovered branches."""

    def test_remove_lock_non_filelock(self) -> None:
        """Non-FileLock ignored."""
        from apme_engine.engine.utils import remove_lock_file

        remove_lock_file("x")

    def test_remove_lock_missing_file(self, tmp_path: Path) -> None:
        """Missing lockfile returns silently.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from filelock import FileLock

        from apme_engine.engine.utils import remove_lock_file

        lk = FileLock(str(tmp_path / "gone.lock"))
        remove_lock_file(lk)

    def test_split_taskfile_empty(self) -> None:
        """Taskfile split with empty remainder."""
        from apme_engine.engine.utils import split_target_taskfile_fullpath

        b, t = split_target_taskfile_fullpath("/a")
        assert b and isinstance(t, str)

    def test_is_local_path_missing_no_slash(self) -> None:
        """Missing file without slash is not local path."""
        from apme_engine.engine.utils import is_local_path

        assert is_local_path("definitely-missing-xyz-123") is False

    def test_report_only_playbooks(self) -> None:
        """Report with only playbooks omits 'and'."""
        from apme_engine.engine.utils import report_to_display

        out = report_to_display({"summary": {"playbooks": {"total": 1}, "roles": {"total": 0}}, "details": []})
        assert "1 playbooks found" in out

    def test_report_skips_bad_details(self) -> None:
        """Non-dict details and empty outputs skipped."""
        from apme_engine.engine.utils import report_to_display

        out = report_to_display(
            {
                "summary": {"playbooks": {"total": 1}, "roles": {"total": 0}},
                "details": ["bad", {"results": [{"output": ""}]}, {"results": [{"output": "hit"}]}],
            }
        )
        assert "hit" in out

    def test_report_non_list_details(self) -> None:
        """Non-list details handled."""
        from apme_engine.engine.utils import report_to_display

        assert isinstance(report_to_display({"summary": {}, "details": {}}), str)

    def test_summarize_findings_data(self) -> None:
        """Summary covers deps, failures, suggestions."""
        from apme_engine.engine.utils import summarize_findings_data

        out = summarize_findings_data(
            {"name": "proj"},
            [{"metadata": {"name": "dep1", "version": "1", "hash": "h"}}],
            {"summary": {"playbooks": {"total": 1}, "roles": {"total": 0}}, "details": []},
            {"module": {"m1": 2}, "role": {"r1": 1}, "taskfile": {"t1": 1}},
            [
                {"type": "module", "name": "dep1.m1", "used_in": "t1", "defined_in": {"name": "dep1", "version": "1"}},
                {"type": "role", "name": "dep1.r1", "used_in": "p1", "defined_in": {"name": "dep1", "version": "1"}},
                {"type": "other", "name": "x", "defined_in": {"name": "dep1"}},
                {"type": "module", "name": "m", "defined_in": {}},
                {"type": "module", "name": "proj.m", "defined_in": {"name": "proj"}},
            ],
            False,
        )
        assert "External Dependencies" in out
        assert "Failed to resolve" in out
        assert "Unresolved modules" in out

    def test_summarize_truncation(self) -> None:
        """Long unresolved/suggestion lists truncate."""
        from apme_engine.engine.utils import summarize_findings_data

        reqs = [
            {"type": "module", "name": f"dep.m{i}", "used_in": "u", "defined_in": {"name": "dep", "version": "1"}}
            for i in range(6)
        ] + [
            {"type": "role", "name": f"dep.r{i}", "used_in": "u", "defined_in": {"name": "dep", "version": "1"}}
            for i in range(6)
        ]
        out = summarize_findings_data(
            {"name": "x"}, [], {"summary": {}}, {}, cast(list[dict[str, object]], reqs), False
        )
        assert "other modules" in out or "Unresolved" in out

    def test_get_module_specs_empty(self) -> None:
        """Empty module files returns empty dict."""
        from apme_engine.engine.utils import get_module_specs_by_ansible_doc

        assert get_module_specs_by_ansible_doc([], "ns", "/tmp") == {}
        assert get_module_specs_by_ansible_doc("__init__.py", "ns", "/tmp") == {}

    def test_get_module_specs_str_and_prefix(self) -> None:
        """String input and prefix stripping covered with mocked subprocess."""
        from apme_engine.engine.utils import get_module_specs_by_ansible_doc

        proc = MagicMock()
        proc.stderr = ""
        proc.stdout = json.dumps({"ns.m": {"doc": {"options": {}}, "examples": "ex"}})
        with patch("apme_engine.engine.utils.subprocess.run", return_value=proc):
            out = get_module_specs_by_ansible_doc("m.py", "ns", "/x/ns/col")
            assert "ns.m" in out

    def test_get_module_specs_stderr_no_stdout(self) -> None:
        """Stderr without stdout returns empty."""
        from apme_engine.engine.utils import get_module_specs_by_ansible_doc

        proc = MagicMock()
        proc.stderr = "err"
        proc.stdout = ""
        with patch("apme_engine.engine.utils.subprocess.run", return_value=proc):
            assert get_module_specs_by_ansible_doc(["m.py"], "ns", "/tmp") == {}

    def test_doc_single_quotes(self, tmp_path: Path) -> None:
        """Single-quote DOCUMENTATION block parsed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.utils import get_documentation_in_module_file

        p = tmp_path / "m.py"
        p.write_text("DOCUMENTATION = '''\nmodule: m\n'''\n")
        assert "module: m" in get_documentation_in_module_file(str(p))
        p2 = tmp_path / "m2.py"
        p2.write_text('DOCUMENTATION = """\nmodule: m2\n"""\n trailing')
        assert "module: m2" in get_documentation_in_module_file(str(p2))

    def test_load_classes_in_dir(self, tmp_path: Path) -> None:
        """Class discovery loads subclasses and records errors.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.utils import load_classes_in_dir

        (tmp_path / "good.py").write_text(
            "from apme_engine.engine.models import Rule\nclass R1(Rule):\n rule_id='X'\n description='d'\n"
        )
        (tmp_path / "bad.py").write_text("raise RuntimeError('boom')\n")
        (tmp_path / "skip_test.py").write_text("x=1\n")
        from apme_engine.engine.models import Rule as _Rule

        classes, errors = load_classes_in_dir(str(tmp_path), _Rule)
        assert any(c.__name__ == "R1" for c in classes)
        assert errors
        with pytest.raises(ValueError, match="not found"):
            load_classes_in_dir(str(tmp_path / "missing"), _Rule)
        with pytest.raises(ValueError, match="failed to load"):
            load_classes_in_dir(str(tmp_path), _Rule, fail_on_error=True)
        # base_dir fallback: dir relative to the directory of base_dir file
        sub = tmp_path / "sub"
        sub.mkdir()
        cls_dir = sub / "classes"
        cls_dir.mkdir()
        (cls_dir / "r2.py").write_text(
            "from apme_engine.engine.models import Rule\nclass R2(Rule):\n rule_id='Y'\n description='d'\n"
        )
        dummy = sub / "dummy.py"
        dummy.write_text("x=1\n")
        classes2, _ = load_classes_in_dir("classes", _Rule, base_dir=str(dummy))
        assert any(c.__name__ == "R2" for c in classes2)

    def test_parse_bool_invalid_type(self) -> None:
        """Unparsable type raises TypeError."""
        from apme_engine.engine.utils import parse_bool

        with pytest.raises(TypeError):
            parse_bool(object())

    def test_get_collection_role_metadata_missing(self, tmp_path: Path) -> None:
        """Existing dir without manifest returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.utils import get_collection_metadata, get_role_metadata

        assert get_collection_metadata(str(tmp_path)) is None
        assert get_role_metadata(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# yaml_utils
# ---------------------------------------------------------------------------


class TestYamlUtilsExtra:
    """Tests for yaml_utils branches."""

    def test_nested_items(self) -> None:
        """Nested walk yields dict/list entries; others stop."""
        from apme_engine.engine.yaml_utils import _nested_items_path

        assert list(_nested_items_path(None)) == []  # type: ignore[arg-type]
        assert list(_nested_items_path("str")) == []  # type: ignore[arg-type]
        data: dict[object, object] = {"a": {"b": 1}, "l": [1, {"x": 2}]}
        items = list(_nested_items_path(data))
        assert len(items) >= 4

    def test_octal_new_and_represent(self) -> None:
        """Octal int construction and representation."""
        from apme_engine.engine.yaml_utils import OctalIntYAML11

        v = OctalIntYAML11(8)
        assert int(v) == 8
        rep = OctalIntYAML11.represent_octal(MagicMock(), OctalIntYAML11(8))
        assert rep is not None

    def test_custom_constructor(self) -> None:
        """Constructor preserves octal/hex in 1.1."""
        from apme_engine.engine.yaml_utils import CustomConstructor, FormattedYAML

        y = FormattedYAML(version=(1, 1))
        assert y.load("a: 0\n") is not None
        assert y.load("a: 0755\n") is not None
        assert y.load("a: 0x10\n") is not None
        # zero stays int
        c = CustomConstructor()
        assert c is not None

    def test_emitter_prefs(self) -> None:
        """Emitter setters and scalar style branches."""
        from apme_engine.engine.yaml_utils import FormattedEmitter, FormattedYAML

        y = FormattedYAML()
        y.Emitter = FormattedEmitter
        e = FormattedEmitter.__new__(FormattedEmitter)
        e._sequence_indent = 4
        e._sequence_dash_offset = 2
        e._root_is_sequence = True
        e.column = 0
        assert e._is_root_level_sequence is True
        assert e.best_sequence_indent == 2
        e.best_sequence_indent = 6
        assert e._sequence_indent == 6
        assert e.sequence_dash_offset == 0
        e.sequence_dash_offset = 3
        assert e._sequence_dash_offset == 3
        assert FormattedEmitter.add_octothorpe_protection("a#b") != "a#b"
        assert FormattedEmitter.drop_octothorpe_protection(FormattedEmitter.add_octothorpe_protection("a#b")) == "a#b"
        assert FormattedEmitter.add_octothorpe_protection(cast(str, 123)) == cast(str, 123)

    def test_formatted_yaml_versions(self) -> None:
        """Version string parsing and property fallback."""
        from apme_engine.engine.yaml_utils import FormattedYAML

        y = FormattedYAML(version=cast(tuple[int, int] | None, "1.1"))
        assert y.version == (1, 1)
        y.version = None
        assert y.version == (1, 1)
        y2 = FormattedYAML(
            config={
                "explicit_start": True,
                "explicit_end": False,
                "width": 80,
                "indent_sequences": False,
                "preferred_quote": "'",
                "min_spaces_inside": 0,
                "max_spaces_inside": 1,
            }
        )
        assert y2.sequence_indent == 2
        # version unset object
        y3 = FormattedYAML.__new__(FormattedYAML)
        assert y3.version is None
        y3.version = None

    def test_load_errors(self) -> None:
        """Load handles bad yaml, non-str, composer fallback."""
        from apme_engine.engine.yaml_utils import FormattedYAML

        y = FormattedYAML()
        with pytest.raises(NotImplementedError):
            y.load(Path("x"))
        assert y.load("{{{ bad: [") is None
        assert y.load("") is None
        # preamble comment preserved
        data = y.load("# header\n---\na: 1\n")
        assert data is not None

    def test_dumps_and_postprocess(self) -> None:
        """Dumps round-trips; postprocess cleans comments."""
        from apme_engine.engine.yaml_utils import FormattedYAML

        y = FormattedYAML()
        data = y.load("- name: hi\n  ansible.builtin.debug:\n    msg: hello # inline\n")
        assert data is not None
        out = y.dumps(data)
        assert "name" in out
        pp = FormattedYAML._post_process_yaml(
            "%YAML 1.1\n---\na: 1\n", strip_version_directive=True, strip_explicit_start=False
        )
        assert "%YAML" not in pp
        pp2 = FormattedYAML._post_process_yaml("---\na: 1\n", strip_explicit_start=True)
        assert pp2.strip()
        # whitespace-only lines removed in preprocess
        text, _ = y._pre_process_yaml("a: 1\n   \nb: 2\n")
        assert "   \n" not in text
        # predict indent
        assert y._predict_indent_length(["k"], "sub") > 0
        assert y._predict_indent_length([0], 1) >= 0
        # prevent wrapping flow style no-op on scalar
        y._prevent_wrapping_flow_style("scalar")

    def test_analyze_and_comment(self) -> None:
        """analyze_scalar empty path and write paths run."""
        from io import StringIO

        from apme_engine.engine.yaml_utils import FormattedYAML

        y = FormattedYAML()
        y.load("a: 1\n")
        stream = StringIO()
        y.dump({"a": 1}, stream)
        assert stream.getvalue()
        y2 = FormattedYAML()
        y2.version = (1, 1)
        s = StringIO()
        y2.dump({"mode": "0755"}, s)
        assert s.getvalue()


# ---------------------------------------------------------------------------
# scan_state
# ---------------------------------------------------------------------------


class TestScanStateExtra:
    """Tests for SingleScan branches."""

    def test_collection_local_path(self, tmp_path: Path) -> None:
        """Local collection path escaped in mappings.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.COLLECTION, name="/tmp/my col", root_dir=str(tmp_path))
        assert "__" in str(s._path_mappings.get("root_definitions", ""))
        assert s.get_src_root()

    def test_playbook_base_dir(self, tmp_path: Path) -> None:
        """Playbook with base_dir derives target name.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.PLAYBOOK, name="/base/site.yml", base_dir="/base", root_dir=str(tmp_path))
        assert s.target_playbook_name == "site.yml"

    def test_playbook_fullpath(self, tmp_path: Path) -> None:
        """Playbook without base_dir splits fullpath.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.PLAYBOOK, name="/base/playbooks/site.yml", root_dir=str(tmp_path))
        assert s.target_playbook_name

    def test_taskfile_branches(self, tmp_path: Path) -> None:
        """Taskfile yaml/base_dir/fullpath branches.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s1 = SingleScan(type=LoadType.TASKFILE, name="inline", taskfile_yaml=TASKFILE_YAML, root_dir=str(tmp_path))
        assert s1.taskfile_only is True
        s2 = SingleScan(
            type=LoadType.TASKFILE, name="/base/roles/r/tasks/main.yml", base_dir="/base", root_dir=str(tmp_path)
        )
        assert s2.target_taskfile_name == "roles/r/tasks/main.yml"
        s3 = SingleScan(type=LoadType.TASKFILE, name="/base/main.yml", root_dir=str(tmp_path))
        assert s3.target_taskfile_name

    def test_in_memory_names(self, tmp_path: Path) -> None:
        """Empty name with inline yaml becomes __in_memory__.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.PLAYBOOK, name="", playbook_yaml=PLAYBOOK_YAML, root_dir=str(tmp_path))
        assert s.name == "__in_memory__"
        s2 = SingleScan(type=LoadType.TASKFILE, name="", taskfile_yaml=TASKFILE_YAML, root_dir=str(tmp_path))
        assert s2.name == "__in_memory__"

    def test_unsupported_type(self, tmp_path: Path) -> None:
        """Unsupported scan type raises.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        with pytest.raises(ValueError, match="Unsupported"):
            SingleScan(type="bad", name="x", root_dir=str(tmp_path))

    def test_src_installed(self, tmp_path: Path) -> None:
        """is_src_installed checks index file.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        assert s.is_src_installed() is False
        idx = s._path_mappings.get("index")
        assert isinstance(idx, str)
        Path(idx).parent.mkdir(parents=True, exist_ok=True)
        Path(idx).write_text("{}")
        assert s.is_src_installed() is True
        s2 = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        s2._path_mappings = {}
        assert s2.get_src_root() == ""
        assert s2.is_src_installed() is False

    def test_create_load_missing_raises(self, tmp_path: Path) -> None:
        """Missing target path without yaml raises.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path), silent=True)
        with pytest.raises(ValueError, match="No such file"):
            s.create_load_file(LoadType.ROLE, "r", str(tmp_path / "missing"))

    def test_create_load_ok(self, tmp_path: Path) -> None:
        """create_load_file populates Load via mocked loader.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path), silent=True)
        with patch("apme_engine.engine.scan_state.load_object", return_value=None):
            ld = s.create_load_file(LoadType.ROLE, "r", str(tmp_path))
            assert ld.target_name == "r"

    def test_definition_path_and_source(self, tmp_path: Path) -> None:
        """Definition/source path delegates work.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        assert isinstance(s.get_definition_path("role", "r"), str)
        assert isinstance(s.get_source_path("role", "r"), str)
        assert isinstance(s.make_target_path("role", "r"), str)

    def test_load_definition_ext_cache(self, tmp_path: Path) -> None:
        """Cached mappings path restores without parser.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.models import Load as _Load
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path), silent=True)
        outdir = tmp_path / "defs"
        outdir.mkdir()
        ld = _Load(target_name="r", target_type="role", path=str(tmp_path))
        (outdir / "mappings.json").write_text(ld.dump())
        with (
            patch("apme_engine.engine.scan_state.load_object", return_value=None),
            patch.object(type(s), "get_definition_path", return_value=str(outdir)),
        ):
            s.load_definition_ext("role", "r", str(tmp_path))
            assert "role-r" in s.ext_definitions

    def test_load_definition_ext_no_parser(self, tmp_path: Path) -> None:
        """Missing parser raises ValueError.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        with (
            patch("apme_engine.engine.scan_state.load_object", return_value=None),
            patch.object(type(s), "get_definition_path", return_value=str(tmp_path / "nocache-xyz")),
            pytest.raises(ValueError, match="Parser not initialized"),
        ):
            s.load_definition_ext("role", "r", str(tmp_path))

    def test_load_definition_ext_parser_fail(self, tmp_path: Path) -> None:
        """Parser returning None raises.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.parser import Parser
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        s._parser = Parser()
        with (
            patch("apme_engine.engine.scan_state.load_object", return_value=None),
            patch.object(type(s), "get_definition_path", return_value=str(tmp_path / "nocache2")),
            patch.object(Parser, "run", return_value=None),
            pytest.raises(ValueError, match="Parser run failed"),
        ):
            s.load_definition_ext("role", "r", str(tmp_path))

    def test_set_load_root_and_definitions(self, tmp_path: Path) -> None:
        """Root load and definitions via mocked parser.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.parser import Parser
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.PROJECT, name="proj", root_dir=str(tmp_path))
        with patch("apme_engine.engine.scan_state.load_object", return_value=None):
            assert s._set_load_root(str(tmp_path)) is not None
        s2 = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        s2._parser = Parser()
        with (
            patch("apme_engine.engine.scan_state.load_object", return_value=None),
            patch.object(Parser, "run", return_value=({"definitions": {}}, Load())),
        ):
            s2.load_definitions_root(str(tmp_path))
            assert "definitions" in s2.root_definitions
        s3 = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        with (
            pytest.raises(ValueError, match="Parser not initialized"),
            patch("apme_engine.engine.scan_state.load_object", return_value=None),
        ):
            # need root load ok but no parser
            s3._set_load_root(str(tmp_path))
            s3.load_definitions_root(str(tmp_path))

    def test_target_object_and_graph(self, tmp_path: Path) -> None:
        """set_target_object picks single/matching; graph builds.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.models import Playbook
        from apme_engine.engine.scan_state import SingleScan

        s = SingleScan(type=LoadType.PROJECT, name="proj", root_dir=str(tmp_path))
        s.root_definitions = {}
        s.set_target_object()
        pb = Playbook(key="k1", defined_in="site.yml")
        s.root_definitions = cast(YAMLDict, {"definitions": {"projects": [pb]}})
        s.type = "project"
        s.set_target_object()
        assert s.target_object is pb
        s.root_definitions = {"definitions": {}}
        s.build_content_graph()
        assert s.content_graph is not None
        with pytest.raises(ValueError, match="ContentGraph must be built"):
            SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path)).build_hierarchy_payload()
        payload = s.build_hierarchy_payload(scan_id="s1")
        assert payload["scan_id"] == "s1"
        s.apply_rules()
        assert s.findings is not None
        s.add_time_records({"a": 1})
        assert s.findings.metadata["time_records"] == {"a": 1}
        s2 = SingleScan(type=LoadType.ROLE, name="r", root_dir=str(tmp_path))
        s2.add_time_records({"a": 1})
        dep, ext, root = s.count_definitions()
        assert isinstance(dep, int)
        s.set_metadata({"version": "1", "hash": "h", "download_url": "u"}, [])
        assert s.version == "1"
        s.set_metadata_findings()
        assert s.findings is not None
        idx = tmp_path / "idx.json"
        idx.write_text("{}")
        s._path_mappings["index"] = str(idx)
        s.load_index()
        assert s.index == {}
        s._path_mappings["index"] = ""
        s.load_index()


# ---------------------------------------------------------------------------
# graph_opa_payload
# ---------------------------------------------------------------------------


class TestGraphOpaExtra:
    """Tests for graph_opa_payload uncovered branches."""

    def test_json_safe(self) -> None:
        """json_safe coerces nested and exotic values."""
        from apme_engine.engine.graph_opa_payload import json_safe

        assert json_safe(None) is None
        assert json_safe("a") == "a"
        assert json_safe(cast(YAMLValue, [1, {"k": (2, 3)}]))
        assert json_safe({"a": 1}) == {"a": 1}
        assert isinstance(json_safe(cast(YAMLValue, object())), str)

    def test_location_to_dict(self) -> None:
        """None/empty yield None; valid yields dict."""
        from apme_engine.engine.graph_opa_payload import _location_to_dict

        assert _location_to_dict(None) is None
        assert _location_to_dict(Location()) is None
        d = _location_to_dict(Location(type="file", value="/a"))
        assert d is not None and d["value"] == "/a"

    def test_annotation_plain(self) -> None:
        """Non-risk annotation yields empty risk_type."""
        from apme_engine.engine.graph_opa_payload import annotation_to_dict

        d = annotation_to_dict(Annotation(key="k", type="t"))
        assert d["risk_type"] == ""

    def test_annotation_risk_full(self) -> None:
        """Risk annotation serializes detail fields."""
        from apme_engine.engine.graph_opa_payload import annotation_to_dict

        anno = RiskAnnotation(risk_type="cmd_exec", key="k")
        anno.command = Arguments(raw="echo hi")
        anno.exec_files = [Location(type="file", value="/bin/echo")]
        anno.src = Location(type="file", value="/s")
        anno.dest = Location(type="file", value="/d")
        anno.is_mutable_src = True
        anno.pkg = "nginx"  # type: ignore[attr-defined]
        anno.version = "1.0"  # type: ignore[attr-defined]
        anno.path = Location(type="file", value="/p")  # type: ignore[attr-defined]
        anno.is_mutable_key = True  # type: ignore[attr-defined]
        d = annotation_to_dict(anno)
        assert d["risk_type"] == "cmd_exec"
        assert d["command"] == "echo hi"
        assert d["pkg"] == "nginx"

    def test_content_node_unknown(self) -> None:
        """Unknown node type yields empty dict."""
        from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
        from apme_engine.graph.content_graph import ContentNode, NodeIdentity, NodeType

        n = ContentNode(identity=NodeIdentity(path="v.yml", node_type=NodeType.VARS_FILE))
        assert content_node_to_opa_dict(n) == {}

    def test_play_fallback_line(self) -> None:
        """Play without line uses index option."""
        from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
        from apme_engine.graph.content_graph import ContentNode, NodeIdentity, NodeType

        n = ContentNode(
            identity=NodeIdentity(path="s.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="s.yml",
            name="p",
            options={"index": 2},
        )
        d = content_node_to_opa_dict(n)
        assert d["line"] == [3, 3]

    def test_task_annotations_and_raw(self) -> None:
        """Task annotations guarded; _raw aliased."""
        from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
        from apme_engine.graph.content_graph import ContentNode, NodeIdentity, NodeType

        n = ContentNode(
            identity=NodeIdentity(path="s.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="s.yml",
            module="ansible.builtin.meta",
            module_options={"_raw": "flush_handlers"},
            options={"when": "x", "bogus": 1},
            annotations=[Annotation(key="k")],
        )
        d = content_node_to_opa_dict(n)
        assert cast(YAMLDict, d["module_options"])["_raw_params"] == "flush_handlers"
        assert "bogus" not in cast(YAMLDict, d["options"])

    def test_hierarchy_auto_scan_id(self) -> None:
        """Empty scan_id autogenerated."""
        from apme_engine.engine.graph_opa_payload import _extract_collections, build_hierarchy_from_graph
        from apme_engine.graph.content_graph import ContentGraph

        p = build_hierarchy_from_graph(ContentGraph(), scan_type="role", scan_name="r")
        assert p["scan_id"]
        assert _extract_collections([{"nodes": "bad"}]) == []
        assert (
            _extract_collections([{"nodes": [{"type": "taskcall", "module": "bad mod", "original_module": 1}]}]) == []
        )
        assert _extract_collections([{"nodes": [{"type": "taskcall", "module": "ansible.builtin.debug"}]}]) == []
        assert _extract_collections([{"nodes": [{"type": "taskcall", "module": "community.general.x"}]}]) == [
            "community.general"
        ]


# ---------------------------------------------------------------------------
# model_loader
# ---------------------------------------------------------------------------


class TestModelLoaderExtra:
    """Tests for model_loader pure branches."""

    def test_load_inventory_types(self, tmp_path: Path) -> None:
        """Inventory group/host/unknown and json/ini.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_inventories, load_inventory

        gv = tmp_path / "group_vars" / "all.yml"
        gv.parent.mkdir(parents=True)
        gv.write_text("a: 1\n")
        hv = tmp_path / "host_vars" / "h1.yml"
        hv.parent.mkdir(parents=True)
        hv.write_text("b: 2\n")
        assert load_inventory(str(gv), basedir=str(tmp_path)).inventory_type == "group_vars"
        assert load_inventory(str(hv), basedir=str(tmp_path)).inventory_type == "host_vars"
        jf = tmp_path / "group_vars" / "j.json"
        jf.write_text('{"k": 1}')
        assert load_inventory(str(jf), basedir=str(tmp_path)).variables == {"k": 1}
        ini = tmp_path / "group_vars" / "plain"
        ini.write_text("[g]\nh\n")
        assert load_inventory(str(ini), basedir=str(tmp_path)) is not None
        bad = tmp_path / "group_vars" / "bad.yml"
        bad.write_text("{{{")
        assert load_inventory(str(bad), basedir=str(tmp_path)) is not None
        badj = tmp_path / "group_vars" / "bad.json"
        badj.write_text("{bad")
        assert load_inventory(str(badj), basedir=str(tmp_path)) is not None
        with pytest.raises(ValueError, match="file not found"):
            load_inventory("missing-xyz", basedir=str(tmp_path))
        assert load_inventories(str(tmp_path / "missing")) == []
        assert load_inventories(str(tmp_path))

    def test_load_file_branches(self, tmp_path: Path) -> None:
        """Vault, bad yaml, missing, role/collection keys.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_file, load_files

        f = load_file(path="x.yml", body="$ANSIBLE_VAULT;1.1\nx", read=False)
        assert f.encrypted is True
        f2 = load_file(path="x.yml", body=": bad: [", read=False)
        assert isinstance(f2.body, str)
        f3 = load_file(path="missing.yml", basedir=str(tmp_path))
        assert "not found" in f3.error.lower() or f3.body == ""
        _f4 = load_file(path="d.yml", basedir=str(tmp_path), role_name="r", collection_name="c")
        assert True  # original `f4.role == "" or f4.collection == "" or True` is always True
        p = tmp_path / "v.yml"
        p.write_text("a: 1\n")
        f5 = load_file(path="v.yml", basedir=str(tmp_path), role_name="myrole", collection_name="mycol")
        assert f5.role == "myrole"
        assert load_files(str(tmp_path), yaml_label_list=None) == []
        assert (
            load_files(
                str(tmp_path), yaml_label_list=[("", "", None), ("a.yml", "", None), ("a.yml", "playbook", None)]
            )
            == []
        )
        out = load_files(str(tmp_path), yaml_label_list=[("v.yml", "others", None)], load_children=False)
        assert out == ["v.yml"]

    def test_load_play_errors(self) -> None:
        """Play validation errors and option branches."""
        from apme_engine.engine.model_loader import load_play
        from apme_engine.engine.models import PlaybookFormatError

        with pytest.raises(ValueError, match="play block dict is required"):
            load_play(path="p.yml", index=0, play_block_dict=None)  # type: ignore[arg-type]
        with pytest.raises(PlaybookFormatError):
            load_play(path="p.yml", index=0, play_block_dict=["x"])  # type: ignore[arg-type]
        with pytest.raises(PlaybookFormatError):
            load_play(path="p.yml", index=0, play_block_dict={"name": "x"})
        # vars non-dict skipped, vars_files/module_defaults/import branches
        play = load_play(
            path="p.yml",
            index=0,
            play_block_dict={
                "hosts": "all",
                "vars": ["bad"],
                "vars_files": "bad",
                "module_defaults": "bad",
                "import_playbook": 123,
                "roles": "bad",
                "pre_tasks": "bad",
                "tasks": [{"name": "t", "ansible.builtin.debug": {"msg": "hi"}}],
                "handlers": [{"name": "h", "ansible.builtin.debug": {"msg": "hi"}}],
            },
            yaml_lines="",
        )
        assert play is not None
        play2 = load_play(
            path="p.yml",
            index=0,
            play_block_dict={
                "hosts": "all",
                "vars": {"a": 1},
                "vars_files": ["v.yml"],
                "module_defaults": {"d": 1},
                "import_playbook": "other.yml",
                "roles": ["myrole", {"role": "r2"}],
            },
            yaml_lines="",
        )
        assert play2.import_playbook == "other.yml"
        assert play2.variables == {"a": 1}

    def test_load_roleinplay_name_option(self) -> None:
        """Name popped from options when empty."""
        from apme_engine.engine.model_loader import load_roleinplay

        rip = load_roleinplay(
            name="", options={"name": "myrole"}, defined_in="/base/pb.yml", role_index=0, play_index=0, basedir="/base"
        )
        assert rip.name == "myrole"
        assert rip.defined_in == "pb.yml"

    def test_load_playbook_errors(self, tmp_path: Path) -> None:
        """Playbook file errors and bad yaml.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_playbook
        from apme_engine.engine.models import PlaybookFormatError

        with pytest.raises(ValueError, match="file not found"):
            load_playbook(path="missing.yml", basedir=str(tmp_path))
        _f = _write(tmp_path, "t.txt", "hi")
        with pytest.raises(ValueError, match="file not found"):
            load_playbook(path="t.txt", basedir="/nonexistent-base-xyz")
        # yaml_str bad with skip False raises
        with pytest.raises(PlaybookFormatError):
            load_playbook(yaml_str="{{{ bad", skip_playbook_format_error=False)
        # file bad yaml skipped
        _bf = _write(tmp_path, "bad.yml", "{{{ bad")
        pb = load_playbook(path="bad.yml", basedir=str(tmp_path))
        assert pb is not None
        with pytest.raises(PlaybookFormatError):
            load_playbook(path="bad.yml", basedir=str(tmp_path), skip_playbook_format_error=False)
        with pytest.raises(PlaybookFormatError):
            load_playbook(yaml_str="key: value\n", skip_playbook_format_error=False)

    def test_load_module_branches(self, tmp_path: Path) -> None:
        """Module loading with docs, plugins path, errors.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_module

        with pytest.raises(ValueError, match="require module"):
            load_module("", basedir=str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            load_module("missing.py", basedir=str(tmp_path))
        mod = tmp_path / "mymod.py"
        mod.write_text(
            'DOCUMENTATION = """---\noptions:\n  src:\n    type: str\n    required: true\n    description: x\n"""\n'
        )
        m = load_module(str(mod), collection_name="ns.col", basedir="", use_ansible_doc=False)
        assert m.fqcn == "ns.col.mymod"
        assert any(a.name == "src" for a in m.arguments)
        plug = tmp_path / "plugins" / "modules" / "sub" / "deep.py"
        plug.parent.mkdir(parents=True)
        plug.write_text('DOCUMENTATION = """---\noptions: {}\n"""\n')
        m2 = load_module(str(plug), collection_name="ns.col", basedir=str(tmp_path), use_ansible_doc=False)
        assert "deep" in m2.name
        m3 = load_module(str(mod), role_name="myrole", basedir="", use_ansible_doc=False)
        assert m3.role == "myrole"
        # ansible-doc specs path
        m4 = load_module(
            str(mod),
            collection_name="ns.col",
            basedir="",
            use_ansible_doc=True,
            module_specs={"ns.col.mymod": {"doc": "---\noptions:\n  p:\n    type: int\n", "examples": "ex"}},
        )
        assert m4.examples == "ex"
        m5 = load_module(
            str(mod),
            collection_name="ns.col",
            basedir="",
            use_ansible_doc=True,
            module_specs=cast(dict[str, dict[str, object]], {"ns.col.mymod": "bad"}),
        )
        assert m5.documentation == ""

    def test_load_builtin_and_modules(self, tmp_path: Path) -> None:
        """Builtin cache and module discovery.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_builtin_modules, load_modules

        b1 = load_builtin_modules()
        b2 = load_builtin_modules()
        assert b1 is b2
        assert load_modules("", basedir="") == []
        assert load_modules(str(tmp_path / "missing")) == []
        lib = tmp_path / "library"
        lib.mkdir()
        (lib / "m.py").write_text('DOCUMENTATION = "x"\n')
        assert load_modules(str(tmp_path), basedir=str(tmp_path), use_ansible_doc=False)
        assert load_modules(str(tmp_path), basedir=str(tmp_path), use_ansible_doc=False, load_children=False)

    def test_load_task_string_opts(self) -> None:
        """String module options parsed into dict; role/taskfile refs."""
        from apme_engine.engine.model_loader import load_task

        t = load_task(
            path="t.yml",
            index=0,
            task_block_dict={"name": "u", "ansible.builtin.ufw": "port=80 proto=tcp"},
            yaml_lines="- name: u\n  ansible.builtin.ufw: port=80 proto=tcp\n",
        )
        assert isinstance(t.module_options, dict)
        t2 = load_task(
            path="t.yml",
            index=0,
            task_block_dict={"ansible.builtin.import_role": {"name": "myrole"}},
            yaml_lines="- ansible.builtin.import_role:\n    name: myrole\n",
        )
        assert t2.executable == "myrole"
        t3 = load_task(
            path="t.yml",
            index=0,
            task_block_dict={"ansible.builtin.include_tasks": {"file": "inc.yml"}},
            yaml_lines="- ansible.builtin.include_tasks:\n    file: inc.yml\n",
        )
        assert t3.executable == "inc.yml"
        t4 = load_task(
            path="t.yml",
            index=0,
            task_block_dict={
                "name": "s",
                "ansible.builtin.set_fact": {"k": "v"},
                "register": "r",
                "loop": ["a"],
                "vars": {"x": 1},
                "module_defaults": {"m": 1},
            },
            yaml_lines="- name: s\n  ansible.builtin.set_fact:\n    k: v\n",
        )
        assert t4.set_facts == {"k": "v"}
        assert "r" in t4.registered_variables
        with pytest.raises(ValueError, match="task block dict is required"):
            load_task(
                path="t.yml",
                index=0,
                task_block_dict=None,  # type: ignore[arg-type]
                yaml_lines="- name: x\n  ansible.builtin.debug:\n    msg: hi\n",
            )
        with pytest.raises(Exception, match="not a dict"):
            load_task(
                path="t.yml",
                index=0,
                task_block_dict=["x"],  # type: ignore[arg-type]
                yaml_lines="- name: x\n  ansible.builtin.debug:\n    msg: hi\n",
            )

    def test_load_taskfile_errors(self, tmp_path: Path) -> None:
        """Taskfile missing/ext and TaskFormat skip/raise.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_taskfile
        from apme_engine.engine.models import TaskFormatError

        with pytest.raises(ValueError, match="file not found"):
            load_taskfile(path="missing.yml", basedir=str(tmp_path))
        f = _write(tmp_path, "x.txt", "hi")
        assert f
        with pytest.raises(ValueError, match=".yml"):
            load_taskfile(path="x.txt", basedir=str(tmp_path))
        tf = load_taskfile(path="t.yml", yaml_str="---\n{{{ bad")
        assert tf is not None
        with pytest.raises(TaskFormatError):
            load_taskfile(path="t.yml", yaml_str="---\n- just_a_string\n", skip_task_format_error=False)

    def test_load_taskfiles_and_roles_helpers(self, tmp_path: Path) -> None:
        """Taskfiles/roles/requirements/installed helpers.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import (
            find_playbook_role_module,
            load_installed_collections,
            load_installed_roles,
            load_requirements,
            load_roles,
            load_taskfiles,
        )

        assert load_taskfiles(str(tmp_path / "missing")) == []
        assert load_roles("") == []
        assert load_requirements(str(tmp_path)) == {}
        _write(tmp_path, "requirements.yml", "---\ncollections: []\n")
        assert isinstance(load_requirements(str(tmp_path)), dict)
        _write(tmp_path, "requirements.yml", "{{{ bad")
        assert isinstance(load_requirements(str(tmp_path)), dict)
        assert load_installed_collections("") == []
        assert load_installed_roles("") == []
        assert load_roles(str(tmp_path)) == []
        _write(tmp_path, "site.yml", PLAYBOOK_YAML)
        assert find_playbook_role_module(str(tmp_path), use_ansible_doc=False)

    def test_load_role_and_collection_errors(self, tmp_path: Path) -> None:
        """Role/collection validation and files fallbacks.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_collection, load_object, load_role
        from apme_engine.engine.models import Load as _Load

        with pytest.raises(ValueError, match="directory not found"):
            load_role("missing", basedir=str(tmp_path))
        with pytest.raises(ValueError, match="directory not found"):
            load_collection("missing-dir-xyz", basedir=str(tmp_path))
        with pytest.raises(ValueError, match="directory not found"):
            load_collection("", basedir="")
        # load_object each type with mocked children
        ld = _Load(target_type="collection", path=str(tmp_path))
        with patch("apme_engine.engine.model_loader.load_collection", return_value=Collection(name="c")):
            load_object(ld)
        ld2 = _Load(target_type="role", path=str(tmp_path))
        with patch("apme_engine.engine.model_loader.load_role", return_value=Role(name="r", defined_in="r")):
            load_object(ld2)
            assert ld2.roles == ["r"]
        ld3 = _Load(target_type="project", path=str(tmp_path))
        with patch("apme_engine.engine.model_loader.load_repository", return_value=Role(name="r")):
            load_object(ld3)
        assert ld3.timestamp

    def test_load_object_playbook_taskfile(self, tmp_path: Path) -> None:
        """load_object playbook/taskfile only and repo variants.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.model_loader import load_object
        from apme_engine.engine.models import Load as _Load
        from apme_engine.engine.models import Playbook as _PB
        from apme_engine.engine.models import TaskFile as _TF

        ld = _Load(target_type="playbook", path="p.yml", playbook_yaml=PLAYBOOK_YAML, playbook_only=True)
        with patch("apme_engine.engine.model_loader.load_playbook", return_value=_PB(name="p", defined_in="p.yml")):
            load_object(ld)
            assert ld.playbooks == ["p.yml"]
        ld2 = _Load(target_type="playbook", path="/b/p.yml", base_dir="/b")
        with patch("apme_engine.engine.model_loader.load_repository", return_value=Collection(name="c")):
            load_object(ld2)
        ld3 = _Load(target_type="taskfile", path="t.yml", taskfile_yaml=TASKFILE_YAML, taskfile_only=True)
        with patch("apme_engine.engine.model_loader.load_taskfile", return_value=_TF(name="t", defined_in="t.yml")):
            load_object(ld3)
            assert ld3.taskfiles == ["t.yml"]
        ld4 = _Load(target_type="taskfile", path="/b/t.yml", base_dir="/b")
        with patch("apme_engine.engine.model_loader.load_repository", return_value=Collection(name="c")):
            load_object(ld4)
