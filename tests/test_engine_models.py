"""Tests for apme_engine.engine.models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from apme_engine.engine.models import (
    ActionGroupMetadata,
    Annotation,
    AnnotationCondition,
    Arguments,
    ArgumentsType,
    AttributeCondition,
    BecomeInfo,
    CallObject,
    Collection,
    CommandExecDetail,
    DefaultRiskType,
    File,
    FileChangeDetail,
    FunctionCondition,
    KeyConfigChangeDetail,
    Load,
    LoadType,
    Location,
    LocationType,
    Module,
    ModuleArgument,
    NetworkTransferDetail,
    Object,
    ObjectList,
    PackageInstallDetail,
    PlaybookFormatError,
    RiskAnnotation,
    RiskAnnotationList,
    RoleMetadata,
    Rule,
    RuleMetadata,
    RuleResult,
    RuleScope,
    RunTarget,
    RunTargetList,
    Severity,
    SpecMutation,
    Task,
    TaskCallsInTree,
    TaskFileMetadata,
    TaskFormatError,
    Variable,
    VariableAnnotation,
    VariableDict,
    VariablePrecedence,
    VariableType,
    YAMLDict,
    YAMLValue,
    _convert_to_bool,
)


class TestExceptions:
    """Tests for PlaybookFormatError and TaskFormatError exceptions."""

    def test_playbook_format_error(self) -> None:
        """Verifies PlaybookFormatError can be raised and caught.

        Raises:
            PlaybookFormatError: When playbook format is invalid.

        """
        with pytest.raises(PlaybookFormatError):
            raise PlaybookFormatError("bad playbook")

    def test_task_format_error(self) -> None:
        """Verifies TaskFormatError can be raised and caught.

        Raises:
            TaskFormatError: When task format is invalid.

        """
        with pytest.raises(TaskFormatError):
            raise TaskFormatError("bad task")


class TestJSONSerializable:
    """Tests for JSON serialization of model objects."""

    def test_dump_returns_json(self) -> None:
        """Verifies dump returns a JSON string containing object fields."""
        obj = Load(target_name="test", target_type="collection")
        result = obj.dump()
        assert "test" in result
        assert "collection" in result

    def test_to_json_from_json_round_trip(self) -> None:
        """Verifies to_json and from_json round-trip preserves object data."""
        obj = Load(target_name="myproject", target_type="project", path="/some/path")
        json_str = obj.to_json()
        restored = cast(Load, Load.from_json(json_str))
        assert restored.target_name == "myproject"
        assert restored.path == "/some/path"


class TestLoadType:
    """Tests for LoadType enum constants."""

    def test_constants(self) -> None:
        """Verifies LoadType string values for project, collection, role, etc."""
        assert LoadType.PROJECT == "project"
        assert LoadType.COLLECTION == "collection"
        assert LoadType.ROLE == "role"
        assert LoadType.PLAYBOOK == "playbook"
        assert LoadType.TASKFILE == "taskfile"
        assert LoadType.UNKNOWN == "unknown"


class TestLoad:
    """Tests for Load model defaults and field assignment."""

    def test_defaults(self) -> None:
        """Verifies Load default values for target_name, target_type, playbook_only, roles."""
        ld = Load()
        assert ld.target_name == ""
        assert ld.target_type == ""
        assert ld.playbook_only is False
        assert ld.roles == []

    def test_with_fields(self) -> None:
        """Verifies Load stores target_name, target_type, and path correctly."""
        ld = Load(target_name="ns.col", target_type="collection", path="/path")
        assert ld.target_name == "ns.col"
        assert ld.path == "/path"


class TestObject:
    """Tests for Object model defaults and field assignment."""

    def test_defaults(self) -> None:
        """Verifies Object default values for type and key."""
        obj = Object()
        assert obj.type == ""
        assert obj.key == ""

    def test_with_values(self) -> None:
        """Verifies Object stores type and key correctly."""
        obj = Object(type="module", key="module ns.col.mymod")
        assert obj.type == "module"


class TestObjectList:
    """Tests for ObjectList add, find, merge, and serialization."""

    def test_add_and_find(self) -> None:
        """Verifies add stores objects and find_by_key retrieves or returns None."""
        ol = ObjectList()
        obj = Object(type="module", key="mod1")
        ol.add(obj)
        assert ol.find_by_key("mod1") is obj
        assert ol.find_by_key("nonexistent") is None

    def test_contains(self) -> None:
        """Verifies contains checks membership by key or object."""
        ol = ObjectList()
        obj = Object(type="role", key="role1")
        ol.add(obj)
        assert ol.contains(key="role1") is True
        assert ol.contains(key="role2") is False
        assert ol.contains(obj=obj) is True

    def test_find_by_type(self) -> None:
        """Verifies find_by_type returns all objects matching the type."""
        ol = ObjectList()
        ol.add(Object(type="module", key="m1"))
        ol.add(Object(type="role", key="r1"))
        ol.add(Object(type="module", key="m2"))
        modules = ol.find_by_type("module")
        assert len(modules) == 2

    def test_find_by_attr(self) -> None:
        """Verifies find_by_attr filters by attribute value."""
        ol = ObjectList()
        ol.add(Object(type="module", key="m1"))
        ol.add(Object(type="role", key="r1"))
        found = ol.find_by_attr("type", "role")
        assert len(found) == 1

    def test_merge(self) -> None:
        """Verifies merge combines items from another ObjectList."""
        ol1 = ObjectList()
        ol1.add(Object(type="a", key="k1"))
        ol2 = ObjectList()
        ol2.add(Object(type="b", key="k2"))
        ol1.merge(ol2)
        assert len(ol1.items) == 2
        assert ol1.find_by_key("k2") is not None

    def test_merge_non_objectlist_raises(self) -> None:
        """Verifies merge raises ValueError when given non-ObjectList."""
        ol = ObjectList()
        with pytest.raises(ValueError, match="ObjectList"):
            ol.merge("bad")  # type: ignore[arg-type]

    def test_resolver_targets(self) -> None:
        """Verifies resolver_targets returns items list."""
        ol = ObjectList()
        ol.add(Object(key="k1"))
        assert ol.resolver_targets == ol.items

    def test_to_json_and_from_json(self) -> None:
        """Verifies ObjectList JSON round-trip preserves items."""
        ol = ObjectList()
        ol.add(Object(type="test", key="key1"))
        json_str = ol.to_json()
        restored = ObjectList.from_json(json_str)
        assert len(restored.items) == 1

    def test_to_one_line_json(self) -> None:
        """Verifies to_one_line_json produces compact JSON with object keys."""
        ol = ObjectList()
        ol.add(Object(type="test", key="key1"))
        result = ol.to_one_line_json()
        assert "key1" in result


class TestCallObject:
    """Tests for CallObject defaults and from_spec factory."""

    def test_defaults(self) -> None:
        """Verifies CallObject default depth and node_id."""
        co = CallObject()
        assert co.depth == -1
        assert co.node_id == ""

    def test_from_spec_without_caller(self) -> None:
        """Verifies from_spec creates CallObject with depth 0 and node_id when no caller."""
        spec = Object(type="module", key="module mod1")
        co = CallObject.from_spec(spec, caller=None, index=0)
        assert co.spec is spec
        assert co.depth == 0
        assert co.node_id == "0"

    def test_from_spec_with_caller(self) -> None:
        """Verifies from_spec builds node_id and depth from caller hierarchy."""
        spec = Object(type="module", key="module mod1")
        caller = CallObject(key="parent_key", depth=1, node_id="0.1")
        co = CallObject.from_spec(spec, caller=caller, index=2)
        assert co.depth == 2
        assert co.node_id == "0.1.2"
        assert co.called_from == "parent_key"


class TestRunTarget:
    """Tests for RunTarget file_info and annotation lookup."""

    def test_file_info(self) -> None:
        """Verifies file_info returns defined_in file and lines from spec."""
        spec = Object(type="task")
        spec.defined_in = "tasks/main.yml"  # type: ignore[attr-defined]
        rt = RunTarget(spec=spec)
        file, lines = rt.file_info()
        assert file == "tasks/main.yml"
        assert lines is None

    def test_has_annotation_returns_false(self) -> None:
        """Verifies has_annotation_by_condition returns False when no annotations."""
        rt = RunTarget()
        cond = AnnotationCondition()
        assert rt.has_annotation_by_condition(cond) is False

    def test_get_annotation_returns_none(self) -> None:
        """Verifies get_annotation_by_condition returns None when no match."""
        rt = RunTarget()
        cond = AnnotationCondition()
        assert rt.get_annotation_by_condition(cond) is None


class TestRunTargetList:
    """Tests for RunTargetList length, indexing, and iteration."""

    def test_len_and_getitem(self) -> None:
        """Verifies len and indexing work on RunTargetList items."""
        items = [RunTarget(key="rt1"), RunTarget(key="rt2")]
        rtl = RunTargetList(items=items)
        assert len(rtl) == 2
        assert rtl[0].key == "rt1"

    def test_iteration(self) -> None:
        """Verifies RunTargetList is iterable over items."""
        items = [RunTarget(key="a"), RunTarget(key="b")]
        rtl = RunTargetList(items=items)
        keys = [rt.key for rt in rtl]
        assert keys == ["a", "b"]


class TestFile:
    """Tests for File model defaults and resolver_targets."""

    def test_defaults(self) -> None:
        """Verifies File default type and name."""
        f = File()
        assert f.type == "file"
        assert f.name == ""

    def test_resolver_targets(self) -> None:
        """Verifies File resolver_targets is None."""
        f = File()
        assert f.resolver_targets is None

    def test_children_to_key(self) -> None:
        """Verifies children_to_key returns self for File."""
        f = File(name="test.yml")
        assert f.children_to_key() is f


class TestModuleArgument:
    """Tests for ModuleArgument available_keys with and without aliases."""

    def test_available_keys_no_aliases(self) -> None:
        """Verifies available_keys returns only name when no aliases."""
        arg = ModuleArgument(name="src")
        assert arg.available_keys() == ["src"]

    def test_available_keys_with_aliases(self) -> None:
        """Verifies available_keys includes name and all aliases."""
        arg = ModuleArgument(name="src", aliases=["source", "origin"])
        assert arg.available_keys() == ["src", "source", "origin"]


class TestModule:
    """Tests for Module model defaults and resolver_targets."""

    def test_defaults(self) -> None:
        """Verifies Module default type and builtin flag."""
        m = Module()
        assert m.type == "module"
        assert m.builtin is False

    def test_resolver_targets(self) -> None:
        """Verifies Module resolver_targets is None."""
        m = Module()
        assert m.resolver_targets is None


class TestVariablePrecedence:
    """Tests for VariablePrecedence str, repr, ordering, and equality."""

    def test_str_repr(self) -> None:
        """Verifies str and repr return the name."""
        vp = VariablePrecedence(name="task_vars", order=17)
        assert str(vp) == "task_vars"
        assert repr(vp) == "task_vars"

    def test_ordering(self) -> None:
        """Verifies VariablePrecedence ordering by order value."""
        low = VariablePrecedence(name="low", order=2)
        high = VariablePrecedence(name="high", order=17)
        assert low < high
        assert low <= high
        assert high > low
        assert high >= low
        assert low != high

    def test_equality(self) -> None:
        """Verifies equality is based on order, not name."""
        a = VariablePrecedence(name="a", order=5)
        b = VariablePrecedence(name="b", order=5)
        assert a == b

    def test_not_equal_to_non_vp(self) -> None:
        """Verifies comparison with non-VariablePrecedence returns NotImplemented."""
        vp = VariablePrecedence(name="x", order=1)
        assert vp.__eq__("other") is NotImplemented
        assert vp.__lt__("other") is NotImplemented
        assert vp.__le__("other") is NotImplemented


class TestVariableType:
    """Tests for VariableType ordering and Unknown order."""

    def test_ordering(self) -> None:
        """Verifies VariableType ordering between RoleDefaults, ExtraVars, LoopVars."""
        assert VariableType.RoleDefaults < VariableType.ExtraVars
        assert VariableType.LoopVars > VariableType.ExtraVars

    def test_unknown_has_negative_order(self) -> None:
        """Verifies VariableType.Unknown has negative order."""
        assert VariableType.Unknown.order < 0


class TestVariable:
    """Tests for Variable is_mutable based on type."""

    def test_is_mutable_default(self) -> None:
        """Verifies Variable is_mutable defaults to True."""
        v = Variable(name="foo")
        assert v.is_mutable is True

    def test_is_mutable_with_loop_vars(self) -> None:
        """Verifies LoopVars variables are not mutable."""
        v = Variable(name="item", type=VariableType.LoopVars)
        assert v.is_mutable is False

    def test_is_mutable_with_task_vars(self) -> None:
        """Verifies TaskVars variables are mutable."""
        v = Variable(name="x", type=VariableType.TaskVars)
        assert v.is_mutable is True


class TestArguments:
    """Tests for Arguments type, raw, and get with various key/raw combinations."""

    def test_defaults(self) -> None:
        """Verifies Arguments default type and raw."""
        args = Arguments()
        assert args.type == ArgumentsType.SIMPLE
        assert args.raw is None

    def test_get_empty_key(self) -> None:
        """Verifies get with empty key returns raw value for simple args."""
        args = Arguments(raw="some value")
        result = args.get("")
        assert result is not None
        assert result.raw == "some value"

    def test_get_returns_none_for_empty_raw(self) -> None:
        """Verifies get returns None when raw is None."""
        args = Arguments(raw=None)
        assert args.get() is None

    def test_get_dict_key(self) -> None:
        """Verifies get retrieves value by key from dict raw."""
        args = Arguments(raw={"src": "/tmp/file", "dest": "/opt/file"})
        result = args.get("src")
        assert result is not None
        assert result.raw == "/tmp/file"

    def test_get_missing_dict_key(self) -> None:
        """Verifies get returns None for missing dict key."""
        args = Arguments(raw={"src": "/tmp/file"})
        result = args.get("missing")
        assert result is None

    def test_get_list_type(self) -> None:
        """Verifies get with list raw returns ArgumentsType.LIST."""
        args = Arguments(raw=["a", "b", "c"])
        result = args.get("")
        assert result is not None
        assert result.type == ArgumentsType.LIST

    def test_get_with_variables(self) -> None:
        """Verifies get propagates vars and is_mutable from Variable."""
        var = Variable(name="my_var", type=VariableType.TaskVars)
        args = Arguments(raw="{{ my_var }}/path", vars=[var])
        result = args.get("")
        assert result is not None
        assert len(result.vars) == 1
        assert result.is_mutable is True


class TestLocation:
    """Tests for Location is_empty, is_mutable, contains, and is_inside."""

    def test_defaults(self) -> None:
        """Verifies Location is_empty when no values set."""
        loc = Location()
        assert loc.is_empty is True

    def test_with_values(self) -> None:
        """Verifies Location with type and value is not empty and not mutable."""
        loc = Location(type=LocationType.FILE, value="/tmp/file")
        assert loc.is_empty is False
        assert loc.is_mutable is False

    def test_is_mutable_with_vars(self) -> None:
        """Verifies Location with vars is mutable."""
        loc = Location(type=LocationType.FILE, value="{{ path }}", vars=[Variable(name="path")])
        assert loc.is_mutable is True

    def test_post_init_from_args(self) -> None:
        """Verifies Location value is derived from _args when provided."""
        args = Arguments(raw="/tmp/file", vars=[])
        loc = Location(_args=args)
        assert loc.value == "/tmp/file"

    def test_contains(self) -> None:
        """Verifies contains returns True when child path is under parent."""
        parent = Location(value="/opt")
        child = Location(value="/opt/myapp/data")
        assert parent.contains(child) is True
        assert child.contains(parent) is False

    def test_is_inside(self) -> None:
        """Verifies child is_inside parent path."""
        parent = Location(value="/opt")
        child = Location(value="/opt/myapp")
        assert child.is_inside(parent) is True

    def test_contains_any(self) -> None:
        """Verifies contains_any returns True when any target is inside."""
        parent = Location(value="/opt")
        targets = [Location(value="/opt/a"), Location(value="/tmp/b")]
        assert parent.contains_any(targets) is True

    def test_contains_all(self) -> None:
        """Verifies contains_all returns True only when all targets inside."""
        parent = Location(value="/opt")
        targets = [Location(value="/opt/a"), Location(value="/opt/b")]
        assert parent.contains_all(targets) is True
        targets2 = [Location(value="/opt/a"), Location(value="/tmp/b")]
        assert parent.contains_all(targets2) is False

    def test_contains_list_any_mode(self) -> None:
        """Verifies contains with any_mode=True matches if any target inside."""
        parent = Location(value="/opt")
        targets = [Location(value="/opt/a"), Location(value="/tmp/b")]
        assert parent.contains(targets, any_mode=True, all_mode=False) is True

    def test_contains_list_all_mode(self) -> None:
        """Verifies contains with all_mode=True matches only when all inside."""
        parent = Location(value="/opt")
        targets = [Location(value="/opt/a"), Location(value="/opt/b")]
        assert parent.contains(targets, any_mode=False, all_mode=True) is True

    def test_contains_bad_mode_raises(self) -> None:
        """Verifies contains raises ValueError when both any_mode and all_mode False."""
        parent = Location(value="/opt")
        with pytest.raises(ValueError, match="any.*all"):
            parent.contains([Location(value="/opt/a")], any_mode=False, all_mode=False)

    def test_contains_non_location_raises(self) -> None:
        """Verifies contains raises ValueError for non-Location argument."""
        loc = Location(value="/opt")
        with pytest.raises(ValueError):
            loc.contains("not-a-location")  # type: ignore[arg-type]


class TestNetworkTransferDetail:
    """Tests for NetworkTransferDetail src, dest, and is_mutable_src from _args."""

    def test_post_init_with_args(self) -> None:
        """Verifies Location and is_mutable_src derived from _src_arg and _dest_arg."""
        src_args = Arguments(raw="/tmp/file", is_mutable=True)
        dest_args = Arguments(raw="/opt/dest")
        detail = NetworkTransferDetail(_src_arg=src_args, _dest_arg=dest_args)
        assert detail.src is not None
        assert detail.src.value == "/tmp/file"
        assert detail.is_mutable_src is True
        assert detail.dest is not None
        assert detail.dest.value == "/opt/dest"


class TestFileChangeDetail:
    """Tests for FileChangeDetail is_insecure_permissions, is_deletion, is_unsafe_write."""

    def test_insecure_permissions(self) -> None:
        """Verifies is_insecure_permissions True for mode 0777."""
        detail = FileChangeDetail(_mode_arg=Arguments(raw="0777"))
        assert detail.is_insecure_permissions is True

    def test_deletion(self) -> None:
        """Verifies is_deletion True when state is absent."""
        detail = FileChangeDetail(_state_arg=Arguments(raw="absent"))
        assert detail.is_deletion is True

    def test_unsafe_write(self) -> None:
        """Verifies is_unsafe_write True when unsafe_write_arg is True."""
        detail = FileChangeDetail(_unsafe_write_arg=Arguments(raw=True))
        assert detail.is_unsafe_write is True


class TestCommandExecDetail:
    """Tests for CommandExecDetail exec_files from command argument."""

    def test_basic_command(self) -> None:
        """Verifies exec_files extracts first token from command string."""
        detail = CommandExecDetail(command=Arguments(raw="echo hello"))
        assert len(detail.exec_files) == 1
        assert detail.exec_files[0].value == "echo"

    def test_no_command(self) -> None:
        """Verifies exec_files is empty when command is None."""
        detail = CommandExecDetail(command=None)
        assert detail.exec_files == []

    def test_non_exec_program(self) -> None:
        """Verifies exec_files empty for non-exec programs like tar."""
        detail = CommandExecDetail(command=Arguments(raw="tar xzf archive.tar.gz"))
        assert detail.exec_files == []


class TestConvertToBool:
    """Tests for _convert_to_bool with various input types."""

    def test_true_values(self) -> None:
        """Verifies True, 'true', 'True', 'yes' convert to True."""
        assert _convert_to_bool(True) is True
        assert _convert_to_bool("true") is True
        assert _convert_to_bool("True") is True
        assert _convert_to_bool("yes") is True

    def test_false_values(self) -> None:
        """Verifies False and 'false' convert to False."""
        assert _convert_to_bool(False) is False
        assert _convert_to_bool("false") is False

    def test_none_for_non_bool(self) -> None:
        """Verifies non-bool values return None."""
        assert _convert_to_bool(42) is None
        assert _convert_to_bool(None) is None


class TestAnnotation:
    """Tests for Annotation defaults and field assignment."""

    def test_defaults(self) -> None:
        """Verifies Annotation default key and value."""
        anno = Annotation()
        assert anno.key == ""
        assert anno.value is None

    def test_with_values(self) -> None:
        """Verifies Annotation stores key, value, and rule_id."""
        anno = Annotation(key="test_key", value="test_value", rule_id="rule1")
        assert anno.rule_id == "rule1"


class TestVariableAnnotation:
    """Tests for VariableAnnotation type attribute."""

    def test_type(self) -> None:
        """Verifies VariableAnnotation type is variable_annotation."""
        va = VariableAnnotation()
        assert va.type == "variable_annotation"


class TestRiskAnnotation:
    """Tests for RiskAnnotation init factory and equal_to."""

    def test_init_factory(self) -> None:
        """Verifies RiskAnnotation.init creates annotation with risk_type and detail."""
        detail = FileChangeDetail(
            _path_arg=Arguments(raw="/tmp/test"),
            _state_arg=Arguments(raw="present"),
        )
        anno = RiskAnnotation.init(DefaultRiskType.FILE_CHANGE, detail)
        assert anno.risk_type == DefaultRiskType.FILE_CHANGE
        path = getattr(anno, "path", None)
        assert path is not None
        assert path.value == "/tmp/test"

    def test_equal_to(self) -> None:
        """Verifies equal_to returns True for same risk_type."""
        a = RiskAnnotation(risk_type="cmd_exec")
        b = RiskAnnotation(risk_type="cmd_exec")
        assert a.equal_to(b) is True

    def test_not_equal_different_risk_type(self) -> None:
        """Verifies equal_to returns False for different risk_type."""
        a = RiskAnnotation(risk_type="cmd_exec")
        b = RiskAnnotation(risk_type="file_change")
        assert a.equal_to(b) is False


class TestAnnotationCondition:
    """Tests for AnnotationCondition fluent API."""

    def test_fluent_api(self) -> None:
        """Verifies risk_type and attr chain and return self."""
        cond = AnnotationCondition()
        result = cond.risk_type("cmd_exec").attr("key1", "val1")
        assert result is cond
        assert cond.type == "cmd_exec"
        assert cond.attr_conditions == [("key1", "val1")]


class TestAttributeCondition:
    """Tests for AttributeCondition check against annotation attributes."""

    def test_check_match(self) -> None:
        """Verifies check returns True when attr matches result."""
        anno = RiskAnnotation(risk_type="cmd_exec")
        anno.is_deletion = True  # type: ignore[attr-defined]
        cond = AttributeCondition(attr="is_deletion", result=True)
        assert cond.check(anno) is True

    def test_check_no_match(self) -> None:
        """Verifies check returns False for nonexistent attr."""
        anno = RiskAnnotation(risk_type="cmd_exec")
        cond = AttributeCondition(attr="nonexistent", result=True)
        assert cond.check(anno) is False


class TestFunctionCondition:
    """Tests for FunctionCondition with custom checker function."""

    def test_check_with_func(self) -> None:
        """Verifies check invokes func and returns result when func matches."""

        def my_checker(anno: RiskAnnotation, **kwargs: YAMLValue) -> bool:
            return anno.risk_type == "cmd_exec"

        cond = FunctionCondition(func=my_checker, result=True)
        anno = RiskAnnotation(risk_type="cmd_exec")
        assert cond.check(anno) is True

    def test_check_no_func(self) -> None:
        """Verifies check returns False when no func set."""
        cond = FunctionCondition()
        anno = RiskAnnotation()
        assert cond.check(anno) is False


class TestRiskAnnotationList:
    """Tests for RiskAnnotationList iteration, filter, after, and find."""

    def _make_list(self) -> RiskAnnotationList:
        """Builds a RiskAnnotationList with cmd_exec and file_change items.

        Returns:
            RiskAnnotationList with three annotations.
        """
        return RiskAnnotationList(
            items=[
                RiskAnnotation(risk_type="cmd_exec", key="a"),
                RiskAnnotation(risk_type="file_change", key="b"),
                RiskAnnotation(risk_type="cmd_exec", key="c"),
            ]
        )

    def test_iteration(self) -> None:
        """Verifies RiskAnnotationList is iterable over items."""
        ral = self._make_list()
        types = [a.risk_type for a in ral]
        assert types == ["cmd_exec", "file_change", "cmd_exec"]

    def test_filter(self) -> None:
        """Verifies filter returns annotations matching risk_type."""
        ral = self._make_list()
        filtered = ral.filter(risk_type="cmd_exec")
        assert len(filtered.items) == 2

    def test_after(self) -> None:
        """Verifies after returns annotations following target."""
        ral = self._make_list()
        target = ral.items[1]
        after = ral.after(target)
        assert len(after.items) == 2
        assert after.items[0].key == "b"

    def test_after_not_found_raises(self) -> None:
        """Verifies after raises ValueError when target not in list."""
        ral = self._make_list()
        missing = RiskAnnotation(risk_type="unknown", key="z")
        with pytest.raises(ValueError, match="not found"):
            ral.after(missing)

    def test_find(self) -> None:
        """Verifies find returns annotations matching condition."""
        ral = self._make_list()
        cond = AttributeCondition(attr="risk_type", result="cmd_exec")
        found = ral.find(condition=cond)
        assert len(found.items) == 2


class TestBecomeInfo:
    """Tests for BecomeInfo.from_options with various option dicts."""

    def test_from_options_with_become(self) -> None:
        """Verifies from_options extracts become, become_user, become_method."""
        options: YAMLDict = {"become": True, "become_user": "root", "become_method": "sudo"}
        info = BecomeInfo.from_options(options)
        assert info is not None
        assert info.enabled is True
        assert info.user == "root"
        assert info.method == "sudo"

    def test_from_options_without_become(self) -> None:
        """Verifies from_options returns None when become not in options."""
        options: YAMLDict = {"hosts": "all"}
        assert BecomeInfo.from_options(options) is None

    def test_from_options_become_false(self) -> None:
        """Verifies from_options returns BecomeInfo with enabled False."""
        options: YAMLDict = {"become": False}
        info = BecomeInfo.from_options(options)
        assert info is not None
        assert info.enabled is False


class TestRuleResult:
    """Tests for RuleResult verdict normalization and detail get/set."""

    def test_verdict_normalization(self) -> None:
        """Verifies verdict normalizes 1 to True and 0 to False."""
        rr = RuleResult(verdict=1)  # type: ignore[arg-type]
        assert rr.verdict is True
        rr2 = RuleResult(verdict=0)  # type: ignore[arg-type]
        assert rr2.verdict is False

    def test_set_value_and_get_detail(self) -> None:
        """Verifies set_value adds to detail and get_detail returns it."""
        rr = RuleResult(detail={"key1": "val1"})
        rr.set_value("key2", "val2")
        detail = rr.get_detail()
        assert detail is not None
        assert detail["key2"] == "val2"

    def test_set_value_no_detail(self) -> None:
        """Verifies set_value leaves detail None when started without detail."""
        rr = RuleResult()
        rr.set_value("key", "val")
        assert rr.detail is None


class TestRuleMetadata:
    """Tests for RuleMetadata defaults."""

    def test_defaults(self) -> None:
        """Verifies RuleMetadata default rule_id and tags."""
        rm = RuleMetadata()
        assert rm.rule_id == ""
        assert rm.tags == ()

    def test_default_scope_is_task(self) -> None:
        """Verifies RuleMetadata defaults scope to TASK."""
        rm = RuleMetadata()
        assert rm.scope == RuleScope.TASK

    def test_get_metadata_preserves_scope(self) -> None:
        """Verifies Rule.get_metadata() copies the declared scope."""

        @dataclass
        class PlayRule(Rule):
            rule_id: str = "L042"
            description: str = "test"
            scope: str = RuleScope.PLAY
            enabled: bool = True

        rule = PlayRule()
        meta = rule.get_metadata()
        assert meta.scope == RuleScope.PLAY

    def test_get_metadata_preserves_non_default_scopes(self) -> None:
        """Verifies get_metadata() works for all non-TASK scopes."""
        for scope_val in (
            RuleScope.BLOCK,
            RuleScope.PLAYBOOK,
            RuleScope.ROLE,
            RuleScope.INVENTORY,
            RuleScope.COLLECTION,
        ):

            @dataclass
            class ScopedRule(Rule):
                rule_id: str = "TEST"
                description: str = "test"
                scope: str = scope_val
                enabled: bool = True

            meta = ScopedRule().get_metadata()
            assert meta.scope == scope_val, f"Expected {scope_val}, got {meta.scope}"


class TestSpecMutation:
    """Tests for SpecMutation defaults."""

    def test_defaults(self) -> None:
        """Verifies SpecMutation default key and changes."""
        sm = SpecMutation()
        assert sm.key is None
        assert sm.changes == []


class TestTaskCallsInTree:
    """Tests for TaskCallsInTree defaults."""

    def test_defaults(self) -> None:
        """Verifies TaskCallsInTree default root_key and taskcalls."""
        tct = TaskCallsInTree()
        assert tct.root_key == ""
        assert tct.taskcalls == []


class TestCollection:
    """Tests for Collection model defaults and fields."""

    def test_defaults(self) -> None:
        """Verifies Collection default type is collection."""
        c = Collection()
        assert c.type == "collection"

    def test_fields(self) -> None:
        """Verifies Collection stores name, path, playbooks, modules."""
        c = Collection(name="testcol", path="/path/to/col")
        assert c.name == "testcol"
        assert c.path == "/path/to/col"
        assert c.playbooks == []
        assert c.modules == []


class TestTask:
    """Tests for Task model defaults."""

    def test_defaults(self) -> None:
        """Verifies Task default type, module, and index."""
        t = Task()
        assert t.type == "task"
        assert t.module == ""
        assert t.index == -1


class TestRoleMetadataModel:
    """Tests for RoleMetadata from_dict, equality, and inequality."""

    def test_from_dict(self) -> None:
        """Verifies from_dict extracts fqcn, version from dict."""
        d: YAMLDict = {"fqcn": "ns.col.role", "name": "role", "type": "role", "version": "1.0", "hash": "abc"}
        rm = RoleMetadata.from_dict(d)
        assert rm.fqcn == "ns.col.role"
        assert rm.version == "1.0"

    def test_equality(self) -> None:
        """Verifies RoleMetadata equality when all fields match."""
        a = RoleMetadata(fqcn="ns.col.r", name="r", type="role", version="1", hash="a")
        b = RoleMetadata(fqcn="ns.col.r", name="r", type="role", version="1", hash="a")
        assert a == b

    def test_inequality_with_non_role(self) -> None:
        """Verifies __eq__ returns False for non-RoleMetadata."""
        rm = RoleMetadata()
        assert rm.__eq__("not a role") is False


class TestTaskFileMetadata:
    """Tests for TaskFileMetadata from_dict, equality, and inequality."""

    def test_from_dict(self) -> None:
        """Verifies from_dict extracts key, name from dict."""
        d: YAMLDict = {"key": "k1", "type": "taskfile", "name": "main.yml", "version": "1.0", "hash": "x"}
        tfm = TaskFileMetadata.from_dict(d)
        assert tfm.key == "k1"
        assert tfm.name == "main.yml"

    def test_equality(self) -> None:
        """Verifies TaskFileMetadata equality when all fields match."""
        a = TaskFileMetadata(key="k", type="tf", name="n", version="v", hash="h")
        b = TaskFileMetadata(key="k", type="tf", name="n", version="v", hash="h")
        assert a == b

    def test_inequality_with_non_tfm(self) -> None:
        """Verifies __eq__ returns False for non-TaskFileMetadata."""
        tfm = TaskFileMetadata()
        assert tfm.__eq__("not a tfm") is False


class TestActionGroupMetadata:
    """Tests for ActionGroupMetadata from_action_group, from_dict, equality."""

    def test_from_action_group(self) -> None:
        """Verifies from_action_group builds metadata from group name, modules, meta."""
        mods = [Module(name="mod1")]
        meta: YAMLDict = {"type": "collection", "name": "ns.col", "version": "1.0", "hash": "abc"}
        agm = ActionGroupMetadata.from_action_group("mygroup", mods, meta)
        assert agm is not None
        assert agm.group_name == "mygroup"
        assert agm.name == "ns.col"

    def test_from_action_group_empty_name(self) -> None:
        """Verifies from_action_group returns None for empty group name."""
        assert ActionGroupMetadata.from_action_group("", [Module()], {}) is None

    def test_from_action_group_empty_modules(self) -> None:
        """Verifies from_action_group returns None for empty modules list."""
        assert ActionGroupMetadata.from_action_group("grp", [], {}) is None

    def test_from_dict(self) -> None:
        """Verifies from_dict extracts group_name from dict."""
        d: YAMLDict = {
            "group_name": "g",
            "group_modules": [],
            "type": "t",
            "name": "n",
            "version": "v",
            "hash": "h",
        }
        agm = ActionGroupMetadata.from_dict(d)
        assert agm.group_name == "g"

    def test_equality(self) -> None:
        """Verifies ActionGroupMetadata equality when all fields match."""
        a = ActionGroupMetadata(group_name="g", name="n", type="t", version="v", hash="h")
        b = ActionGroupMetadata(group_name="g", name="n", type="t", version="v", hash="h")
        assert a == b

    def test_inequality_with_non_agm(self) -> None:
        """Verifies __eq__ returns False for non-ActionGroupMetadata."""
        agm = ActionGroupMetadata()
        assert agm.__eq__("not an agm") is False


class TestSeverity:
    """Tests for Severity IntEnum values (ADR-043)."""

    def test_levels(self) -> None:
        """Verifies Severity levels from CRITICAL to INFO with numeric ordering."""
        assert int(Severity.CRITICAL) == 6
        assert int(Severity.ERROR) == 5
        assert int(Severity.HIGH) == 4
        assert int(Severity.MEDIUM) == 3
        assert int(Severity.LOW) == 2
        assert int(Severity.INFO) == 1
        assert int(Severity.UNSPECIFIED) == 0

    def test_ordering(self) -> None:
        """Severity values support comparison for threshold gating."""
        assert Severity.CRITICAL > Severity.ERROR > Severity.HIGH
        assert Severity.HIGH > Severity.MEDIUM > Severity.LOW > Severity.INFO


class TestVariableDict:
    """Tests for VariableDict.print_table output."""

    def test_print_table(self) -> None:
        """Verifies print_table includes variable names and values in output."""
        data = {
            "my_var": [Variable(name="my_var", value="hello", type=VariableType.TaskVars)],
        }
        result = VariableDict.print_table(data)
        assert "my_var" in result
        assert "hello" in result


class TestPackageInstallDetail:
    """Tests for PackageInstallDetail pkg and is_mutable_pkg from _pkg_arg."""

    def test_with_args(self) -> None:
        """Verifies pkg and is_mutable_pkg derived from _pkg_arg."""
        detail = PackageInstallDetail(
            _pkg_arg=Arguments(raw="nginx", is_mutable=True),
        )
        assert detail.pkg == "nginx"
        assert detail.is_mutable_pkg is True


class TestKeyConfigChangeDetail:
    """Tests for KeyConfigChangeDetail is_deletion from _state_arg."""

    def test_deletion(self) -> None:
        """Verifies is_deletion True when state is absent."""
        detail = KeyConfigChangeDetail(
            _state_arg=Arguments(raw="absent"),
        )
        assert detail.is_deletion is True
