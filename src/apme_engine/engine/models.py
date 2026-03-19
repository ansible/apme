"""Engine data models: load metadata, objects, runs, annotations, and rule results."""

from __future__ import annotations

import builtins
import contextlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import jsonpickle
from rapidfuzz.distance import Levenshtein
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from apme_engine.ansi import table as ansi_table

# Recursive type for YAML/JSON values (defined before local imports to avoid circular import)
YAMLScalar = str | int | float | bool | None
YAMLValue = YAMLScalar | list["YAMLValue"] | dict[str, "YAMLValue"]
YAMLDict = dict[str, YAMLValue]
YAMLList = list[YAMLValue]

# Violation dicts from validators (rule_id, level, message, file, line, path, etc.)
ViolationDict = dict[str, str | int | list[int] | bool | None]


class RemediationClass(str, Enum):
    """Classification of remediation complexity for violations.

    Attributes:
        AUTO_FIXABLE: Tier 1 — deterministic transform exists.
        AI_CANDIDATE: Tier 2 — AI can propose a fix.
        MANUAL_REVIEW: Tier 3 — requires human judgment.
    """

    AUTO_FIXABLE = "auto-fixable"
    AI_CANDIDATE = "ai-candidate"
    MANUAL_REVIEW = "manual-review"


class RemediationResolution(str, Enum):
    """What happened during remediation of a specific finding.

    Attributes:
        UNRESOLVED: Initial state at scan time.
        TRANSFORM_FAILED: Deterministic transform returned applied=False.
        OSCILLATION: Convergence loop detected oscillation.
        AI_PROPOSED: AI proposed a fix (pending validation).
        AI_FAILED: AI call failed or returned no result.
        AI_LOW_CONFIDENCE: AI returned a low-confidence proposal.
        USER_REJECTED: User rejected the proposed fix.
        NEEDS_CROSS_FILE: Requires cross-file context (deferred to MCP tool).
        MANUAL: Requires manual review (play-level or structural issue).
    """

    UNRESOLVED = "unresolved"
    TRANSFORM_FAILED = "transform-failed"
    OSCILLATION = "oscillation"
    AI_PROPOSED = "ai-proposed"
    AI_FAILED = "ai-failed"
    AI_LOW_CONFIDENCE = "ai-low-confidence"
    USER_REJECTED = "user-rejected"
    NEEDS_CROSS_FILE = "needs-cross-file"
    MANUAL = "manual"


from . import yaml as ariyaml  # noqa: E402
from .finder import (  # noqa: E402
    identify_lines_with_jsonpath,
)
from .keyutil import (  # noqa: E402
    get_obj_info_by_key,
    set_call_object_key,
    set_collection_key,
    set_file_key,
    set_module_key,
    set_play_key,
    set_playbook_key,
    set_repository_key,
    set_role_key,
    set_task_key,
    set_taskfile_key,
)
from .utils import (  # noqa: E402
    equal,
    parse_bool,
    recursive_copy_dict,
)

if TYPE_CHECKING:
    from .risk_assessment_model import RAMClient


class PlaybookFormatError(Exception):
    """Raised when playbook structure or format is invalid."""


class TaskFormatError(Exception):
    """Raised when task structure or format is invalid."""


class FatalRuleResultError(Exception):
    """Raised when a rule reports a fatal result that should stop processing."""


class JSONSerializable:
    """Mixin for classes that can serialize to and from JSON via jsonpickle."""

    def dump(self) -> str:
        """Return JSON string representation.

        Returns:
            JSON string from to_json().

        """
        return self.to_json()

    def to_json(self) -> str:
        """Serialize this instance to a JSON string.

        Returns:
            JSON-encoded string via jsonpickle.

        """
        return str(jsonpickle.encode(self, make_refs=False))

    @classmethod
    def from_json(cls: type[JSONSerializable], json_str: str) -> JSONSerializable:
        """Deserialize an instance from a JSON string.

        Args:
            json_str: JSON-encoded string from to_json().

        Returns:
            New instance populated from the JSON.

        """
        instance = cls()
        loaded: object = jsonpickle.decode(json_str)
        if hasattr(loaded, "__dict__"):
            instance.__dict__.update(loaded.__dict__)
        return instance


class Resolver(Protocol):
    """Protocol for objects that apply resolution to Resolvable targets."""

    def apply(self, target: Resolvable) -> None:
        """Apply resolution to a single Resolvable target.

        Args:
            target: The Resolvable to resolve.

        """
        ...


class Resolvable:
    """Mixin for objects that can be resolved by a Resolver (e.g. resolve keys, refs)."""

    def resolve(self, resolver: Resolver) -> None:
        """Apply resolver to this instance and recursively to resolver_targets.

        Args:
            resolver: Resolver with an apply() method.

        Raises:
            ValueError: If resolver has no apply() method or apply is not callable.

        """
        if not hasattr(resolver, "apply"):
            raise ValueError("this resolver does not have apply() method")
        if not callable(resolver.apply):
            raise ValueError("resolver.apply is not callable")

        # apply resolver for this instance
        resolver.apply(self)

        # call resolve() for children recursively
        targets = self.resolver_targets
        if targets is None:
            return
        for t in targets:
            if isinstance(t, str):
                continue
            t.resolve(resolver)

        # apply resolver again here
        # because some attributes was not set at first
        resolver.apply(self)
        return

    @property
    def resolver_targets(self) -> list[Resolvable | str] | None:
        """Return child Resolvable objects; str entries are skipped.

        Returns:
            List of child Resolvable instances, or None.

        Raises:
            NotImplementedError: Subclasses must implement this property.

        """
        raise NotImplementedError


class LoadType:
    """Constants for load target types (project, collection, role, playbook, taskfile).

    Attributes:
        PROJECT: Project load type.
        COLLECTION: Collection load type.
        ROLE: Role load type.
        PLAYBOOK: Playbook load type.
        TASKFILE: Taskfile load type.
        UNKNOWN: Unknown load type.
    """

    PROJECT = "project"
    COLLECTION = "collection"
    ROLE = "role"
    PLAYBOOK = "playbook"
    TASKFILE = "taskfile"
    UNKNOWN = "unknown"


@dataclass
class Load(JSONSerializable):
    """Load metadata for a project, collection, role, playbook, or taskfile scan target.

    Attributes:
        target_name: Human-readable name of the load target.
        target_type: One of project, collection, role, playbook, taskfile.
        path: Filesystem path to the target.
        loader_version: Version of the loader that produced this load.
        playbook_yaml: Raw YAML of playbook content when applicable.
        playbook_only: Whether only playbook content was loaded.
        taskfile_yaml: Raw YAML of taskfile content when applicable.
        taskfile_only: Whether only taskfile content was loaded.
        base_dir: Base directory for the load.
        include_test_contents: Whether to include test content in the load.
        yaml_label_list: Labels assigned to YAML files.
        timestamp: When the load was produced.
        roles: List of role paths.
        playbooks: List of playbook paths.
        taskfiles: List of taskfile paths.
        modules: List of module paths.
        files: List of other file paths.
    """

    target_name: str = ""
    target_type: str = ""
    path: str = ""
    loader_version: str = ""
    playbook_yaml: str = ""
    playbook_only: bool = False
    taskfile_yaml: str = ""
    taskfile_only: bool = False
    base_dir: str = ""
    include_test_contents: bool = False
    yaml_label_list: list[str] = field(default_factory=list)
    timestamp: str = ""

    # the following variables are list of paths; not object
    roles: list[str] = field(default_factory=list)
    playbooks: list[str] = field(default_factory=list)
    taskfiles: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class Object(JSONSerializable):
    """Base object with type and key; used for playbook/role/task/module specs.

    Attributes:
        type: Kind of object (e.g. module, role, playbook).
        key: Unique key for lookup and resolution.
    """

    type: str = ""
    key: str = ""


@dataclass
class ObjectList(JSONSerializable):
    """List of Object/CallObject with key-indexed dict for lookup.

    Attributes:
        items: Ordered list of Object or CallObject instances.
    """

    items: list[Object | CallObject] = field(default_factory=list)
    _dict: dict[str, Object | CallObject] = field(default_factory=dict)

    def dump(self, fpath: str = "") -> str:
        """Return JSON string; optionally write to fpath.

        Args:
            fpath: Optional path to write JSON to disk.

        Returns:
            Newline-separated JSON string.

        """
        return self.to_json(fpath=fpath)

    def to_json(self, fpath: str = "") -> str:
        """Serialize items to newline-separated JSON; optionally write to fpath.

        Args:
            fpath: Optional path to write JSON to disk.

        Returns:
            Newline-separated JSON string.

        """
        lines: list[str] = [jsonpickle.encode(obj, make_refs=False) for obj in self.items]
        json_str = "\n".join(lines)
        if fpath != "":
            Path(fpath).write_text(json_str)
        return json_str

    def to_one_line_json(self) -> str:
        """Serialize items to a single JSON string.

        Returns:
            Single-line JSON string of all items.

        """
        return str(jsonpickle.encode(self.items, make_refs=False))

    @classmethod
    def from_json(cls: type[ObjectList], json_str: str = "", fpath: str = "") -> ObjectList:
        """Load instance from JSON string or from file at fpath.

        Args:
            json_str: Newline-separated JSON string.
            fpath: Path to read JSON from (used if json_str is empty).

        Returns:
            ObjectList populated from the JSON.

        """
        instance = cls()
        if fpath != "":
            json_str = Path(fpath).read_text()
        lines: list[str] = json_str.splitlines()
        items: list[object] = [jsonpickle.decode(obj_str) for obj_str in lines]
        instance.items = [cast(Object, obj) for obj in items]
        instance._update_dict()
        return instance

    def add(self, obj: Object | CallObject, update_dict: bool = True) -> None:
        """Append an item and optionally update the key index.

        Args:
            obj: Object or CallObject to add.
            update_dict: Whether to add the item to the key index immediately.

        """
        self.items.append(obj)
        if update_dict:
            self._add_dict_item(obj)
        return

    def merge(self, obj_list: ObjectList) -> None:
        """Extend this list with items from another ObjectList.

        Args:
            obj_list: ObjectList whose items to append.

        Raises:
            ValueError: If obj_list is not an ObjectList instance.

        """
        if not isinstance(obj_list, ObjectList):
            raise ValueError(f"obj_list must be an instance of ObjectList, but got {type(obj_list).__name__}")
        self.items.extend(obj_list.items)
        self._update_dict()
        return

    def find_by_attr(self, key: str, val: YAMLValue) -> list[Object | CallObject]:
        """Return items whose attribute key equals val.

        Args:
            key: Attribute name to check.
            val: Expected value.

        Returns:
            Matching items.

        """
        found = [obj for obj in self.items if obj.__dict__.get(key, None) == val]
        return found

    def find_by_type(self, type_name: str) -> list[Object | CallObject]:
        """Return items whose type attribute equals type_name.

        Args:
            type_name: Type string to match.

        Returns:
            Matching items.

        """
        return [obj for obj in self.items if hasattr(obj, "type") and obj.type == type_name]

    def find_by_key(self, key: str) -> Object | CallObject | None:
        """Return the item with the given key or None.

        Args:
            key: Unique key to look up.

        Returns:
            Matching item or None.

        """
        return self._dict.get(key, None)

    def contains(self, key: str = "", obj: Object | None = None) -> bool:
        """Return True if an item with the given key (or obj.key) exists.

        Args:
            key: Key string to search for.
            obj: Alternatively, an Object whose key is used.

        Returns:
            True if found.

        """
        if obj is not None:
            key = obj.key
        return self.find_by_key(key) is not None

    def update_dict(self) -> None:
        """Rebuild the key index from items."""
        self._update_dict()

    def _update_dict(self) -> None:
        """Rebuild _dict from current items."""
        for obj in self.items:
            self._dict[obj.key] = obj

    def _add_dict_item(self, obj: Object | CallObject) -> None:
        """Add a single item to the key index.

        Args:
            obj: Item to index.

        """
        self._dict[obj.key] = obj

    @property
    def resolver_targets(self) -> list[Object | CallObject]:
        """Return items for resolver traversal.

        Returns:
            The items list.

        """
        return self.items


@dataclass
class CallObject(JSONSerializable):
    """Object representing a call (e.g. module call) with spec, caller, depth, node_id.

    Attributes:
        type: Kind of call (e.g. modulecall, rolecall).
        key: Unique key for this call.
        called_from: Key of the caller (parent call).
        spec: The Object spec being invoked.
        depth: Depth in the call tree.
        node_id: Dot-separated node id in the tree.
    """

    type: str = ""
    key: str = ""
    called_from: str = ""
    spec: Object = field(default_factory=Object)
    depth: int = -1
    node_id: str = ""

    @classmethod
    def from_spec(cls: builtins.type[CallObject], spec: Object, caller: CallObject | None, index: int) -> CallObject:
        """Build a CallObject from a spec, optional caller, and index (for node_id).

        Args:
            spec: The Object spec being called.
            caller: Parent CallObject, or None if root.
            index: Sibling index for node_id computation.

        Returns:
            New CallObject linked to spec and caller.

        """
        instance = cls()
        instance.spec = spec
        caller_key = "None"
        depth = 0
        node_id = "0"
        if caller:
            instance.called_from = caller.key
            caller_key = caller.key
            depth = caller.depth + 1
            index_str = "0"
            if index >= 0:
                index_str = str(index)
            node_id = caller.node_id + "." + index_str
        instance.depth = depth
        instance.node_id = node_id
        instance.key = set_call_object_key(cls.__name__, spec.key, caller_key)
        return instance


class RunTargetType:
    """Constants for run target kinds (playbook, play, role, taskfile, task).

    Attributes:
        Playbook: Playbook call type.
        Play: Play call type.
        Role: Role call type.
        TaskFile: Taskfile call type.
        Task: Task call type.
    """

    Playbook = "playbookcall"
    Play = "playcall"
    Role = "rolecall"
    TaskFile = "taskfilecall"
    Task = "taskcall"


@dataclass
class RunTarget:
    """Base for a single run target (playbook/play/role/taskfile/task) with annotations.

    Attributes:
        type: Kind of run target (playbookcall, playcall, rolecall, taskfilecall, taskcall).
        spec: Underlying Object (from CallObject in subclasses).
        key: Unique key for this target.
        annotations: List of annotations (e.g. risk annotations) on this target.
    """

    type: str = ""
    spec: Object = field(default_factory=Object)  # from CallObject in subclasses
    key: str = ""
    annotations: list[Annotation] = field(default_factory=list)

    def file_info(self) -> tuple[str, str | None]:
        """Return (defined_in path, line info) for this target's spec.

        Returns:
            Tuple of (file path, line info string or None).

        """
        file = getattr(self.spec, "defined_in", "") if self.spec else ""
        lines: str | None = None
        return file, lines

    def has_annotation_by_condition(self, cond: AnnotationCondition) -> bool:
        """Return True if any annotation matches the condition.

        Args:
            cond: Condition to test against annotations.

        Returns:
            True if any annotation matches.

        """
        return False

    def get_annotation_by_condition(self, cond: AnnotationCondition) -> Annotation | RiskAnnotation | None:
        """Return the first annotation matching the condition or None.

        Args:
            cond: Condition to test against annotations.

        Returns:
            First matching annotation or None.

        """
        return None


@dataclass
class RunTargetList:
    """List of RunTarget with iteration and indexing.

    Attributes:
        items: List of RunTarget instances.
    """

    items: list[RunTarget] = field(default_factory=list)

    _i: int = 0

    def __len__(self) -> int:
        """Return number of items.

        Returns:
            Count of RunTarget items.

        """
        return len(self.items)

    def __iter__(self) -> RunTargetList:
        """Return iterator over items.

        Returns:
            Self as the iterator.

        """
        return self

    def __next__(self) -> RunTarget:
        """Return next item; raises StopIteration when exhausted.

        Returns:
            Next RunTarget.

        Raises:
            StopIteration: When all items have been yielded.

        """
        if self._i == len(self.items):
            self._i = 0
            raise StopIteration()
        item = self.items[self._i]
        self._i += 1
        return item

    def __getitem__(self, i: int) -> RunTarget:
        """Return item at index i.

        Args:
            i: Zero-based index.

        Returns:
            RunTarget at position i.

        """
        return self.items[i]


@dataclass
class File:
    """Represents a file (playbook, task, vars) with path, body, label, and annotations.

    Attributes:
        type: Always "file".
        name: File path or name.
        key: Unique key for lookup.
        local_key: Local key within role/collection.
        role: Role name if file belongs to a role.
        collection: Collection name if file belongs to a collection.
        body: Raw file content.
        data: Parsed YAML/value when applicable.
        encrypted: Whether content is encrypted.
        error: Error message if load failed.
        label: Classification (playbook, taskfile, others).
        defined_in: Path where this file is defined.
        annotations: Map of annotation key to value.
    """

    type: str = "file"
    name: str = ""
    key: str = ""
    local_key: str = ""
    role: str = ""
    collection: str = ""

    body: str = ""
    data: YAMLValue | None = None
    encrypted: bool = False
    error: str = ""
    label: str = ""
    defined_in: str = ""

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from name/role/collection via keyutil."""
        set_file_key(self)

    def children_to_key(self) -> File:
        """Return self (File has no keyed children).

        Returns:
            This File instance.

        """
        return self

    @property
    def resolver_targets(self) -> None:
        """No child targets to resolve."""
        return None


@dataclass
class ModuleArgument:
    """Ansible module argument metadata (name, type, default, required, etc.).

    Attributes:
        name: Argument name.
        type: Argument type (e.g. str, list).
        elements: Type of list elements when type is list.
        default: Default value.
        required: Whether the argument is required.
        description: Help text for the argument.
        choices: Allowed values when constrained.
        aliases: Alternative names for this argument.
    """

    name: str = ""
    type: str | None = None
    elements: str | None = None
    default: YAMLValue = None
    required: bool = False
    description: str = ""
    choices: list[YAMLScalar] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def available_keys(self) -> list[str]:
        """Return name plus aliases for matching user-provided keys.

        Returns:
            List of valid key strings for this argument.

        """
        keys = [self.name]
        if self.aliases:
            keys.extend(self.aliases)
        return keys


@dataclass
class Module(Object, Resolvable):
    """Ansible module spec: FQCN, args, documentation, defined_in.

    Attributes:
        type: Always "module".
        name: Short or FQCN name.
        fqcn: Fully qualified collection name.
        key: Unique key for lookup.
        local_key: Local key within collection/role.
        collection: Collection name.
        role: Role name if from a role.
        documentation: Module DOCUMENTATION string.
        examples: Module EXAMPLES string.
        arguments: List of ModuleArgument.
        defined_in: Path to the module file.
        builtin: Whether this is a builtin module.
        used_in: List of paths where this module is used (resolved later).
        annotations: Map of annotation key to value.
    """

    type: str = "module"
    name: str = ""
    fqcn: str = ""
    key: str = ""
    local_key: str = ""
    collection: str = ""
    role: str = ""
    documentation: str = ""
    examples: str = ""
    arguments: list[ModuleArgument] = field(default_factory=list)
    defined_in: str = ""
    builtin: bool = False
    used_in: list[str] = field(default_factory=list)  # resolved later

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from name/collection via keyutil."""
        set_module_key(self)

    def children_to_key(self) -> Module:
        """Return self (Module has no keyed children).

        Returns:
            This Module instance.

        """
        return self

    @property
    def resolver_targets(self) -> None:
        """No child targets to resolve."""
        return None


@dataclass
class ModuleCall(CallObject, Resolvable):
    """Call object for a module invocation.

    Attributes:
        type: Always "modulecall".

    """

    type: str = "modulecall"


@dataclass
class Collection(Object, Resolvable):
    """Collection spec: playbooks, taskfiles, roles, modules, metadata.

    Attributes:
        type: Always "collection".
        name: Collection name (namespace.name).
        path: Path to the collection root.
        key: Unique key for lookup.
        local_key: Local key.
        metadata: Collection metadata dict.
        meta_runtime: Runtime meta dict.
        files: Files dict.
        playbooks: List of Playbook or key.
        taskfiles: List of TaskFile or key.
        roles: List of Role or key.
        modules: List of Module or key.
        dependency: Dependency info.
        requirements: Requirements info.
        annotations: Map of annotation key to value.
        variables: Variables dict.
        options: Options dict.
    """

    type: str = "collection"
    name: str = ""
    path: str = ""
    key: str = ""
    local_key: str = ""
    metadata: YAMLDict = field(default_factory=dict)
    meta_runtime: YAMLDict = field(default_factory=dict)
    files: YAMLDict = field(default_factory=dict)
    playbooks: list[Playbook | str] = field(default_factory=list)
    taskfiles: list[TaskFile | str] = field(default_factory=list)
    roles: list[Role | str] = field(default_factory=list)
    modules: list[Module | str] = field(default_factory=list)
    dependency: YAMLDict = field(default_factory=dict)
    requirements: YAMLDict = field(default_factory=dict)

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    variables: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key and sort playbooks/taskfiles/roles/modules by key."""
        set_collection_key(self)

    def children_to_key(self) -> Collection:
        """Sort child refs by key and return self.

        Returns:
            This Collection instance with children sorted.

        """
        module_keys = [m.key if isinstance(m, Module) else m for m in self.modules]
        self.modules = cast(list["Module | str"], sorted(module_keys))

        playbook_keys = [p.key if isinstance(p, Playbook) else p for p in self.playbooks]
        self.playbooks = cast(list["Playbook | str"], sorted(playbook_keys))

        role_keys = [r.key if isinstance(r, Role) else r for r in self.roles]
        self.roles = cast(list["Role | str"], sorted(role_keys))

        taskfile_keys = [tf.key if isinstance(tf, TaskFile) else tf for tf in self.taskfiles]
        self.taskfiles = cast(list["TaskFile | str"], sorted(taskfile_keys))
        return self

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Playbooks, taskfiles, roles, modules (may be keys until resolved).

        Returns:
            Combined list of child objects or key strings.

        """
        return cast(
            list["Resolvable | str"],
            list(self.playbooks) + list(self.taskfiles) + list(self.roles) + list(self.modules),
        )


@dataclass
class CollectionCall(CallObject, Resolvable):
    """Call object for a collection invocation.

    Attributes:
        type: Always "collectioncall".

    """

    type: str = "collectioncall"


@dataclass
class TaskCallsInTree(JSONSerializable):
    """Root key and list of TaskCall in a tree.

    Attributes:
        root_key: Key of the root node in the tree.
        taskcalls: Ordered list of TaskCall instances in the tree.

    """

    root_key: str = ""
    taskcalls: list[TaskCall] = field(default_factory=list)


@dataclass
class VariablePrecedence:
    """Variable precedence level (name and order for Ansible precedence).

    Attributes:
        name: Precedence level name (e.g. role_defaults).
        order: Numeric order for comparison; higher wins.

    """

    name: str = ""
    order: int = -1

    def __str__(self) -> str:
        """Return precedence name.

        Returns:
            The name string.

        """
        return self.name

    def __repr__(self) -> str:
        """Return precedence name.

        Returns:
            The name string.

        """
        return self.name

    def __eq__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if orders are equal, NotImplemented if types differ.

        """
        if not isinstance(__o, VariablePrecedence):
            return NotImplemented
        return self.order == __o.order

    def __ne__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if orders differ.

        """
        return not self.__eq__(__o)

    def __lt__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if self.order < __o.order.

        """
        if not isinstance(__o, VariablePrecedence):
            return NotImplemented
        return self.order < __o.order

    def __le__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if self.order <= __o.order.

        """
        if not isinstance(__o, VariablePrecedence):
            return NotImplemented
        return self.__lt__(__o) or self.__eq__(__o)

    def __gt__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if self.order > __o.order.

        """
        return not self.__le__(__o)

    def __ge__(self, __o: object) -> bool:
        """Compare by order.

        Args:
            __o: Other object to compare.

        Returns:
            True if self.order >= __o.order.

        """
        return not self.__lt__(__o)


class VariableType:
    """Ansible variable precedence types (command line, role defaults, play vars, etc.).

    Attributes:
        Unknown: Unknown variable type.
        CommandLineValues: Command line values precedence.
        RoleDefaults: Role defaults precedence.
        InventoryFileOrScriptGroupVars: Inventory group vars precedence.
        InventoryGroupVarsAll: Inventory group vars all precedence.
        PlaybookGroupVarsAll: Playbook group vars all precedence.
        InventoryGroupVarsAny: Inventory group vars any precedence.
        PlaybookGroupVarsAny: Playbook group vars any precedence.
        InventoryFileOrScriptHostVars: Inventory host vars precedence.
        InventoryHostVarsAny: Inventory host vars any precedence.
        PlaybookHostVarsAny: Playbook host vars any precedence.
        HostFacts: Host facts precedence.
        PlayVars: Play vars precedence.
        PlayVarsPrompt: Play vars prompt precedence.
        PlayVarsFiles: Play vars files precedence.
        RoleVars: Role vars precedence.
        BlockVars: Block vars precedence.
        TaskVars: Task vars precedence.
        IncludeVars: Include vars precedence.
        SetFacts: Set facts precedence.
        RegisteredVars: Registered vars precedence.
        RoleParams: Role params precedence.
        IncludeParams: Include params precedence.
        ExtraVars: Extra vars precedence.
        LoopVars: Loop vars precedence.
    """

    # When resolving variables, sometimes find unknown variables (e.g. undefined variable)
    # so we consider it as one type of variable
    Unknown = VariablePrecedence("unknown", -100)
    # Variable Precedence
    # https://docs.ansible.com/ansible/latest/playbook_guide
    #     /playbooks_variables.html#understanding-variable-precedence
    CommandLineValues = VariablePrecedence("command_line_values", 1)
    RoleDefaults = VariablePrecedence("role_defaults", 2)
    InventoryFileOrScriptGroupVars = VariablePrecedence("inventory_file_or_script_group_vars", 3)
    InventoryGroupVarsAll = VariablePrecedence("inventory_group_vars_all", 4)
    PlaybookGroupVarsAll = VariablePrecedence("playbook_group_vars_all", 5)
    InventoryGroupVarsAny = VariablePrecedence("inventory_group_vars_any", 6)
    PlaybookGroupVarsAny = VariablePrecedence("playbook_group_vars_any", 7)
    InventoryFileOrScriptHostVars = VariablePrecedence("inventory_file_or_script_host_vars", 8)
    InventoryHostVarsAny = VariablePrecedence("inventory_host_vars_any", 9)
    PlaybookHostVarsAny = VariablePrecedence("playbook_host_vars_any", 10)
    HostFacts = VariablePrecedence("host_facts", 11)
    PlayVars = VariablePrecedence("play_vars", 12)
    PlayVarsPrompt = VariablePrecedence("play_vars_prompt", 13)
    PlayVarsFiles = VariablePrecedence("play_vars_files", 14)
    RoleVars = VariablePrecedence("role_vars", 15)
    BlockVars = VariablePrecedence("block_vars", 16)
    TaskVars = VariablePrecedence("task_vars", 17)
    IncludeVars = VariablePrecedence("include_vars", 18)
    # we deal with set_facts and registered_vars separately
    # because the expression in a fact will be evaluated everytime it is used
    SetFacts = VariablePrecedence("set_facts", 19)
    RegisteredVars = VariablePrecedence("registered_vars", 20)
    RoleParams = VariablePrecedence("role_params", 21)
    IncludeParams = VariablePrecedence("include_params", 22)
    ExtraVars = VariablePrecedence("extra_vars", 23)
    # vars defined in `loop` cannot be overridden by the vars above
    # so we put this as a highest precedence var type
    LoopVars = VariablePrecedence("loop_vars", 24)


immutable_var_types = [VariableType.LoopVars]


@dataclass
class Variable:
    """Single variable with name, value, precedence type, setter, used_in.

    Attributes:
        name: Variable name.
        value: Resolved or raw value.
        type: Variable precedence (e.g. role_defaults, play_vars).
        elements: Nested variables when value is structured.
        setter: Task or location that set this variable.
        used_in: Task or location where this variable is used.
    """

    name: str = ""
    value: YAMLValue = None
    type: VariablePrecedence | None = None
    elements: list[Variable] = field(default_factory=list)
    setter: str | TaskCall | None = None
    used_in: str | TaskCall | None = None

    @property
    def is_mutable(self) -> bool:
        """True if this variable can be overridden (not loop/immutable).

        Returns:
            True if the variable precedence allows override.

        """
        return self.type not in immutable_var_types if self.type else True


@dataclass
class VariableDict:
    """Map variable name to list of Variable (by precedence)."""

    _dict: dict[str, list[Variable]] = field(default_factory=dict)

    @staticmethod
    def print_table(data: dict[str, list[Variable]]) -> str:
        """Format variable data as a table string.

        Args:
            data: Map from variable name to list of Variable by precedence.

        Returns:
            Formatted table string.

        """
        d = VariableDict(_dict=data)
        type_labels: list[VariablePrecedence] = []
        found_type_label_names: list[str] = []
        for v_list in d._dict.values():
            for v in v_list:
                if not v.type or v.type.name in found_type_label_names:
                    continue
                type_labels.append(v.type)
                found_type_label_names.append(v.type.name)
        type_labels = sorted(type_labels, key=lambda x: x.order, reverse=True)

        headers = ["NAME", *(t.name.upper() for t in type_labels)]
        rows: list[list[str]] = []
        for v_name in d._dict:
            v_list = d._dict[v_name]
            row: list[str] = [v_name]
            for t in type_labels:
                cell_value: YAMLValue = "-"
                for v in v_list:
                    if v.type != t:
                        continue
                    cell_value = v.value
                    if isinstance(cell_value, str) and cell_value == "":
                        cell_value = '""'
                row.append(str(cell_value))
            rows.append(row)
        return ansi_table(headers, rows)


class ArgumentsType:
    """Argument container type: simple, list, or dict.

    Attributes:
        SIMPLE: Simple argument type.
        LIST: List argument type.
        DICT: Dict argument type.
    """

    SIMPLE = "simple"
    LIST = "list"
    DICT = "dict"


@dataclass
class Arguments:
    """Task/module arguments: raw, vars, resolved/templated value, mutability.

    Attributes:
        type: One of simple, list, dict.
        raw: Raw argument value (pre-resolution).
        vars: List of Variable referenced in the value.
        resolved: Whether the value has been resolved.
        templated: Resolved/templated value when applicable.
        is_mutable: Whether the value contains mutable variable refs.
    """

    type: str = ArgumentsType.SIMPLE
    raw: YAMLValue = None
    vars: list[Variable] = field(default_factory=list)
    resolved: bool = False
    templated: YAMLValue = None
    is_mutable: bool = False

    def get(self, key: str = "") -> Arguments | None:
        """Return a sub-Arguments for the given key (or self if key is empty).

        Args:
            key: Sub-key into the raw dict; empty returns self-level.

        Returns:
            Sub-Arguments or None if key is not present.

        """
        sub_raw: YAMLValue = None
        sub_templated: YAMLValue = None
        if key == "":
            sub_raw = self.raw
            sub_templated = self.templated
        else:
            if isinstance(self.raw, dict):
                sub_raw = self.raw.get(key, None)
                if self.templated and isinstance(self.templated, list | tuple):
                    first: YAMLValue = self.templated[0]
                    sub_templated = first.get(key, None) if isinstance(first, dict) else self.templated
            else:
                sub_raw = self.raw
                sub_templated = self.templated
        if not sub_raw:
            return None

        _vars: list[Variable] = []
        sub_type = ArgumentsType.SIMPLE
        if isinstance(sub_raw, str):
            for v in self.vars:
                if v.name in sub_raw:
                    _vars.append(v)
        elif isinstance(sub_raw, list):
            sub_type = ArgumentsType.LIST
        elif isinstance(sub_raw, dict):
            sub_type = ArgumentsType.DICT
        is_mutable = False
        for v in _vars:
            if v.is_mutable:
                is_mutable = True
                break

        return Arguments(
            type=sub_type,
            raw=sub_raw,
            vars=_vars,
            resolved=self.resolved,
            templated=sub_templated,
            is_mutable=is_mutable,
        )


class LocationType:
    """Location kind: file, dir, or url.

    Attributes:
        FILE: File location type.
        DIR: Directory location type.
        URL: URL location type.
    """

    FILE = "file"
    DIR = "dir"
    URL = "url"


@dataclass
class Location:
    """Path/URL location with optional variable refs.

    Attributes:
        type: One of file, dir, url.
        value: Path or URL string.
        vars: Variables referenced in the value.
    """

    type: str = ""
    value: str = ""
    vars: list[Variable] = field(default_factory=list)

    _args: Arguments | None = None

    def __post_init__(self) -> None:
        """Populate value and vars from _args if provided."""
        if self._args:
            self.value = str(self._args.raw) if self._args.raw is not None else ""
            self.vars = self._args.vars

    @property
    def is_mutable(self) -> bool:
        """True if location has variable refs.

        Returns:
            True if vars is non-empty.

        """
        return len(self.vars) > 0

    @property
    def is_empty(self) -> bool:
        """True if type and value are empty.

        Returns:
            True if both type and value are falsy.

        """
        return not self.type and not self.value

    def is_inside(self, loc: Location) -> bool:
        """Return True if this location is inside the given location.

        Args:
            loc: Outer location to check against.

        Returns:
            True if this location's path starts with loc's path.

        Raises:
            ValueError: If loc is not a Location instance.

        """
        if not isinstance(loc, Location):
            raise ValueError(f"is_inside() expect Location but given {type(loc)}")
        return loc.contains(self)

    def contains(self, target: Location | list[Location], any_mode: bool = False, all_mode: bool = True) -> bool:
        """Return True if target path is under this location; list uses any_mode or all_mode.

        Args:
            target: Single Location or list of Locations.
            any_mode: If True, return True when any target is contained.
            all_mode: If True (default), return True only when all targets are contained.

        Returns:
            True if target(s) satisfy the containment check.

        Raises:
            ValueError: If target is invalid type or mode is invalid.

        """
        if isinstance(target, list):
            if any_mode:
                return self.contains_any(target_list=target)
            elif all_mode:
                return self.contains_all(target_list=target)
            else:
                raise ValueError('contains() must be run in either "any" or "all" mode')

        else:
            if not isinstance(target, Location):
                raise ValueError(f"contains() expect Location or list of Location, but given {type(target)}")

        my_path = self.value
        target_path = target.value
        return bool(target_path.startswith(my_path))

    def contains_any(self, target_list: list[Location]) -> bool:
        """Return True if this location contains any of the targets.

        Args:
            target_list: Locations to check.

        Returns:
            True if at least one target is contained.

        """
        return any(self.contains(target) for target in target_list)

    def contains_all(self, target_list: list[Location]) -> bool:
        """Return True if this location contains all of the targets.

        Args:
            target_list: Locations to check.

        Returns:
            True if every target is contained.

        """
        count = 0
        for target in target_list:
            if self.contains(target):
                count += 1
        return count == len(target_list)


class AnnotationDetail:
    """Base for risk annotation detail (transfer, package install, file change, etc.)."""


@dataclass
class NetworkTransferDetail(AnnotationDetail):
    """Source and destination locations for network transfer annotations.

    Attributes:
        src: Source Location.
        dest: Destination Location.
        is_mutable_src: True if src contains variable refs.
        is_mutable_dest: True if dest contains variable refs.

    """

    src: Location | None = None
    dest: Location | None = None
    is_mutable_src: bool = False
    is_mutable_dest: bool = False

    _src_arg: Arguments | None = None
    _dest_arg: Arguments | None = None

    def __post_init__(self) -> None:
        """Build src/dest Locations from _src_arg/_dest_arg if provided."""
        if self._src_arg:
            self.src = Location(_args=self._src_arg)
            if self._src_arg.is_mutable:
                self.is_mutable_src = True

        if self._dest_arg:
            self.dest = Location(_args=self._dest_arg)
            if self._dest_arg.is_mutable:
                self.is_mutable_dest = True


@dataclass
class InboundTransferDetail(NetworkTransferDetail):
    """Inbound transfer (e.g. get_url) annotation detail."""

    def __post_init__(self) -> None:
        """Delegate to NetworkTransferDetail.__post_init__."""
        super().__post_init__()


@dataclass
class OutboundTransferDetail(NetworkTransferDetail):
    """Outbound transfer (e.g. copy) annotation detail."""

    def __post_init__(self) -> None:
        """Delegate to NetworkTransferDetail.__post_init__."""
        super().__post_init__()


@dataclass
class PackageInstallDetail(AnnotationDetail):
    """Package install (yum, dnf, apt) annotation: pkg, version, options.

    Attributes:
        pkg: Package name or Arguments.
        version: Version string, Arguments, or list of Variables.
        is_mutable_pkg: True if pkg contains variable refs.
        disable_validate_certs: True if validate_certs is disabled.
        disable_gpg_check: True if GPG signature checking is disabled.
        allow_downgrade: True if allow_downgrade is set.

    """

    pkg: str | Arguments = ""
    version: str | Arguments | list[Variable] = ""
    is_mutable_pkg: bool = False
    disable_validate_certs: bool = False
    disable_gpg_check: bool = False
    allow_downgrade: bool = False

    _pkg_arg: Arguments | None = None
    _version_arg: Arguments | None = None
    _allow_downgrade_arg: Arguments | None = None
    _validate_certs_arg: Arguments | None = None
    _disable_gpg_check_arg: Arguments | None = None

    def __post_init__(self) -> None:
        """Build pkg/version from _pkg_arg/_version_arg if provided."""
        if self._pkg_arg:
            self.pkg = cast(str | Arguments, self._pkg_arg.raw)
            if self._pkg_arg.is_mutable:
                self.is_mutable_pkg = True
        if self._version_arg:
            self.version = self._version_arg.vars
        if self._allow_downgrade_arg and _convert_to_bool(self._allow_downgrade_arg.raw):
            self.allow_downgrade = True
        if self._validate_certs_arg and not _convert_to_bool(self._validate_certs_arg.raw):
            self.disable_validate_certs = True
        if self._disable_gpg_check_arg and _convert_to_bool(self._disable_gpg_check_arg.raw):
            self.disable_gpg_check = True


@dataclass
class KeyConfigChangeDetail(AnnotationDetail):
    """Key/config change (e.g. lineinfile state=absent) annotation detail.

    Attributes:
        is_deletion: True when state is absent.
        is_mutable_key: True if key contains variable refs.
        key: Config key string or list of Variables.

    """

    is_deletion: bool = False
    is_mutable_key: bool = False
    key: str | list[Variable] = ""

    _key_arg: Arguments | None = None
    _state_arg: Arguments | None = None

    def __post_init__(self) -> None:
        """Build key from _key_arg and check deletion state."""
        if self._key_arg:
            self.key = self._key_arg.vars
            if self._key_arg and self._key_arg.is_mutable:
                self.is_mutable_key = True
        if self._state_arg and self._state_arg.raw == "absent":
            self.is_deletion = True


@dataclass
class FileChangeDetail(AnnotationDetail):
    """File change (path, src, mode, state, unsafe_write) annotation detail.

    Attributes:
        path: Destination path Location.
        src: Source path Location.
        is_mutable_path: True if path has variable refs.
        is_mutable_src: True if src has variable refs.
        is_unsafe_write: True when unsafe_writes is enabled.
        is_deletion: True when state is absent.
        is_insecure_permissions: True when mode is 0777 or 1777.

    """

    path: Location | None = None
    src: Location | None = None
    is_mutable_path: bool = False
    is_mutable_src: bool = False
    is_unsafe_write: bool = False
    is_deletion: bool = False
    is_insecure_permissions: bool = False

    _path_arg: Arguments | None = None
    _src_arg: Arguments | None = None
    _mode_arg: Arguments | None = None
    _state_arg: Arguments | None = None
    _unsafe_write_arg: Arguments | None = None

    def __post_init__(self) -> None:
        """Build path/src Locations and check mode/state/unsafe_writes."""
        if self._mode_arg and self._mode_arg.raw in ["1777", "0777"]:
            self.is_insecure_permissions = True
        if self._state_arg and self._state_arg.raw == "absent":
            self.is_deletion = True
        if self._path_arg:
            self.path = Location(_args=self._path_arg)
            if self._path_arg.is_mutable:
                self.is_mutable_path = True
        if self._src_arg:
            self.src = Location(_args=self._src_arg)
            if self._src_arg.is_mutable:
                self.is_mutable_src = True
        if self._unsafe_write_arg and _convert_to_bool(self._unsafe_write_arg.raw):
            self.is_unsafe_write = True


execution_programs: list[str] = ["sh", "bash", "zsh", "fish", "ash", "python*", "java*", "node*"]
non_execution_programs: list[str] = ["tar", "gunzip", "unzip", "mv", "cp"]


@dataclass
class CommandExecDetail(AnnotationDetail):
    """Command execution annotation: command args and extracted exec file locations.

    Attributes:
        command: Arguments wrapping the shell/command string.
        exec_files: Locations of executable files parsed from the command.
        is_mutable_cmd: True if the command contains variable references.

    """

    command: Arguments | None = None
    exec_files: list[Location] = field(default_factory=list)
    is_mutable_cmd: bool = False

    def __post_init__(self) -> None:
        """Parse command into exec_files on construction."""
        if self.command and getattr(self.command, "is_mutable", False):
            self.is_mutable_cmd = True
        self.exec_files = self.extract_exec_files()

    def extract_exec_files(self) -> list[Location]:
        """Parse command arguments and extract executable file Locations.

        Returns:
            List of Location pointing to extracted exec files.

        """
        cmd_str: str | list[str] | YAMLDict = cast(
            "str | list[str] | YAMLDict", "" if not self.command else (self.command.raw or "")
        )
        if isinstance(cmd_str, list):
            cmd_str = " ".join(str(x) for x in cmd_str)
        elif isinstance(cmd_str, dict):
            cmd_str = str(cmd_str.get("cmd", ""))
        elif not isinstance(cmd_str, str):
            cmd_str = str(cmd_str) if cmd_str else ""
        lines: list[str] = cmd_str.splitlines()
        exec_files = []
        for line in lines:
            parts = []
            is_in_variable = False
            concat_p = ""
            for p in line.split(" "):
                if "{{" in p and "}}" not in p:
                    is_in_variable = True
                if "}}" in p:
                    is_in_variable = False
                concat_p += " " + p if concat_p != "" else p
                if not is_in_variable:
                    parts.append(concat_p)
                    concat_p = ""
            found_program = None
            for i, p in enumerate(parts):
                if i == 0:
                    program = p if "/" not in p else p.split("/")[-1]
                    # filter out some specific non-exec patterns
                    if program in non_execution_programs:
                        break
                    # if the command string is like "python {{ python_script_path }}",
                    # {{ python_script_path }} is the exec file instead of "python"
                    if program in execution_programs:
                        continue
                    # for the case that the program name is like "python-3.6"
                    for exec_p in execution_programs:
                        if exec_p[-1] == "*" and program.startswith(exec_p[:-1]):
                            continue
                if p.startswith("-"):
                    continue
                if found_program is None:
                    found_program = p
                    break
            if found_program and self.command:
                exec_file_name = found_program
                related_vars = [v for v in self.command.vars if v.name in exec_file_name]
                location_type = LocationType.FILE
                exec_file = Location(
                    type=location_type,
                    value=exec_file_name,
                    vars=related_vars,
                )
                exec_files.append(exec_file)
        return exec_files


def _convert_to_bool(a: YAMLValue) -> bool | None:
    """Convert YAML value to bool; supports bool and 'true'/'yes' strings.

    Args:
        a: Value to convert.

    Returns:
        Boolean or None if type is unsupported.

    """
    if type(a) is bool:
        return bool(a)
    if type(a) is str:
        return bool(a == "true" or a == "True" or a == "yes")
    return None


@dataclass
class Annotation(JSONSerializable):
    """Base annotation: key, value, rule_id, type.

    Attributes:
        key: Annotation key.
        value: Annotation value.
        rule_id: Rule that produced this annotation.
        type: Annotation subtype (e.g. variable_annotation, risk_annotation).
    """

    key: str = ""
    value: YAMLValue = None

    rule_id: str = ""

    # TODO: avoid Annotation variants and remove `type`
    type: str = ""


@dataclass
class VariableAnnotation(Annotation):
    """Annotation for variable usage (option_value from task args).

    Attributes:
        type: Always "variable_annotation".
        option_value: Arguments holding the variable reference.
    """

    type: str = "variable_annotation"
    option_value: Arguments = field(default_factory=lambda: Arguments())


class RiskType:
    """Base for risk type constants."""


class DefaultRiskType(RiskType):
    """Default risk categories (cmd_exec, inbound, outbound, file_change, etc.).

    Attributes:
        NONE: No risk.
        CMD_EXEC: Command execution risk.
        INBOUND: Inbound transfer risk.
        OUTBOUND: Outbound transfer risk.
        FILE_CHANGE: File change risk.
        SYSTEM_CHANGE: System change risk.
        NETWORK_CHANGE: Network change risk.
        CONFIG_CHANGE: Config change risk.
        PACKAGE_INSTALL: Package install risk.
        PRIVILEGE_ESCALATION: Privilege escalation risk.
    """

    NONE = ""
    CMD_EXEC = "cmd_exec"
    INBOUND = "inbound_transfer"
    OUTBOUND = "outbound_transfer"
    FILE_CHANGE = "file_change"
    SYSTEM_CHANGE = "system_change"
    NETWORK_CHANGE = "network_change"
    CONFIG_CHANGE = "config_change"
    PACKAGE_INSTALL = "package_install"
    PRIVILEGE_ESCALATION = "privilege_escalation"


@dataclass
class RiskAnnotation(Annotation, NetworkTransferDetail, CommandExecDetail):
    """Risk annotation combining base annotation with transfer/exec detail.

    Attributes:
        type: Always "risk_annotation".
        risk_type: Category of risk (e.g. cmd_exec, inbound_transfer).
    """

    type: str = "risk_annotation"
    risk_type: str | RiskType = ""

    @classmethod
    def init(
        cls: builtins.type[RiskAnnotation],
        risk_type: str | RiskType,
        detail: AnnotationDetail,
    ) -> RiskAnnotation:
        """Build a RiskAnnotation from risk_type and detail (copies detail attrs).

        Args:
            risk_type: Risk category string or RiskType.
            detail: AnnotationDetail whose attributes are copied.

        Returns:
            New RiskAnnotation instance.

        """
        anno = cls()
        anno.risk_type = risk_type
        # Walk MRO to collect annotations from all parent classes of the detail
        all_attrs: dict[str, Any] = {}  # type: ignore[explicit-any]
        for klass in reversed(type(detail).__mro__):
            all_attrs.update(getattr(klass, "__annotations__", {}))
        for attr_name in all_attrs:
            if attr_name.startswith("_"):
                continue
            val = getattr(detail, attr_name, None)
            setattr(anno, attr_name, val)
        return anno

    def equal_to(self, anno: RiskAnnotation) -> bool:
        """Return True if type, risk_type, and __dict__ match the other annotation.

        Args:
            anno: Other RiskAnnotation to compare.

        Returns:
            True if fully equal.

        """
        if self.type != anno.type:
            return False
        if self.risk_type != anno.risk_type:
            return False
        self_dict = self.__dict__
        anno_dict = anno.__dict__
        return bool(equal(self_dict, anno_dict))


@dataclass
class FindCondition:
    """Condition for matching risk annotations in search."""

    def check(self, anno: RiskAnnotation) -> bool:
        """Return True if the annotation matches this condition.

        Args:
            anno: Annotation to test.

        Returns:
            True if matched.

        Raises:
            NotImplementedError: Subclasses must implement this method.

        """
        raise NotImplementedError


@dataclass
class AnnotationCondition:
    """Composite condition: risk type and optional attribute checks.

    Attributes:
        type: Risk type to match.
        attr_conditions: List of (attr_name, expected_value) to match.
    """

    type: str | RiskType = ""
    attr_conditions: list[tuple[str, YAMLValue]] = field(default_factory=list)

    def risk_type(self, risk_type: str | RiskType) -> AnnotationCondition:
        """Set risk type and return self for chaining.

        Args:
            risk_type: Risk category to match.

        Returns:
            Self for chaining.

        """
        self.type = risk_type
        return self

    def attr(self, key: str, val: YAMLValue) -> AnnotationCondition:
        """Add an attribute condition and return self for chaining.

        Args:
            key: Attribute name.
            val: Expected value.

        Returns:
            Self for chaining.

        """
        self.attr_conditions.append((key, val))
        return self


@dataclass
class AttributeCondition(FindCondition):
    """Match annotation by attribute name and optional value.

    Attributes:
        attr: Attribute name to check.
        result: Expected value (None means "truthy").
    """

    attr: str | None = None
    result: YAMLValue = None

    def check(self, anno: RiskAnnotation) -> bool:
        """Return True if the annotation attribute matches.

        Args:
            anno: Annotation to test.

        Returns:
            True if attr value matches expected result.

        """
        if self.attr and hasattr(anno, self.attr):
            anno_value = getattr(anno, self.attr, None)
            if anno_value == self.result:
                return True
            if self.result is None and isinstance(anno_value, bool) and anno_value:
                return True
        return False


class _RiskAnnotationChecker(Protocol):
    """Protocol for checker callables that take a RiskAnnotation and return bool or None."""

    def __call__(self, anno: RiskAnnotation, **kwargs: YAMLValue) -> bool | None:
        """Check a risk annotation.

        Args:
            anno: Annotation to check.
            **kwargs: Extra arguments for the checker.

        Returns:
            True/False/None result.

        """
        ...


@dataclass
class FunctionCondition(FindCondition):
    """Match annotation by calling a checker function with optional args.

    Attributes:
        func: Callable that takes (anno, **kwargs) and returns bool or None.
        args: Kwargs to pass to func.
        result: Expected return value for a match.
    """

    func: _RiskAnnotationChecker | None = None
    args: YAMLDict | YAMLList | None = None
    result: bool | None = None

    def check(self, anno: RiskAnnotation) -> bool:
        """Return True if func(anno) equals expected result.

        Args:
            anno: Annotation to test.

        Returns:
            True if func returns expected result.

        """
        if self.func is not None and callable(self.func):
            kwargs: YAMLDict = self.args if isinstance(self.args, dict) else {}
            result = self.func(anno, **kwargs)
            if result == self.result:
                return True
        return False


@dataclass
class RiskAnnotationList:
    """List of RiskAnnotation with iteration, after, filter, and find helpers.

    Attributes:
        items: List of RiskAnnotation instances.
    """

    items: list[RiskAnnotation] = field(default_factory=list)

    _i: int = 0

    def __iter__(self) -> RiskAnnotationList:
        """Return iterator over items.

        Returns:
            Self as the iterator.

        """
        return self

    def __next__(self) -> RiskAnnotation:
        """Return next item; raises StopIteration when exhausted.

        Returns:
            Next RiskAnnotation.

        Raises:
            StopIteration: When all items have been yielded.

        """
        if self._i == len(self.items):
            self._i = 0
            raise StopIteration()
        anno = self.items[self._i]
        self._i += 1
        return anno

    def after(self, anno: RiskAnnotation) -> RiskAnnotationList:
        """Return new list containing items after the given annotation.

        Args:
            anno: Annotation to find; items from this one onward are returned.

        Returns:
            New RiskAnnotationList starting from anno.

        """
        return get_annotations_after(self, anno)

    def filter(self, risk_type: str | RiskType = "") -> RiskAnnotationList:
        """Return new list filtered by risk_type (or copy if empty).

        Args:
            risk_type: Risk category to keep; empty keeps all.

        Returns:
            Filtered RiskAnnotationList.

        """
        current = self
        if risk_type:
            current = filter_annotations_by_type(current, risk_type)
        return current

    def find(
        self,
        risk_type: str | RiskType = "",
        condition: FindCondition | list[FindCondition] | None = None,
    ) -> RiskAnnotationList:
        """Return new list of annotations matching risk_type and condition.

        Args:
            risk_type: Risk category to match.
            condition: Single or list of FindConditions.

        Returns:
            Matching RiskAnnotationList.

        """
        return search_risk_annotations(self, risk_type, condition)


def get_annotations_after(anno_list: RiskAnnotationList, anno: RiskAnnotation) -> RiskAnnotationList:
    """Return a new list with all items after the first matching annotation.

    Args:
        anno_list: Source list.
        anno: Annotation to find.

    Returns:
        New RiskAnnotationList from anno onward.

    Raises:
        ValueError: If anno is not found in anno_list.

    """
    sub_list = []
    found = False
    for anno_i in anno_list:
        if anno_i.equal_to(anno):
            found = True
        if found:
            sub_list.append(anno_i)
    if not found:
        raise ValueError(f"Annotation {anno} is not found in the specified AnnotationList")
    return RiskAnnotationList(sub_list)


def filter_annotations_by_type(anno_list: RiskAnnotationList, risk_type: str | RiskType) -> RiskAnnotationList:
    """Return a new list containing only annotations with the given risk_type.

    Args:
        anno_list: Source list.
        risk_type: Risk category to keep.

    Returns:
        Filtered RiskAnnotationList.

    """
    sub_list: list[RiskAnnotation] = []
    for anno_i in anno_list:
        if anno_i.risk_type == risk_type:
            sub_list.append(anno_i)
    return RiskAnnotationList(sub_list)


def search_risk_annotations(
    anno_list: RiskAnnotationList,
    risk_type: str | RiskType = "",
    condition: FindCondition | list[FindCondition] | None = None,
) -> RiskAnnotationList:
    """Return a new list of annotations matching risk_type and condition(s).

    Args:
        anno_list: Source list to search.
        risk_type: Risk category to match; empty matches all.
        condition: Single or list of FindConditions.

    Returns:
        Matching RiskAnnotationList.

    """
    matched = []
    for risk_anno in anno_list:
        if not isinstance(risk_anno, RiskAnnotation):
            continue
        if risk_type and risk_anno.risk_type != risk_type:
            continue
        if condition:
            if isinstance(condition, FindCondition):
                condition = [condition]
            for cond in condition:
                if cond.check(risk_anno):
                    matched.append(risk_anno)
                    break
    return RiskAnnotationList(matched)


class ExecutableType:
    """Executable target type: Module, Role, or TaskFile.

    Attributes:
        MODULE_TYPE: Module executable type.
        ROLE_TYPE: Role executable type.
        TASKFILE_TYPE: Taskfile executable type.
    """

    MODULE_TYPE = "Module"
    ROLE_TYPE = "Role"
    TASKFILE_TYPE = "TaskFile"


@dataclass
class BecomeInfo:
    """Become (privilege escalation) options: enabled, user, method, flags.

    Attributes:
        enabled: Whether become is enabled.
        become: Raw become value.
        user: become_user value.
        method: become_method (e.g. sudo).
        flags: become_flags.
    """

    enabled: bool = False
    become: str = ""
    user: str = ""
    method: str = ""
    flags: str = ""

    @staticmethod
    def from_options(options: YAMLDict) -> BecomeInfo | None:
        """Build BecomeInfo from play/task options dict; None if no become key.

        Args:
            options: Dict of play/task options.

        Returns:
            BecomeInfo instance or None if no become key.

        """
        if "become" in options:
            become = options.get("become", "")
            enabled = False
            with contextlib.suppress(Exception):
                enabled = parse_bool(become)
            user = str(options.get("become_user", ""))
            method = str(options.get("become_method", ""))
            flags = str(options.get("become_flags", ""))
            return BecomeInfo(enabled=enabled, user=user, method=method, flags=flags)
        return None


@dataclass
class Task(Object, Resolvable):
    """Single task: module, options, become, variables, yaml_lines, jsonpath, etc.

    Attributes:
        type: Always "task".
        name: Task name (optional).
        module: Module FQCN or short name.
        index: Task index in the play.
        play_index: Play index.
        defined_in: Path to the file defining this task.
        key: Unique key for lookup.
        local_key: Local key within role/collection.
        role: Role name if in a role.
        collection: Collection name.
        become: Privilege escalation info.
        variables: Variables available to the task.
        module_defaults: Module defaults applied.
        registered_variables: Registered vars from previous tasks.
        set_facts: Set_fact vars from this task.
        loop: Loop config.
        options: Task-level options (when, tags, etc.).
        module_options: Module argument dict.
        executable: Executable name (module/role/taskfile).
        executable_type: One of Module, Role, TaskFile.
        collections_in_play: Collections in scope.
        yaml_lines: Raw YAML fragment for this task.
        line_num_in_file: [begin, end] line numbers.
        jsonpath: Jsonpath to this task in the playbook.
        resolved_name: FQCN or path after resolution.
        possible_candidates: List of (fqcn, defined_in_path) for resolution.
        module_info: Resolved module metadata.
        include_info: Include role/taskfile metadata.
    """

    type: str = "task"
    name: str | None = ""
    module: str = ""
    index: int = -1
    play_index: int = -1
    defined_in: str = ""
    key: str = ""
    local_key: str = ""
    role: str = ""
    collection: str = ""
    become: BecomeInfo | None = None
    variables: YAMLDict = field(default_factory=dict)
    module_defaults: YAMLDict = field(default_factory=dict)
    registered_variables: YAMLDict = field(default_factory=dict)
    set_facts: YAMLDict = field(default_factory=dict)
    loop: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)
    module_options: YAMLDict = field(default_factory=dict)
    executable: str = ""
    executable_type: str = ""
    collections_in_play: list[str] = field(default_factory=list)

    yaml_lines: str = ""
    line_num_in_file: list[int] = field(default_factory=list)  # [begin, end]
    jsonpath: str = ""

    # FQCN for Module and Role. Or a file path for TaskFile.  resolved later
    resolved_name: str = ""
    # candidates of resovled_name — (fqcn, defined_in_path)
    possible_candidates: list[tuple[str, str]] = field(default_factory=list)

    # embed these data when module/role/taskfile are resolved
    module_info: YAMLDict = field(default_factory=dict)
    include_info: YAMLDict = field(default_factory=dict)

    def set_yaml_lines(
        self,
        fullpath: str = "",
        yaml_lines: str = "",
        task_name: str = "",
        module_name: str = "",
        module_options: YAMLValue | None = None,
        task_options: YAMLValue | None = None,
        previous_task_line: int = -1,
        jsonpath: str = "",
    ) -> None:
        """Set yaml_lines and line_num_in_file from file or jsonpath/task match.

        Args:
            fullpath: Path to the file containing the task.
            yaml_lines: Raw YAML string (used if fullpath not provided).
            task_name: Task name to match in the YAML.
            module_name: Module FQCN or short name to match.
            module_options: Module args dict or string to match.
            task_options: Task-level options for reconstruction.
            previous_task_line: Skip lines before this line number (1-based).
            jsonpath: Jsonpath to locate the task block directly.
        """
        if not task_name and not module_options:
            return

        lines: list[str] = []
        lines = yaml_lines.splitlines() if yaml_lines else Path(fullpath).read_text().splitlines()

        if jsonpath:
            found_yaml, line_num = identify_lines_with_jsonpath(fpath=fullpath, yaml_str=yaml_lines, jsonpath=jsonpath)
            if found_yaml and line_num:
                self.yaml_lines = found_yaml
                self.line_num_in_file = list(line_num)
                return

        # search candidates that match either of the following conditions
        #   - task name is included in the line
        #   - if module name is included,
        #       - if module option is string, it is included
        #       - if module option is dict, at least one key is included
        candidate_line_nums = []
        for i, line in enumerate(lines):
            # skip lines until `previous_task_line` if provided
            if previous_task_line > 0 and i <= previous_task_line - 1:
                continue

            if task_name:
                if task_name in line:
                    candidate_line_nums.append(i)
            elif f"{module_name}:" in line:
                if isinstance(module_options, str):
                    if module_options in line:
                        candidate_line_nums.append(i)
                elif isinstance(module_options, dict):
                    option_matched = False
                    for key in module_options:
                        if i + 1 < len(lines) and f"{key}:" in lines[i + 1]:
                            option_matched = True
                            break
                    if option_matched:
                        candidate_line_nums.append(i)
        if not candidate_line_nums:
            return

        # get task yaml_lines for each candidate
        candidate_blocks = []
        for candidate_line_num in candidate_line_nums:
            _yaml_lines, _line_num_in_file = self._find_task_block(lines, candidate_line_num)
            if _yaml_lines and _line_num_in_file:
                candidate_blocks.append((_yaml_lines, _line_num_in_file))

        if not candidate_blocks:
            return

        reconstructed_yaml = ""
        best_yaml_lines = ""
        best_line_num_in_file = []
        sorted_candidates = []
        if len(candidate_blocks) == 1:
            best_yaml_lines = candidate_blocks[0][0]
            best_line_num_in_file = candidate_blocks[0][1]
        else:
            # reconstruct yaml from the task data to calculate similarity (edit distance) later
            reconstructed_data: list[YAMLDict] = [{}]
            if task_name:
                reconstructed_data[0]["name"] = task_name
            reconstructed_data[0][module_name] = module_options
            if isinstance(task_options, dict):
                for key, val in task_options.items():
                    if key not in reconstructed_data[0]:
                        reconstructed_data[0][key] = val

            with contextlib.suppress(Exception):
                reconstructed_yaml = ariyaml.dump(cast(YAMLValue, reconstructed_data))

            # find best match by edit distance
            if reconstructed_yaml:

                def remove_comment_lines(s: str) -> str:
                    """Strip lines starting with # from a YAML string.

                    Args:
                        s: Multi-line string.

                    Returns:
                        String with comment lines removed.

                    """
                    lines = s.splitlines()
                    updated = []
                    for line in lines:
                        if line.strip().startswith("#"):
                            continue
                        updated.append(line)
                    return "\n".join(updated)

                def calc_dist(s1: str, s2: str) -> int:
                    """Compute Levenshtein distance between two YAML strings (comments stripped).

                    Args:
                        s1: First YAML string.
                        s2: Second YAML string.

                    Returns:
                        Integer edit distance.

                    """
                    us1 = remove_comment_lines(s1)
                    us2 = remove_comment_lines(s2)
                    dist = int(Levenshtein.distance(us1, us2))
                    return dist

                r = reconstructed_yaml
                sorted_candidates = sorted(candidate_blocks, key=lambda x: calc_dist(r, x[0]))
                best_yaml_lines = sorted_candidates[0][0]
                best_line_num_in_file = sorted_candidates[0][1]
            else:
                # give up here if yaml reconstruction failed
                # use the first candidate
                best_yaml_lines = candidate_blocks[0][0]
                best_line_num_in_file = candidate_blocks[0][1]

        self.yaml_lines = best_yaml_lines
        self.line_num_in_file = best_line_num_in_file
        return

    def _find_task_block(self, yaml_lines: list[str], start_line_num: int) -> tuple[str | None, list[int] | None]:
        """Extract the task block (YAML fragment and [begin, end] line numbers) starting at start_line_num.

        Args:
            yaml_lines: List of YAML source lines.
            start_line_num: Zero-based line index where the task starts.

        Returns:
            Tuple of (YAML fragment string, [begin, end] line numbers) or (None, None).
        """
        if not yaml_lines:
            return None, None

        if start_line_num < 0:
            return None, None

        lines = yaml_lines
        found_line = lines[start_line_num]
        is_top_of_block = found_line.replace(" ", "").startswith("-")
        begin_line_num = start_line_num
        indent_of_block = -1
        if is_top_of_block:
            indent_of_block = len(found_line.split("-")[0])
        else:
            found = False
            found_line = ""
            _indent_of_block = -1
            parts = found_line.split(" ")
            for i, p in enumerate(parts):
                if p != "":
                    break
                _indent_of_block = i + 1
            for _ in range(len(lines)):
                index = begin_line_num
                _line = lines[index]
                is_top_of_block = _line.replace(" ", "").startswith("-")
                if is_top_of_block:
                    _indent = len(_line.split("-")[0])
                    if _indent < _indent_of_block:
                        found = True
                        found_line = _line
                        break
                begin_line_num -= 1
                if begin_line_num < 0:
                    break
            if not found:
                return None, None
            indent_of_block = len(found_line.split("-")[0])
        index = begin_line_num + 1
        end_found = False
        end_line_num = -1
        for _ in range(len(lines)):
            if index >= len(lines):
                break
            _line = lines[index]
            is_top_of_block = _line.replace(" ", "").startswith("-")
            is_when_at_same_indent = _line.replace(" ", "").startswith("when")
            if is_top_of_block or is_when_at_same_indent:
                if is_top_of_block:
                    _indent = len(_line.split("-")[0])
                elif is_when_at_same_indent:
                    _indent = len(_line.split("when")[0])
                if _indent <= indent_of_block:
                    end_found = True
                    end_line_num = index - 1
                    break
            else:
                _indent = len(_line) - len(_line.lstrip())
                if _indent <= indent_of_block:
                    end_found = True
                    end_line_num = index - 1
                    break
            index += 1
            if index >= len(lines):
                end_found = True
                end_line_num = index
                break

        if not end_found:
            return None, None
        if begin_line_num < 0 or end_line_num > len(lines) or begin_line_num > end_line_num:
            return None, None

        result_yaml = "\n".join(lines[begin_line_num : end_line_num + 1])
        line_num_in_file = [begin_line_num + 1, end_line_num + 1]
        return result_yaml, line_num_in_file

    def yaml(self, original_module: str = "", use_yaml_lines: bool = True) -> str:
        """Return task as YAML string; preserves comments/indent when use_yaml_lines and parseable.

        Args:
            original_module: Original module key when renaming module.
            use_yaml_lines: If True, use yaml_lines when parseable; else build from spec.

        Returns:
            Task as YAML string.
        """
        task_data_wrapper: list[YAMLDict] | None = None
        task_data: YAMLDict | None = None
        if use_yaml_lines:
            try:
                loaded: object = ariyaml.load(self.yaml_lines)
                task_data_wrapper = cast(list[YAMLDict], loaded) if loaded else None
                task_data = task_data_wrapper[0] if task_data_wrapper else None
            except Exception:
                pass

            if not task_data:
                return self.yaml_lines
        else:
            task_data_wrapper = []
            task_data = {}

        is_local_action = "local_action" in self.options

        # task name
        if self.name:
            task_data["name"] = self.name
        elif "name" in task_data:
            task_data.pop("name")

        if not is_local_action:
            # module name
            if original_module:
                mo = deepcopy(task_data[original_module])
                task_data[self.module] = mo
            elif self.module and self.module not in task_data:
                task_data[self.module] = self.module_options

            # module options
            if isinstance(self.module_options, dict):
                current_mo = task_data[self.module]
                # if the module options was an old style inline parameter in YAML,
                # we can ignore them here because it is parsed as self.module_options
                if not isinstance(current_mo, dict):
                    current_mo = {}
                old_keys = list(current_mo.keys())
                new_keys = list(self.module_options.keys())
                for old_key in old_keys:
                    if old_key not in new_keys:
                        current_mo.pop(old_key)
                recursive_copy_dict(self.module_options, current_mo)
                task_data[self.module] = current_mo

        # task options
        if isinstance(self.options, dict):
            current_to = task_data
            old_keys = list(current_to.keys())
            new_keys = list(self.options.keys())
            for old_key in old_keys:
                if old_key in ["name", self.module]:
                    continue
                if old_key not in new_keys:
                    current_to.pop(old_key)
            options_without_name = {k: v for k, v in self.options.items() if k != "name"}
            if is_local_action:
                new_la_opt: YAMLDict = {}
                new_la_opt["module"] = self.module
                recursive_copy_dict(self.module_options, new_la_opt)
                options_without_name["local_action"] = new_la_opt
                recursive_copy_dict(options_without_name, current_to)
        wrapper = task_data_wrapper if task_data_wrapper is not None else []
        if len(wrapper) == 0:
            wrapper.append(current_to)
        else:
            wrapper[0] = current_to
        new_yaml = str(ariyaml.dump(cast(YAMLValue, wrapper)))
        return new_yaml

    def formatted_yaml(self) -> str:
        """Build YAML from task spec (name, module, options); loses original comments/indent.

        Returns:
            Task as formatted YAML string.
        """
        task_data: YAMLDict = {}
        if self.name:
            task_data["name"] = self.name
        if self.module:
            task_data[self.module] = self.module_options
        for key, val in self.options.items():
            if key == "name":
                continue
            task_data[key] = val
        task_data = cast(YAMLDict, self.str2double_quoted_scalar(task_data))
        data = [task_data]
        return str(ariyaml.dump(cast(YAMLValue, data)))

    def str2double_quoted_scalar(self, v: YAMLValue) -> YAMLValue:
        """Recursively wrap string values in DoubleQuotedScalarString for ruamel output.

        Args:
            v: Dict, list, or string value to process.

        Returns:
            Same structure with string values wrapped in DoubleQuotedScalarString.
        """
        if isinstance(v, dict):
            for key, val in v.items():
                new_val = self.str2double_quoted_scalar(val)
                v[key] = new_val
        elif isinstance(v, list):
            for i, val in enumerate(v):
                new_val = self.str2double_quoted_scalar(val)
                v[i] = new_val
        elif isinstance(v, str):
            v = DoubleQuotedScalarString(v)
        else:
            pass
        return v

    def set_key(self, parent_key: str = "", parent_local_key: str = "") -> None:
        """Set key from task identity and parent via keyutil.

        Args:
            parent_key: Key of the parent object.
            parent_local_key: Local key of the parent within role/collection.
        """
        set_task_key(self, parent_key, parent_local_key)

    def children_to_key(self) -> Task:
        """Return self (Task has no keyed children).

        Returns:
            Self.
        """
        return self

    @property
    def defined_vars(self) -> YAMLDict:
        """Merge variables, registered_variables, and set_facts.

        Returns:
            Merged dict of variables available to the task.
        """
        d_vars = self.variables
        d_vars.update(self.registered_variables)
        d_vars.update(self.set_facts)
        return d_vars

    @property
    def tags(self) -> YAMLValue:
        """Return tags from options.

        Returns:
            Tags value or None.
        """
        return self.options.get("tags", None)

    @property
    def when(self) -> YAMLValue:
        """Return when from options.

        Returns:
            When condition value or None.
        """
        return self.options.get("when", None)

    @property
    def action(self) -> str:
        """Return executable (module/role/taskfile name).

        Returns:
            Executable name.
        """
        return self.executable

    @property
    def resolved_action(self) -> str:
        """Return resolved_name (FQCN or path after resolution).

        Returns:
            Resolved FQCN or path.
        """
        return self.resolved_name

    @property
    def line_number(self) -> list[int]:
        """Return [begin, end] line numbers in file.

        Returns:
            List of [begin, end] line numbers (1-based).
        """
        return self.line_num_in_file

    @property
    def id(self) -> str:
        """Return stable id from defined_in, index, play_index.

        Returns:
            JSON string with path, index, play_index.
        """
        return json.dumps(
            {
                "path": self.defined_in,
                "index": self.index,
                "play_index": self.play_index,
            }
        )

    @property
    def resolver_targets(self) -> None:
        """No child targets to resolve."""
        return None


@dataclass
class MutableContent:
    """Mutable task YAML wrapper: edit task spec and regenerate YAML."""

    _yaml: str = ""
    _task_spec: Task | None = None

    def _require_task_spec(self) -> Task:
        """Return _task_spec or raise if missing.

        Returns:
            The task spec.

        Raises:
            ValueError: If _task_spec is None.
        """
        if self._task_spec is None:
            raise ValueError("MutableContent has no task spec")
        return self._task_spec

    @staticmethod
    def from_task_spec(task_spec: Task) -> MutableContent:
        """Build MutableContent from a Task (copy of spec, yaml_lines as _yaml).

        Args:
            task_spec: Task to wrap for editing.

        Returns:
            MutableContent instance.
        """
        mc = MutableContent(
            _yaml=task_spec.yaml_lines,
            _task_spec=deepcopy(task_spec),
        )
        return mc

    def set_task_name(self, task_name: str) -> MutableContent:
        """Set task name and refresh _yaml; return self for chaining.

        Args:
            task_name: New task name.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        spec.name = task_name
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def get_task_name(self) -> str | None:
        """Return current task name or None.

        Returns:
            Task name or None.
        """
        return self._task_spec.name if self._task_spec else None

    def omit_task_name(self) -> MutableContent:
        """Clear task name and refresh _yaml; return self for chaining.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        spec.name = None
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def set_module_name(self, module_name: str) -> MutableContent:
        """Set module (FQCN) and refresh _yaml; return self for chaining.

        Args:
            module_name: New module FQCN.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        original_module = deepcopy(spec.module)
        spec.module = module_name
        self._yaml = spec.yaml(original_module=original_module)
        spec.yaml_lines = self._yaml
        return self

    def replace_key(self, old_key: str, new_key: str) -> MutableContent:
        """Replace a task option key and refresh _yaml.

        Args:
            old_key: Existing option key.
            new_key: New option key.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        if old_key in spec.options:
            value = spec.options[old_key]
            spec.options.pop(old_key)
            spec.options[new_key] = value
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def replace_value(self, old_value: str, new_value: str) -> MutableContent:
        """Replace a task option value and refresh _yaml.

        Args:
            old_value: Value to match.
            new_value: Replacement value.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        original_new_value = deepcopy(new_value)
        need_restore = False
        keys_to_be_restored = []
        if isinstance(new_value, str):
            new_value = DoubleQuotedScalarString(new_value)
            need_restore = True
        for k, v in spec.options.items():
            if type(v).__name__ != type(old_value).__name__:
                continue
            if v != old_value:
                continue
            spec.options[k] = new_value
            keys_to_be_restored.append(k)
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        if need_restore:
            for k, _ in spec.options.items():
                if k in keys_to_be_restored:
                    spec.options[k] = original_new_value
        return self

    def remove_key(self, key: str) -> MutableContent:
        """Remove a task option key and refresh _yaml.

        Args:
            key: Option key to remove.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        if key in spec.options:
            spec.options.pop(key)
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def set_new_module_arg_key(self, key: str, value: YAMLValue) -> MutableContent:
        """Add or set a module argument and refresh _yaml.

        Args:
            key: Module argument key.
            value: Module argument value.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        original_value = deepcopy(value)
        need_restore = False
        if isinstance(value, str):
            value = DoubleQuotedScalarString(value)
            need_restore = True
        spec.module_options[key] = value
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        if need_restore:
            spec.module_options[key] = original_value
        return self

    def remove_module_arg_key(self, key: str) -> MutableContent:
        """Remove a module argument and refresh _yaml.

        Args:
            key: Module argument key to remove.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        if key in spec.module_options:
            spec.module_options.pop(key)
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def replace_module_arg_key(self, old_key: str, new_key: str) -> MutableContent:
        """Replace a module argument key and refresh _yaml.

        Args:
            old_key: Existing module argument key.
            new_key: New module argument key.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        if old_key in spec.module_options:
            value = spec.module_options[old_key]
            spec.module_options.pop(old_key)
            spec.module_options[new_key] = value
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        return self

    def replace_module_arg_value(
        self, key: str = "", old_value: YAMLValue = None, new_value: YAMLValue = None
    ) -> MutableContent:
        """Replace a module argument value and refresh _yaml.

        Args:
            key: Module argument key (optional; if empty, match all keys).
            old_value: Value to match.
            new_value: Replacement value.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        original_new_value = deepcopy(new_value)
        need_restore = False
        keys_to_be_restored = []
        if isinstance(new_value, str):
            new_value = DoubleQuotedScalarString(new_value)
            need_restore = True
        for k in spec.module_options:
            # if `key` is specified, skip other keys
            if key and k != key:
                continue
            value = spec.module_options[k]
            if type(value).__name__ == type(old_value).__name__ and value == old_value:
                spec.module_options[k] = new_value
                keys_to_be_restored.append(k)
        self._yaml = spec.yaml()
        spec.yaml_lines = self._yaml
        if need_restore:
            for k in spec.module_options:
                if k in keys_to_be_restored:
                    spec.module_options[k] = original_new_value
        return self

    def replace_with_dict(self, new_dict: YAMLDict) -> MutableContent:
        """Replace the entire task spec with a new dict and refresh _yaml.

        Args:
            new_dict: New task block dict.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        from .model_loader import load_task

        yaml_lines = ariyaml.dump([new_dict])
        new_task = load_task(
            path=spec.defined_in,
            index=spec.index,
            task_block_dict=cast(dict[str, object], new_dict),
            task_jsonpath=spec.jsonpath,
            role_name=spec.role,
            collection_name=spec.collection,
            collections_in_play=spec.collections_in_play,
            play_index=spec.play_index,
            yaml_lines=yaml_lines,
        )
        self._yaml = yaml_lines
        self._task_spec = new_task
        return self

    def replace_module_arg_with_dict(self, new_dict: YAMLDict) -> MutableContent:
        """Replace the entire module arguments dict and refresh _yaml.

        Args:
            new_dict: New module arguments dict.

        Returns:
            Self for chaining.
        """
        spec = self._require_task_spec()
        spec.module_options = new_dict
        self._yaml = spec.yaml()
        return self

    # this keeps original contents like comments, indentation
    # and quotes for string as much as possible
    def yaml(self) -> str:
        """Return current YAML string (preserves comments, indentation, quotes).

        Returns:
            Current YAML string.
        """
        return self._yaml

    # this makes a yaml from task contents such as spec.module,
    # spec.options, spec.module_options in a fixed format
    # NOTE: this will lose comments and indentations in the original YAML
    def formatted_yaml(self) -> str:
        """Return formatted YAML from task spec (may lose comments/indentation).

        Returns:
            Formatted YAML string from task spec.
        """
        return self._require_task_spec().formatted_yaml()


@dataclass
class TaskCall(CallObject, RunTarget):
    """Call target for a single task with spec and annotations.

    Attributes:
        type: Always "taskcall".
        annotations: List of annotations (e.g. from annotators).
        args: Task arguments.
        variable_set: Variables set by this task.
        variable_use: Variables used by this task.
        become: Privilege escalation info.
        module_defaults: Module defaults applied.
        module: Resolved Module when applicable.
        content: MutableContent for editing task YAML.
    """

    type: str = "taskcall"
    # annotations are used for storing generic analysis data
    # any Annotators in "annotators" dir can add them to this object
    annotations: list[Annotation] = field(default_factory=list)
    args: Arguments = field(default_factory=Arguments)
    variable_set: YAMLDict = field(default_factory=dict)
    variable_use: YAMLDict = field(default_factory=dict)
    become: BecomeInfo | None = None
    module_defaults: YAMLDict = field(default_factory=dict)

    module: Module | None = None
    content: MutableContent | None = None

    def get_annotation_by_type(self, type_str: str = "") -> list[Annotation]:
        """Return annotations matching the given type.

        Args:
            type_str: Annotation type to match.

        Returns:
            List of matching annotations.
        """
        matched = [an for an in self.annotations if an.type == type_str]
        return matched

    def get_annotation_by_type_and_attr(
        self, type_str: str = "", key: str = "", val: YAMLValue = None
    ) -> list[Annotation]:
        """Return annotations matching type and attribute value.

        Args:
            type_str: Annotation type to match.
            key: Attribute name to check.
            val: Attribute value to match.

        Returns:
            List of matching annotations.
        """
        matched = [
            an
            for an in self.annotations
            if hasattr(an, "type") and an.type == type_str and getattr(an, key, None) == val
        ]
        return matched

    def set_annotation(self, key: str, value: YAMLValue, rule_id: str) -> None:
        """Set or add annotation by key.

        Args:
            key: Annotation key.
            value: Annotation value.
            rule_id: Rule ID that set this annotation.
        """
        end_to_set = False
        for an in self.annotations:
            if not hasattr(an, "key"):
                continue
            if an.key == key:
                an.value = value
                end_to_set = True
                break
        if not end_to_set:
            self.annotations.append(Annotation(key=key, value=value, rule_id=rule_id))
        return

    def get_annotation(self, key: str, __default: YAMLValue = None, rule_id: str = "") -> YAMLValue:
        """Return annotation value by key, optionally filtered by rule_id.

        Args:
            key: Annotation key.
            __default: Default if not found.
            rule_id: Optional rule ID filter.

        Returns:
            Annotation value or default.
        """
        value = __default
        for an in self.annotations:
            if not hasattr(an, "key"):
                continue
            if rule_id and hasattr(an, "rule_id") and an.rule_id != rule_id:
                continue
            if an.key == key:
                value = getattr(an, "value", __default)
                break
        return value

    def has_annotation_by_condition(self, cond: AnnotationCondition) -> bool:
        """Return whether any annotation matches the condition.

        Args:
            cond: Condition to match.

        Returns:
            True if any annotation matches.
        """
        anno = self.get_annotation_by_condition(cond)
        return bool(anno)

    def get_annotation_by_condition(self, cond: AnnotationCondition) -> Annotation | RiskAnnotation | None:
        """Return first annotation matching the condition.

        Args:
            cond: Condition to match.

        Returns:
            First matching annotation or None.
        """
        _annotations: list[Annotation] = list(self.annotations)
        if cond.type:
            _annotations = [an for an in _annotations if isinstance(an, RiskAnnotation) and an.risk_type == cond.type]
        if cond.attr_conditions:
            for key, val in cond.attr_conditions:
                _annotations = [an for an in _annotations if hasattr(an, key) and getattr(an, key) == val]
        if _annotations:
            return _annotations[0]
        return None

    def file_info(self) -> tuple[str, str]:
        """Return (defined_in path, line info) for this task.

        Returns:
            Tuple of (file path, line info string).
        """
        file = self.spec.defined_in  # type: ignore[attr-defined]
        lines = "?"
        if len(self.spec.line_number) == 2:  # type: ignore[attr-defined]
            l_num = self.spec.line_number  # type: ignore[attr-defined]
            lines = f"L{l_num[0]}-{l_num[1]}"
        return file, lines

    @property
    def resolved_name(self) -> str:
        """Return resolved module/action name from spec."""
        return getattr(self.spec, "resolved_name", "") if self.spec else ""

    @property
    def resolved_action(self) -> str:
        """Return resolved action (alias for resolved_name)."""
        return self.resolved_name

    @property
    def action_type(self) -> str:
        """Return executable type (Module, Role, TaskFile) from spec."""
        return getattr(self.spec, "executable_type", "") if self.spec else ""


@dataclass
class AnsibleRunContext:
    """Ordered sequence of run targets for rule evaluation.

    Attributes:
        sequence: Ordered list of RunTarget.
        root_key: Key of the root target.
        parent: Parent Object when built from a tree.
        ram_client: Optional RAM client for lookups.
        scan_metadata: Metadata for the scan.
        current: Current RunTarget during iteration.
        last_item: Whether this is the last item in a loop.
        vars: Variables context (optional).
        host_info: Host info (optional).
    """

    sequence: RunTargetList = field(default_factory=RunTargetList)
    root_key: str = ""
    parent: Object | None = None
    ram_client: RAMClient | None = None
    scan_metadata: YAMLDict = field(default_factory=dict)

    # used by rule check
    current: RunTarget | None = None
    _i: int = 0

    # used if ram generate / other data generation by loop
    last_item: bool = False

    # TODO: implement the following attributes
    vars: YAMLDict | None = None
    host_info: YAMLDict | None = None

    def __len__(self) -> int:
        """Return number of run targets in the sequence.

        Returns:
            Length of sequence.
        """
        return len(self.sequence)

    def __iter__(self) -> AnsibleRunContext:
        """Return self as iterator.

        Returns:
            Self as the iterator.
        """
        return self

    def __next__(self) -> RunTarget:
        """Return next run target; raise StopIteration when done.

        Returns:
            Next RunTarget.

        Raises:
            StopIteration: When iteration is complete.
        """
        if self._i == len(self.sequence):
            self._i = 0
            self.current = None
            raise StopIteration()
        t = self.sequence[self._i]
        self.current = t
        self._i += 1
        return t

    def __getitem__(self, i: int) -> RunTarget:
        """Return run target at index.

        Args:
            i: Index.

        Returns:
            RunTarget at index.
        """
        return self.sequence[i]

    @staticmethod
    def from_tree(
        tree: ObjectList,
        parent: Object | None = None,
        last_item: bool = False,
        ram_client: RAMClient | None = None,
        scan_metadata: YAMLDict | None = None,
    ) -> AnsibleRunContext:
        """Build context from an ObjectList of RunTarget items.

        Args:
            tree: ObjectList containing RunTarget items.
            parent: Parent Object when built from tree.
            last_item: Whether this is the last item in a loop.
            ram_client: Optional RAM client for lookups.
            scan_metadata: Metadata for the scan.

        Returns:
            AnsibleRunContext instance.
        """
        if not tree:
            return AnsibleRunContext(parent=parent, last_item=last_item, scan_metadata=scan_metadata or {})
        if len(tree.items) == 0:
            return AnsibleRunContext(parent=parent, last_item=last_item, scan_metadata=scan_metadata or {})
        scan_metadata = scan_metadata or {}
        first_item = tree.items[0]
        spec = getattr(first_item, "spec", None)
        root_key = getattr(spec, "key", getattr(first_item, "key", "")) if spec else getattr(first_item, "key", "")
        sequence_items: list[RunTarget] = []
        for item in tree.items:
            if isinstance(item, RunTarget):
                sequence_items.append(cast(RunTarget, item))
        tl = RunTargetList(items=sequence_items)
        return AnsibleRunContext(
            sequence=tl,
            root_key=root_key,
            parent=parent,
            last_item=last_item,
            ram_client=ram_client,
            scan_metadata=scan_metadata,
        )

    @staticmethod
    def from_targets(
        targets: list[RunTarget],
        root_key: str = "",
        parent: Object | None = None,
        last_item: bool = False,
        ram_client: RAMClient | None = None,
        scan_metadata: YAMLDict | None = None,
    ) -> AnsibleRunContext:
        """Build context from a list of RunTarget items.

        Args:
            targets: List of RunTarget.
            root_key: Key of the root target.
            parent: Parent Object.
            last_item: Whether this is the last item in a loop.
            ram_client: Optional RAM client for lookups.
            scan_metadata: Metadata for the scan.

        Returns:
            AnsibleRunContext instance.
        """
        if not root_key and len(targets) > 0:
            root_key = (
                getattr(targets[0].spec, "key", "") if hasattr(targets[0], "spec") else getattr(targets[0], "key", "")
            )
        scan_metadata = scan_metadata or {}
        tl = RunTargetList(items=targets)
        return AnsibleRunContext(
            sequence=tl,
            root_key=root_key,
            parent=parent,
            last_item=last_item,
            ram_client=ram_client,
            scan_metadata=scan_metadata,
        )

    def find(self, target: RunTarget) -> RunTarget | None:
        """Find run target by key.

        Args:
            target: RunTarget whose key to match.

        Returns:
            Matching RunTarget or None.
        """
        for t in self.sequence:
            if t.key == target.key:
                return t
        return None

    def before(self, target: RunTarget) -> AnsibleRunContext:
        """Return context of run targets before the given target.

        Args:
            target: RunTarget to stop before.

        Returns:
            New AnsibleRunContext with targets before target.
        """
        targets = []
        for rt in self.sequence:
            if rt.key == target.key:
                break
            targets.append(rt)
        return AnsibleRunContext.from_targets(
            targets,
            root_key=self.root_key,
            parent=self.parent,
            last_item=self.last_item,
            ram_client=self.ram_client,
            scan_metadata=self.scan_metadata,
        )

    def search(self, cond: AnnotationCondition) -> AnsibleRunContext:
        """Return context of task targets matching the annotation condition.

        Args:
            cond: Annotation condition to match.

        Returns:
            New AnsibleRunContext with matching task targets.
        """
        targets = [t for t in self.sequence if t.type == RunTargetType.Task and t.has_annotation_by_condition(cond)]
        return AnsibleRunContext.from_targets(
            targets,
            root_key=self.root_key,
            parent=self.parent,
            last_item=self.last_item,
            ram_client=self.ram_client,
            scan_metadata=self.scan_metadata,
        )

    def is_end(self, target: RunTarget) -> bool:
        """Return whether target is the last item in the sequence.

        Args:
            target: RunTarget to check.

        Returns:
            True if target is the last item.
        """
        if len(self) == 0:
            return False
        return target.key == self.sequence[-1].key

    def is_last_task(self, target: RunTarget) -> bool:
        """Return whether target is the last task in the sequence.

        Args:
            target: RunTarget to check.

        Returns:
            True if target is the last task.
        """
        if len(self) == 0:
            return False
        taskcalls = self.taskcalls
        if len(taskcalls) == 0:
            return False
        return target.key == taskcalls[-1].key

    def is_begin(self, target: RunTarget) -> bool:
        """Return whether target is the first item in the sequence.

        Args:
            target: RunTarget to check.

        Returns:
            True if target is the first item.
        """
        if len(self) == 0:
            return False
        return target.key == self.sequence[0].key

    def copy(self) -> AnsibleRunContext:
        """Return a shallow copy of this context.

        Returns:
            New AnsibleRunContext with same sequence and metadata.
        """
        return AnsibleRunContext.from_targets(
            targets=self.sequence.items,
            root_key=self.root_key,
            parent=self.parent,
            last_item=self.last_item,
            ram_client=self.ram_client,
            scan_metadata=self.scan_metadata,
        )

    @property
    def info(self) -> YAMLDict:
        """Return object info by root_key.

        Returns:
            Dict with object info or empty dict.
        """
        if not self.root_key:
            return {}
        info = cast(YAMLDict, dict(get_obj_info_by_key(self.root_key)))
        return info

    @property
    def taskcalls(self) -> list[RunTarget]:
        """Return run targets that are tasks.

        Returns:
            List of task RunTarget items.
        """
        return [t for t in self.sequence if t.type == RunTargetType.Task]

    @property
    def tasks(self) -> list[RunTarget]:
        """Return taskcalls (alias).

        Returns:
            List of task RunTarget items.
        """
        return self.taskcalls

    @property
    def annotations(self) -> RiskAnnotationList:
        """Return all RiskAnnotations from task targets in the sequence.

        Returns:
            RiskAnnotationList of annotations.
        """
        anno_list: list[RiskAnnotation] = []
        for tc in self.taskcalls:
            for a in tc.annotations:
                if isinstance(a, RiskAnnotation):
                    anno_list.append(a)
        return RiskAnnotationList(anno_list)


@dataclass
class TaskFile(Object, Resolvable):
    """Task file (tasks/main.yml, etc.) with tasks and metadata.

    Attributes:
        type: Always "taskfile".
        name: Task file name.
        defined_in: Path to the file.
        key: Unique key for lookup.
        local_key: Local key within role/collection.
        tasks: List of Task or task keys.
        role: Role name if in a role.
        collection: Collection name.
        yaml_lines: Raw YAML content.
        used_in: Paths where this task file is used.
        annotations: Annotation dict.
        variables: Variables available.
        module_defaults: Module defaults.
        options: Task file options.
        task_loading: Task loading metadata.
    """

    type: str = "taskfile"
    name: str = ""
    defined_in: str = ""
    key: str = ""
    local_key: str = ""
    tasks: list[Task | str] = field(default_factory=list)
    # role name of this task file
    # this might be empty because a task file can be defined out of roles
    role: str = ""
    collection: str = ""

    yaml_lines: str = ""

    used_in: list[str] = field(default_factory=list)  # resolved later

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    variables: YAMLDict = field(default_factory=dict)
    module_defaults: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)

    task_loading: YAMLDict = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from task file identity via keyutil."""
        set_taskfile_key(self)

    def children_to_key(self) -> TaskFile:
        """Sort tasks by key and return self.

        Returns:
            Self.
        """
        task_keys = [t.key if isinstance(t, Task) else t for t in self.tasks]
        self.tasks = cast(list["Task | str"], sorted(task_keys))
        return self

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Return list of tasks as resolver targets.

        Returns:
            List of tasks.
        """
        return list(self.tasks)


@dataclass
class TaskFileCall(CallObject, RunTarget):
    """Call target for a task file.

    Attributes:
        type: Always "taskfilecall".
    """

    type: str = "taskfilecall"


@dataclass
class Role(Object, Resolvable):
    """Ansible role with playbooks, task files, handlers, modules.

    Attributes:
        type: Always "role".
        name: Role name.
        defined_in: Path to the role.
        key: Unique key for lookup.
        local_key: Local key within collection.
        fqcn: Fully qualified collection name.
        metadata: Role metadata.
        collection: Collection name.
        playbooks: List of Playbook or keys.
        taskfiles: List of TaskFile or keys.
        handlers: List of Task handlers.
        modules: List of Module or keys.
        dependency: Role dependencies.
        requirements: Requirements metadata.
        source: Collection/scm/galaxy source.
        annotations: Annotation dict.
        default_variables: Default variables.
        variables: Variables.
        loop: Loop config.
        options: Role options.
    """

    type: str = "role"
    name: str = ""
    defined_in: str = ""
    key: str = ""
    local_key: str = ""
    fqcn: str = ""
    metadata: YAMLDict = field(default_factory=dict)
    collection: str = ""
    playbooks: list[Playbook | str] = field(default_factory=list)
    # 1 role can have multiple task yamls
    taskfiles: list[TaskFile | str] = field(default_factory=list)
    handlers: list[Task] = field(default_factory=list)
    # roles/xxxx/library/zzzz.py can be called as module zzzz
    modules: list[Module | str] = field(default_factory=list)
    dependency: YAMLDict = field(default_factory=dict)
    requirements: YAMLDict = field(default_factory=dict)

    source: str = ""  # collection/scm repo/galaxy

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    default_variables: YAMLDict = field(default_factory=dict)
    variables: YAMLDict = field(default_factory=dict)
    # key: loop_var (default "item"), value: list/dict of item value
    loop: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from role identity via keyutil."""
        set_role_key(self)

    def children_to_key(self) -> Role:
        """Sort modules, playbooks, taskfiles by key and return self.

        Returns:
            Self.
        """
        module_keys = [m.key if isinstance(m, Module) else m for m in self.modules]
        self.modules = cast(list["Module | str"], sorted(module_keys))

        playbook_keys = [p.key if isinstance(p, Playbook) else p for p in self.playbooks]
        self.playbooks = cast(list["Playbook | str"], sorted(playbook_keys))

        taskfile_keys = [tf.key if isinstance(tf, TaskFile) else tf for tf in self.taskfiles]
        self.taskfiles = cast(list["TaskFile | str"], sorted(taskfile_keys))
        return self

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Return taskfiles and modules as resolver targets.

        Returns:
            List of taskfiles and modules.
        """
        return cast(list["Resolvable | str"], list(self.taskfiles) + list(self.modules))


@dataclass
class RoleCall(CallObject, RunTarget):
    """Call target for a role.

    Attributes:
        type: Always "rolecall".
    """

    type: str = "rolecall"


@dataclass
class RoleInPlay(Object, Resolvable):
    """Role reference within a play (roles: block).

    Attributes:
        type: Always "roleinplay".
        name: Role name.
        options: Role options.
        defined_in: Path to the playbook.
        role_index: Index in the roles list.
        play_index: Play index.
        role: Role name.
        collection: Collection name.
        resolved_name: Resolved FQCN.
        possible_candidates: Resolution candidates.
        annotations: Annotation dict.
        collections_in_play: Collections in scope.
        role_info: Resolved role metadata.
    """

    type: str = "roleinplay"
    name: str = ""
    options: YAMLDict = field(default_factory=dict)
    defined_in: str = ""
    role_index: int = -1
    play_index: int = -1

    role: str = ""
    collection: str = ""

    resolved_name: str = ""  # resolved later
    # candidates of resovled_name — (fqcn, defined_in_path)
    possible_candidates: list[tuple[str, str]] = field(default_factory=list)

    annotations: dict[str, YAMLValue] = field(default_factory=dict)
    collections_in_play: list[str] = field(default_factory=list)

    # embed this data when role is resolved
    role_info: YAMLDict = field(default_factory=dict)

    @property
    def resolver_targets(self) -> None:
        """No child targets to resolve.

        Returns:
            None.
        """
        return None


@dataclass
class RoleInPlayCall(CallObject):
    """Call target for a role in play.

    Attributes:
        type: Always "roleinplaycall".
    """

    type: str = "roleinplaycall"


@dataclass
class Play(Object, Resolvable):
    """Ansible play with tasks, roles, handlers.

    Attributes:
        type: Always "play".
        name: Play name.
        defined_in: Path to the playbook.
        index: Play index.
        key: Unique key for lookup.
        local_key: Local key.
        role: Role name.
        collection: Collection name.
        import_module: Import module.
        import_playbook: Import playbook path.
        pre_tasks: Pre-tasks list.
        tasks: Tasks list.
        post_tasks: Post-tasks list.
        handlers: Handlers list.
        roles: RoleInPlay list.
        module_defaults: Module defaults.
        options: Play options.
        collections_in_play: Collections in scope.
        become: Privilege escalation.
        variables: Variables.
        vars_files: Vars file paths.
        jsonpath: Jsonpath to play.
        task_loading: Task loading metadata.
    """

    type: str = "play"
    name: str = ""
    defined_in: str = ""
    index: int = -1
    key: str = ""
    local_key: str = ""

    role: str = ""
    collection: str = ""
    import_module: str = ""
    import_playbook: str = ""
    pre_tasks: list[Task | str] = field(default_factory=list)
    tasks: list[Task | str] = field(default_factory=list)
    post_tasks: list[Task | str] = field(default_factory=list)
    handlers: list[Task | str] = field(default_factory=list)
    # not actual Role, but RoleInPlay defined in this playbook
    roles: list[RoleInPlay | str] = field(default_factory=list)
    module_defaults: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)
    collections_in_play: list[str] = field(default_factory=list)
    become: BecomeInfo | None = None
    variables: YAMLDict = field(default_factory=dict)
    vars_files: list[str] = field(default_factory=list)

    jsonpath: str = ""

    task_loading: YAMLDict = field(default_factory=dict)

    def set_key(self, parent_key: str = "", parent_local_key: str = "") -> None:
        """Set key from play identity and parent via keyutil.

        Args:
            parent_key: Key of the parent object.
            parent_local_key: Local key of the parent.
        """
        set_play_key(self, parent_key, parent_local_key)

    def children_to_key(self) -> Play:
        """Sort pre_tasks, tasks, post_tasks, handlers by key and return self.

        Returns:
            Self.
        """
        pre_task_keys = [t.key if isinstance(t, Task) else t for t in self.pre_tasks]
        self.pre_tasks = cast(list["Task | str"], sorted(pre_task_keys))

        task_keys = [t.key if isinstance(t, Task) else t for t in self.tasks]
        self.tasks = cast(list["Task | str"], sorted(task_keys))

        post_task_keys = [t.key if isinstance(t, Task) else t for t in self.post_tasks]
        self.post_tasks = cast(list["Task | str"], sorted(post_task_keys))

        handler_task_keys = [t.key if isinstance(t, Task) else t for t in self.handlers]
        self.handlers = cast(list["Task | str"], sorted(handler_task_keys))
        return self

    @property
    def id(self) -> str:
        """Return stable id from defined_in and index.

        Returns:
            JSON string with path and index.
        """
        return json.dumps({"path": self.defined_in, "index": self.index})

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Return pre_tasks, tasks, and roles as resolver targets.

        Returns:
            List of pre_tasks, tasks, and roles.
        """
        return cast(
            list["Resolvable | str"],
            list(self.pre_tasks) + list(self.tasks) + list(self.roles),
        )


@dataclass
class PlayCall(CallObject, RunTarget):
    """Call target for a play.

    Attributes:
        type: Always "playcall".
    """

    type: str = "playcall"


@dataclass
class Playbook(Object, Resolvable):
    """Ansible playbook with plays.

    Attributes:
        type: Always "playbook".
        name: Playbook name.
        defined_in: Path to the file.
        key: Unique key for lookup.
        local_key: Local key.
        yaml_lines: Raw YAML content.
        role: Role name.
        collection: Collection name.
        plays: List of Play or keys.
        used_in: Paths where this playbook is used.
        annotations: Annotation dict.
        variables: Variables.
        options: Playbook options.
    """

    type: str = "playbook"
    name: str = ""
    defined_in: str = ""
    key: str = ""
    local_key: str = ""

    yaml_lines: str = ""

    role: str = ""
    collection: str = ""

    plays: list[Play | str] = field(default_factory=list)

    used_in: list[str] = field(default_factory=list)  # resolved later

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    variables: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from playbook identity via keyutil."""
        set_playbook_key(self)

    def children_to_key(self) -> Playbook:
        """Sort plays by key and return self.

        Returns:
            Self.
        """
        play_keys = [play.key if isinstance(play, Play) else play for play in self.plays]
        self.plays = cast(list["Play | str"], sorted(play_keys))
        return self

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Return plays or roles+tasks as resolver targets.

        Returns:
            List of plays or roles and tasks.
        """
        if "plays" in self.__dict__:
            return cast(list["Resolvable | str"], self.plays)
        return cast(
            list["Resolvable | str"],
            list(getattr(self, "roles", [])) + list(getattr(self, "tasks", [])),
        )


@dataclass
class PlaybookCall(CallObject, RunTarget):
    """Call target for a playbook.

    Attributes:
        type: Always "playbookcall".
    """

    type: str = "playbookcall"


class InventoryType:
    """Constants for inventory types (group_vars, host_vars).

    Attributes:
        GROUP_VARS_TYPE: Group vars inventory type.
        HOST_VARS_TYPE: Host vars inventory type.
        UNKNOWN_TYPE: Unknown inventory type.
    """

    GROUP_VARS_TYPE = "group_vars"
    HOST_VARS_TYPE = "host_vars"
    UNKNOWN_TYPE = ""


@dataclass
class Inventory(JSONSerializable):
    """Inventory (group_vars, host_vars) with variables.

    Attributes:
        type: Always "inventory".
        name: Inventory name.
        defined_in: Path to the inventory file.
        inventory_type: One of group_vars, host_vars.
        group_name: Group name when inventory_type is group_vars.
        host_name: Host name when inventory_type is host_vars.
        variables: Variables dict.
    """

    type: str = "inventory"
    name: str = ""
    defined_in: str = ""
    inventory_type: str = ""
    group_name: str = ""
    host_name: str = ""
    variables: YAMLDict = field(default_factory=dict)


@dataclass
class Repository(Object, Resolvable):
    """Repository (project root) with playbooks, roles, modules, taskfiles.

    Attributes:
        type: Always "repository".
        name: Repository name.
        path: Repository path.
        key: Unique key for lookup.
        local_key: Local key.
        my_collection_name: Collection name if this is a collection repo.
        playbooks: List of Playbook or keys.
        roles: List of Role or keys.
        target_playbook_path: Target playbook for playbook scan.
        target_taskfile_path: Target taskfile for taskfile scan.
        requirements: Requirements metadata.
        installed_collections_path: Path to installed collections.
        installed_collections: List of Collection or keys.
        installed_roles_path: Path to installed roles.
        installed_roles: List of Role or keys.
        modules: List of Module or keys.
        taskfiles: List of TaskFile or keys.
        inventories: List of Inventory or keys.
        files: List of File or keys.
        version: Version string.
        annotations: Annotation dict.
    """

    type: str = "repository"
    name: str = ""
    path: str = ""
    key: str = ""
    local_key: str = ""

    # if set, this repository is a collection repository
    my_collection_name: str = ""

    playbooks: list[Playbook | str] = field(default_factory=list)
    roles: list[Role | str] = field(default_factory=list)

    # for playbook scan
    target_playbook_path: str = ""

    # for taskfile scan
    target_taskfile_path: str = ""

    requirements: YAMLDict = field(default_factory=dict)

    installed_collections_path: str = ""
    installed_collections: list[Collection | str] = field(default_factory=list)

    installed_roles_path: str = ""
    installed_roles: list[Role | str] = field(default_factory=list)
    modules: list[Module | str] = field(default_factory=list)
    taskfiles: list[TaskFile | str] = field(default_factory=list)

    inventories: list[Inventory | str] = field(default_factory=list)

    files: list[File | str] = field(default_factory=list)

    version: str = ""

    annotations: dict[str, YAMLValue] = field(default_factory=dict)

    def set_key(self) -> None:
        """Set key from repository identity via keyutil."""
        set_repository_key(self)

    def children_to_key(self) -> Repository:
        """Sort modules, playbooks, taskfiles, roles by key and return self.

        Returns:
            Self.
        """
        module_keys = [m.key if isinstance(m, Module) else m for m in self.modules]
        self.modules = cast(list["Module | str"], sorted(module_keys))

        playbook_keys = [p.key if isinstance(p, Playbook) else p for p in self.playbooks]
        self.playbooks = cast(list["Playbook | str"], sorted(playbook_keys))

        taskfile_keys = [tf.key if isinstance(tf, TaskFile) else tf for tf in self.taskfiles]
        self.taskfiles = cast(list["TaskFile | str"], sorted(taskfile_keys))

        role_keys = [r.key if isinstance(r, Role) else r for r in self.roles]
        self.roles = cast(list["Role | str"], sorted(role_keys))
        return self

    @property
    def resolver_targets(self) -> list[Resolvable | str]:
        """Return playbooks, roles, modules, installed_roles, installed_collections.

        Returns:
            List of resolver targets.
        """
        return cast(
            list["Resolvable | str"],
            list(self.playbooks)
            + list(self.roles)
            + list(self.modules)
            + list(self.installed_roles)
            + list(self.installed_collections),
        )


@dataclass
class RepositoryCall(CallObject):
    """Call target for a repository.

    Attributes:
        type: Always "repositorycall".
    """

    type: str = "repositorycall"


def call_obj_from_spec(spec: Object, caller: CallObject | None, index: int = 0) -> CallObject | None:
    """Create a CallObject from an Object spec and optional caller.

    Args:
        spec: The Object spec (Repository, Playbook, Play, RoleInPlay, Role, TaskFile, Task, or Module).
        caller: Optional CallObject that invokes this spec.
        index: Index for the call (default 0).

    Returns:
        The corresponding CallObject, or None if spec type is not supported.
    """
    if isinstance(spec, Repository):
        return RepositoryCall.from_spec(spec, caller, index)
    elif isinstance(spec, Playbook):
        return PlaybookCall.from_spec(spec, caller, index)
    elif isinstance(spec, Play):
        return PlayCall.from_spec(spec, caller, index)
    elif isinstance(spec, RoleInPlay):
        return RoleInPlayCall.from_spec(spec, caller, index)
    elif isinstance(spec, Role):
        return RoleCall.from_spec(spec, caller, index)
    elif isinstance(spec, TaskFile):
        return TaskFileCall.from_spec(spec, caller, index)
    elif isinstance(spec, Task):
        taskcall = cast(TaskCall, TaskCall.from_spec(spec, caller, index))
        taskcall.content = MutableContent.from_task_spec(task_spec=spec)
        return taskcall
    elif isinstance(spec, Module):
        return ModuleCall.from_spec(spec, caller, index)
    return None


# inherit Repository just for convenience
# this is not a Repository but one or multiple Role / Collection
@dataclass
class GalaxyArtifact(Repository):
    """Galaxy artifact (Role or Collection) with search dicts for modules, tasks, etc.

    Attributes:
        type: "Role" or "Collection".
        module_dict: Map from key to Module for lookup.
        task_dict: Map from key to Task for lookup.
        taskfile_dict: Map from key to TaskFile for lookup.
        role_dict: Map from key to Role for lookup.
        playbook_dict: Map from key to Playbook for lookup.
        collection_dict: Map from key to Collection for lookup.
    """

    type: str = ""  # Role or Collection

    # make it easier to search a module
    module_dict: dict[str, Module] = field(default_factory=dict)
    # make it easier to search a task
    task_dict: dict[str, Task] = field(default_factory=dict)
    # make it easier to search a taskfile
    taskfile_dict: dict[str, TaskFile] = field(default_factory=dict)
    # make it easier to search a role
    role_dict: dict[str, Role] = field(default_factory=dict)
    # make it easier to search a playbook
    playbook_dict: dict[str, Playbook] = field(default_factory=dict)
    # make it easier to search a collection
    collection_dict: dict[str, Collection] = field(default_factory=dict)


@dataclass
class ModuleMetadata:
    """Metadata for an Ansible module (fqcn, type, name, version, hash).

    Attributes:
        fqcn: Fully qualified collection name.
        type: Module type.
        name: Module name.
        version: Version string.
        hash: Content hash.
        deprecated: Whether the module is deprecated.
    """

    fqcn: str = ""
    # arguments: list = field(default_factory=list)
    type: str = ""
    name: str = ""
    version: str = ""
    hash: str = ""
    deprecated: bool = False

    @staticmethod
    def from_module(m: Module, metadata: YAMLDict) -> ModuleMetadata:
        """Build ModuleMetadata from a Module and metadata dict.

        Args:
            m: The Module instance.
            metadata: Dict with type, name, version, hash keys.

        Returns:
            ModuleMetadata populated from the module and metadata.
        """
        mm = ModuleMetadata()
        for key in mm.__dict__:
            if hasattr(m, key):
                val = getattr(m, key, None)
                setattr(mm, key, val)

        mm.type = str(metadata.get("type", ""))
        mm.name = str(metadata.get("name", ""))
        mm.version = str(metadata.get("version", ""))
        mm.hash = str(metadata.get("hash", ""))
        return mm

    @staticmethod
    def from_routing(dst: str, metadata: YAMLDict) -> ModuleMetadata:
        """Build ModuleMetadata from routing destination and metadata (deprecated).

        Args:
            dst: FQCN destination.
            metadata: Dict with type, name, version, hash keys.

        Returns:
            ModuleMetadata with deprecated=True.
        """
        mm = ModuleMetadata()
        mm.fqcn = dst
        mm.type = str(metadata.get("type", ""))
        mm.name = str(metadata.get("name", ""))
        mm.version = str(metadata.get("version", ""))
        mm.hash = str(metadata.get("hash", ""))
        mm.deprecated = True
        return mm

    @staticmethod
    def from_dict(d: YAMLDict) -> ModuleMetadata:
        """Build ModuleMetadata from a dict.

        Args:
            d: Dict with fqcn, type, name, version, hash keys.

        Returns:
            ModuleMetadata populated from the dict.
        """
        mm = ModuleMetadata()
        mm.fqcn = str(d.get("fqcn", ""))
        mm.type = str(d.get("type", ""))
        mm.name = str(d.get("name", ""))
        mm.version = str(d.get("version", ""))
        mm.hash = str(d.get("hash", ""))
        return mm

    def __eq__(self, mm: object) -> bool:
        """Compare equality by fqcn, name, type, version, and hash.

        Args:
            mm: Object to compare (must be ModuleMetadata).

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(mm, ModuleMetadata):
            return False
        return (
            self.fqcn == mm.fqcn
            and self.name == mm.name
            and self.type == mm.type
            and self.version == mm.version
            and self.hash == mm.hash
        )


@dataclass
class RoleMetadata:
    """Metadata for an Ansible role.

    Attributes:
        fqcn: Fully qualified collection name.
        type: Role type.
        name: Role name.
        version: Version string.
        hash: Content hash.
    """

    fqcn: str = ""
    type: str = ""
    name: str = ""
    version: str = ""
    hash: str = ""

    @staticmethod
    def from_role(r: Role, metadata: YAMLDict) -> RoleMetadata:
        """Build RoleMetadata from a Role and metadata dict.

        Args:
            r: The Role instance.
            metadata: Dict with type, name, version, hash keys.

        Returns:
            RoleMetadata populated from the role and metadata.
        """
        rm = RoleMetadata()
        for key in rm.__dict__:
            if hasattr(r, key):
                val = getattr(r, key, None)
                setattr(rm, key, val)

        rm.type = str(metadata.get("type", ""))
        rm.name = str(metadata.get("name", ""))
        rm.version = str(metadata.get("version", ""))
        rm.hash = str(metadata.get("hash", ""))
        return rm

    @staticmethod
    def from_dict(d: YAMLDict) -> RoleMetadata:
        """Build RoleMetadata from a dict.

        Args:
            d: Dict with fqcn, type, name, version, hash keys.

        Returns:
            RoleMetadata populated from the dict.
        """
        rm = RoleMetadata()
        rm.fqcn = str(d.get("fqcn", ""))
        rm.type = str(d.get("type", ""))
        rm.name = str(d.get("name", ""))
        rm.version = str(d.get("version", ""))
        rm.hash = str(d.get("hash", ""))
        return rm

    def __eq__(self, rm: object) -> bool:
        """Compare equality by fqcn, name, type, version, and hash.

        Args:
            rm: Object to compare (must be RoleMetadata).

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(rm, RoleMetadata):
            return False
        return (
            self.fqcn == rm.fqcn
            and self.name == rm.name
            and self.type == rm.type
            and self.version == rm.version
            and self.hash == rm.hash
        )


@dataclass
class TaskFileMetadata:
    """Metadata for a task file.

    Attributes:
        key: Task file key.
        type: Task file type.
        name: Task file name.
        version: Version string.
        hash: Content hash.
    """

    key: str = ""
    type: str = ""
    name: str = ""
    version: str = ""
    hash: str = ""

    @staticmethod
    def from_taskfile(tf: TaskFile, metadata: YAMLDict) -> TaskFileMetadata:
        """Build TaskFileMetadata from a TaskFile and metadata dict.

        Args:
            tf: The TaskFile instance.
            metadata: Dict with type, name, version, hash keys.

        Returns:
            TaskFileMetadata populated from the task file and metadata.
        """
        tfm = TaskFileMetadata()
        for key in tfm.__dict__:
            if hasattr(tf, key):
                val = getattr(tf, key, None)
                setattr(tfm, key, val)

        tfm.type = str(metadata.get("type", ""))
        tfm.name = str(metadata.get("name", ""))
        tfm.version = str(metadata.get("version", ""))
        tfm.hash = str(metadata.get("hash", ""))
        return tfm

    @staticmethod
    def from_dict(d: YAMLDict) -> TaskFileMetadata:
        """Build TaskFileMetadata from a dict.

        Args:
            d: Dict with key, type, name, version, hash keys.

        Returns:
            TaskFileMetadata populated from the dict.
        """
        tfm = TaskFileMetadata()
        tfm.key = str(d.get("key", ""))
        tfm.type = str(d.get("type", ""))
        tfm.name = str(d.get("name", ""))
        tfm.version = str(d.get("version", ""))
        tfm.hash = str(d.get("hash", ""))
        return tfm

    def __eq__(self, tfm: object) -> bool:
        """Compare equality by key, name, type, version, and hash.

        Args:
            tfm: Object to compare (must be TaskFileMetadata).

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(tfm, TaskFileMetadata):
            return False
        return (
            self.key == tfm.key
            and self.name == tfm.name
            and self.type == tfm.type
            and self.version == tfm.version
            and self.hash == tfm.hash
        )


@dataclass
class ActionGroupMetadata:
    """Metadata for an action group (module group).

    Attributes:
        group_name: Name of the action group.
        group_modules: List of Module instances in the group.
        type: Group type.
        name: Group name.
        version: Version string.
        hash: Content hash.
    """

    group_name: str = ""
    group_modules: list[Module] = field(default_factory=list)
    type: str = ""
    name: str = ""
    version: str = ""
    hash: str = ""

    @staticmethod
    def from_action_group(
        group_name: str, group_modules: list[Module], metadata: YAMLDict
    ) -> ActionGroupMetadata | None:
        """Build ActionGroupMetadata from group name, modules, and metadata.

        Args:
            group_name: Name of the action group.
            group_modules: List of Module instances.
            metadata: Dict with type, name, version, hash keys.

        Returns:
            ActionGroupMetadata, or None if group_name or group_modules is empty.
        """
        if not group_name:
            return None

        if not group_modules:
            return None

        agm = ActionGroupMetadata()
        agm.group_name = group_name
        agm.group_modules = group_modules
        agm.type = str(metadata.get("type", ""))
        agm.name = str(metadata.get("name", ""))
        agm.version = str(metadata.get("version", ""))
        agm.hash = str(metadata.get("hash", ""))
        return agm

    @staticmethod
    def from_dict(d: YAMLDict) -> ActionGroupMetadata:
        """Build ActionGroupMetadata from a dict.

        Args:
            d: Dict with group_name, group_modules, type, name, version, hash keys.

        Returns:
            ActionGroupMetadata populated from the dict.
        """
        agm = ActionGroupMetadata()
        agm.group_name = str(d.get("group_name", ""))
        agm.group_modules = cast(list["Module"], d.get("group_modules", []))
        agm.type = str(d.get("type", ""))
        agm.name = str(d.get("name", ""))
        agm.version = str(d.get("version", ""))
        agm.hash = str(d.get("hash", ""))
        return agm

    def __eq__(self, agm: object) -> bool:
        """Compare equality by group_name, name, type, version, and hash.

        Args:
            agm: Object to compare (must be ActionGroupMetadata).

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(agm, ActionGroupMetadata):
            return False
        return (
            self.group_name == agm.group_name
            and self.name == agm.name
            and self.type == agm.type
            and self.version == agm.version
            and self.hash == agm.hash
        )


# following ansible-lint severity levels
class Severity:
    """Severity levels for rule results (ansible-lint compatible).

    Attributes:
        VERY_HIGH: Highest severity.
        HIGH: High severity.
        MEDIUM: Medium severity.
        LOW: Low severity.
        VERY_LOW: Very low severity.
        NONE: No severity (informational).
    """

    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"
    NONE = "none"


_severity_level_mapping = {
    Severity.VERY_HIGH: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.VERY_LOW: 1,
    Severity.NONE: 0,
}


class RuleTag:
    """Rule tags for categorization (network, command, dependency, etc.).

    Attributes:
        NETWORK: Network-related rule.
        COMMAND: Command-related rule.
        DEPENDENCY: Dependency-related rule.
        SYSTEM: System-related rule.
        PACKAGE: Package-related rule.
        CODING: Coding-related rule.
        VARIABLE: Variable-related rule.
        QUALITY: Quality-related rule.
        DEBUG: Debug-related rule.
    """

    NETWORK = "network"
    COMMAND = "command"
    DEPENDENCY = "dependency"
    SYSTEM = "system"
    PACKAGE = "package"
    CODING = "coding"
    VARIABLE = "variable"
    QUALITY = "quality"
    DEBUG = "debug"


@dataclass
class RuleMetadata:
    """Metadata for a rule (id, description, name, version, severity, tags).

    Attributes:
        rule_id: Unique rule identifier.
        description: Rule description.
        name: Rule name.
        version: Version string.
        commit_id: Commit ID.
        severity: Severity level.
        tags: Tags for categorization.
    """

    rule_id: str = ""
    description: str = ""
    name: str = ""

    version: str = ""
    commit_id: str = ""
    severity: str = ""
    tags: tuple[str, ...] = ()


@dataclass
class SpecMutation:
    """Mutation for a spec object: key, changes, object, and rule.

    Attributes:
        key: Optional key for the mutation.
        changes: List of changes.
        object: Object being mutated.
        rule: RuleMetadata for the rule that produced this mutation.
    """

    key: str | None = None
    changes: list[YAMLValue] = field(default_factory=list)
    object: Object = field(default_factory=Object)
    rule: RuleMetadata = field(default_factory=RuleMetadata)


@dataclass
class RuleResult:
    """Result of applying a rule to a target.

    Attributes:
        rule: RuleMetadata for the rule that produced this result.
        verdict: Whether the rule passed (True) or failed (False).
        detail: Optional dict with additional details.
        file: Optional file location tuple (path, line, etc.).
        error: Optional error message.
        matched: Whether the rule matched the target.
        duration: Optional duration in seconds.
    """

    rule: RuleMetadata | None = None

    verdict: bool = False
    detail: YAMLDict | None = None
    file: tuple[str | int, ...] | None = None
    error: str | None = None

    matched: bool = False
    duration: float | None = None

    def __post_init__(self) -> None:
        """Normalize verdict to bool."""
        if self.verdict:
            self.verdict = True
        else:
            self.verdict = False

    def set_value(self, key: str, value: YAMLValue) -> None:
        """Set a key in the detail dict.

        Args:
            key: Key to set.
            value: Value to set.

        """
        if self.detail is not None:
            self.detail[key] = value

    def get_detail(self) -> YAMLDict | None:
        """Return the detail dict.

        Returns:
            The detail dict, or None if not set.
        """
        return self.detail


@dataclass
class Rule(RuleMetadata):
    """Base class for policy rules with match/process logic.

    Attributes:
        enabled: Whether the rule is enabled.
        precedence: Evaluation order (lower evaluated earlier).
        spec_mutation: Whether the rule mutates spec objects.
    """

    # `enabled` represents if the rule is enabled or not
    enabled: bool = False

    # `precedence` represents the order of the rule evaluation.
    # A rule with a lower number will be evaluated earlier than others.
    precedence: int = 10

    # `spec_mutation` represents if the rule mutates spec objects
    # if there are any spec mutations, re-run the scan later with the mutated spec
    spec_mutation: bool = False

    def __post_init__(self, rule_id: str = "", description: str = "") -> None:
        """Initialize rule_id and description; validate both are set.

        Args:
            rule_id: Optional rule ID to set.
            description: Optional description to set.

        Raises:
            ValueError: If rule_id or description is empty.
        """
        if rule_id:
            self.rule_id = rule_id
        if description:
            self.description = description

        if not self.rule_id:
            raise ValueError("A rule must have a unique rule_id")

        if not self.description:
            raise ValueError("A rule must have a description")

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if this rule applies to the given context.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if the rule applies.

        Raises:
            ValueError: Base class method; must be overridden.
        """
        raise ValueError("this is a base class method")

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Process the context and return a rule result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult or None.

        Raises:
            ValueError: Base class method; must be overridden.
        """
        raise ValueError("this is a base class method")

    def print(self, result: RuleResult) -> str:
        """Format a human-readable result string.

        Args:
            result: RuleResult to format.

        Returns:
            Formatted string with rule ID, severity, description, verdict, file, detail.
        """
        output = (
            f"ruleID={self.rule_id}, severity={self.severity}, description={self.description}, result={result.verdict}"
        )

        if result.file:
            output += f", file={result.file}"
        if result.detail:
            output += f", detail={result.detail}"
        return output

    def to_json(self, result: RuleResult) -> str:
        """Serialize result detail to JSON.

        Args:
            result: RuleResult to serialize.

        Returns:
            JSON string of result.detail.
        """
        return str(json.dumps(result.detail))

    def error(self, result: RuleResult) -> str | None:
        """Return the error message from a result if any.

        Args:
            result: RuleResult to check.

        Returns:
            Error string or None.
        """
        if result.error:
            return result.error
        return None

    def get_metadata(self) -> RuleMetadata:
        """Return RuleMetadata for this rule.

        Returns:
            RuleMetadata with rule_id, description, name, version, commit_id, severity, tags.
        """
        return RuleMetadata(
            rule_id=self.rule_id,
            description=self.description,
            name=self.name,
            version=self.version,
            commit_id=self.commit_id,
            severity=self.severity,
            tags=self.tags,
        )


@dataclass
class NodeResult(JSONSerializable):
    """Rule results for a single node (RunTarget).

    Attributes:
        node: The RunTarget or YAMLDict being evaluated.
        rules: List of RuleResult for this node.
    """

    node: RunTarget | YAMLDict | None = None
    rules: list[RuleResult] = field(default_factory=list)

    def results(self) -> list[RuleResult]:
        """Return the list of RuleResult for this node.

        Returns:
            List of RuleResult.
        """
        return self.rules

    def find_result(self, rule_id: str) -> RuleResult | None:
        """Find the first result for a given rule ID.

        Args:
            rule_id: Rule ID to search for.

        Returns:
            RuleResult or None if not found.
        """
        filtered = [r for r in self.rules if r.rule and r.rule.rule_id == rule_id]
        if not filtered:
            return None
        return filtered[0]

    def search_results(
        self,
        rule_id: str | list[str] | None = None,
        tag: str | list[str] | None = None,
        matched: bool | None = None,
        verdict: bool | None = None,
    ) -> list[RuleResult]:
        """Filter results by rule_id, tag, matched, and/or verdict.

        Args:
            rule_id: Rule ID(s) to filter by.
            tag: Tag(s) to filter by.
            matched: Filter by matched status.
            verdict: Filter by verdict (pass/fail).

        Returns:
            Filtered list of RuleResult.
        """
        if not rule_id and not tag:
            return self.rules

        filtered = self.rules
        if rule_id:
            target_rule_ids = []
            if isinstance(rule_id, str):
                target_rule_ids = [rule_id]
            elif isinstance(rule_id, list):
                target_rule_ids = rule_id
            filtered = [r for r in filtered if r.rule and r.rule.rule_id in target_rule_ids]

        if tag:
            target_tags: list[str] = []
            if isinstance(tag, str):
                target_tags = [tag]
            elif isinstance(tag, list):
                target_tags = tag
            filtered = [r for r in filtered if r.rule is not None and any(t in target_tags for t in r.rule.tags)]

        if matched is not None:
            filtered = [r for r in filtered if r.matched == matched]

        if verdict is not None:
            filtered = [r for r in filtered if r.verdict == verdict]

        return filtered


@dataclass
class TargetResult(JSONSerializable):
    """Rule results for a single target (playbook, role, or taskfile).

    Attributes:
        target_type: One of playbook, role, taskfile.
        target_name: Name of the target.
        nodes: List of NodeResult for each node in the target.
    """

    target_type: str = ""  # playbook, role or taskfile
    target_name: str = ""
    nodes: list[NodeResult] = field(default_factory=list)

    def applied_rules(self) -> list[RuleResult]:
        """Return all results where the rule matched the target.

        Returns:
            List of RuleResult with matched=True.
        """
        results: list[RuleResult] = []
        for n in self.nodes:
            matched_rules = n.search_results(matched=True)
            if matched_rules:
                results.extend(matched_rules)
        return results

    def matched_rules(self) -> list[RuleResult]:
        """Return all results where the rule verdict is True (passed).

        Returns:
            List of RuleResult with verdict=True.
        """
        results: list[RuleResult] = []
        for n in self.nodes:
            matched_rules = n.search_results(verdict=True)
            if matched_rules:
                results.extend(matched_rules)
        return results

    def tasks(self) -> TargetResult:
        """Filter to only task nodes.

        Returns:
            TargetResult with only TaskCall nodes.
        """
        return self._filter(TaskCall)

    def task(self, name: str) -> NodeResult | None:
        """Find a task node by name.

        Args:
            name: Task name to find.

        Returns:
            NodeResult or None if not found.
        """
        return self._find_by_name(name=name, target_type=TaskCall)

    def roles(self) -> TargetResult:
        """Filter to only role nodes.

        Returns:
            TargetResult with only RoleCall nodes.
        """
        return self._filter(RoleCall)

    def role(self, name: str) -> NodeResult | None:
        """Find a role node by name.

        Args:
            name: Role name to find.

        Returns:
            NodeResult or None if not found.
        """
        return self._find_by_name(name=name, target_type=RoleCall)

    def playbooks(self) -> TargetResult:
        """Filter to only playbook nodes.

        Returns:
            TargetResult with only PlaybookCall nodes.
        """
        return self._filter(PlaybookCall)

    def playbook(self, name: str) -> NodeResult | None:
        """Find a playbook node by name.

        Args:
            name: Playbook name to find.

        Returns:
            NodeResult or None if not found.
        """
        return self._find_by_name(name=name, target_type=PlaybookCall)

    def plays(self) -> TargetResult:
        """Filter to only play nodes.

        Returns:
            TargetResult with only PlayCall nodes.
        """
        return self._filter(PlayCall)

    def play(self, name: str) -> NodeResult | None:
        """Find a play node by name.

        Args:
            name: Play name to find.

        Returns:
            NodeResult or None if not found.
        """
        return self._find_by_name(name=name, target_type=PlayCall)

    def taskfiles(self) -> TargetResult:
        """Filter to only taskfile nodes.

        Returns:
            TargetResult with only TaskFileCall nodes.
        """
        return self._filter(TaskFileCall)

    def taskfile(self, name: str) -> NodeResult | None:
        """Find a taskfile node by name.

        Args:
            name: Taskfile name to find.

        Returns:
            NodeResult or None if not found.
        """
        return self._find_by_name(name=name, target_type=TaskFileCall)

    def _find_by_name(self, name: str, target_type: type[RunTarget] | None = None) -> NodeResult | None:
        """Find a node by name, optionally filtered by target type.

        Args:
            name: Name to match (from spec.name).
            target_type: Optional RunTarget subclass to filter by.

        Returns:
            First matching NodeResult or None.
        """
        nodes = deepcopy(self.nodes)
        if target_type:
            type_only_result = self._filter(target_type)
            if not type_only_result:
                return None
            nodes = type_only_result.nodes
        filtered_nodes = [nr for nr in nodes if nr.node and getattr(getattr(nr.node, "spec", None), "name", "") == name]
        if not filtered_nodes:
            return None
        return filtered_nodes[0]

    def _filter(self, target_type: type[RunTarget]) -> TargetResult:
        """Filter nodes by RunTarget type.

        Args:
            target_type: RunTarget subclass to filter by.

        Returns:
            New TargetResult with only matching nodes.
        """
        filtered_nodes = [nr for nr in self.nodes if isinstance(nr.node, target_type)]
        return TargetResult(target_type=self.target_type, target_name=self.target_name, nodes=filtered_nodes)


@dataclass
class ARIResult(JSONSerializable):
    """Aggregated rule results for all targets in a scan.

    Attributes:
        targets: List of TargetResult for each scanned target.
    """

    targets: list[TargetResult] = field(default_factory=list)

    def playbooks(self) -> ARIResult:
        """Filter to only playbook targets.

        Returns:
            ARIResult with only playbook targets.
        """
        return self._filter("playbook")

    def playbook(self, name: str = "", path: str = "", yaml_str: str = "") -> TargetResult | None:
        """Find a playbook target by name, path, or yaml_str.

        Args:
            name: Playbook name to find.
            path: path to derive name from (uses basename).
            yaml_str: yaml_lines content to match.

        Returns:
            TargetResult or None if not found.
        """
        if name:
            return self._find_by_name(name)

        # TODO: use path correctly
        if path:
            name = os.path.basename(path)
            return self._find_by_name(name)

        if yaml_str:
            return self._find_by_yaml_str(yaml_str, "playbook")

        return None

    def roles(self) -> ARIResult:
        """Filter to only role targets.

        Returns:
            ARIResult with only role targets.
        """
        return self._filter("role")

    def role(self, name: str) -> TargetResult | None:
        """Find a role target by name.

        Args:
            name: Role name to find.

        Returns:
            TargetResult or None if not found.
        """
        return self._find_by_name(name=name, type_str="role")

    def taskfiles(self) -> ARIResult:
        """Filter to only taskfile targets.

        Returns:
            ARIResult with only taskfile targets.
        """
        return self._filter("taskfile")

    def taskfile(self, name: str = "", path: str = "", yaml_str: str = "") -> TargetResult | None:
        """Find a taskfile target by name, path, or yaml_str.

        Args:
            name: Taskfile name to find.
            path: path to derive name from (uses basename).
            yaml_str: yaml_lines content to match.

        Returns:
            TargetResult or None if not found.
        """
        if name:
            return self._find_by_name(name=name, type_str="taskfile")

        # TODO: use path correctly
        if path:
            name = os.path.basename(path)
            return self._find_by_name(name=name, type_str="taskfile")

        if yaml_str:
            return self._find_by_yaml_str(yaml_str, "taskfile")

        return None

    def find_target(
        self, name: str = "", path: str = "", yaml_str: str = "", target_type: str = ""
    ) -> TargetResult | None:
        """Find a target by name, path, or yaml_str.

        Args:
            name: Target name to find.
            path: Path to derive name from (uses basename).
            yaml_str: yaml_lines content to match.
            target_type: Target type filter.

        Returns:
            TargetResult or None if not found.
        """
        if name:
            return self._find_by_name(name=name, type_str=target_type)

        # TODO: use path correctly
        if path:
            name = os.path.basename(path)
            return self._find_by_name(name=name, type_str=target_type)

        if yaml_str:
            return self._find_by_yaml_str(yaml_str, target_type)

        return None

    def _find_by_name(self, name: str, type_str: str = "") -> TargetResult | None:
        """Find a target by name, optionally filtered by type.

        Args:
            name: Target name to match.
            type_str: Optional target type filter.

        Returns:
            TargetResult or None if not found.
        """
        targets = deepcopy(self.targets)
        if type_str:
            type_only_result = self._filter(type_str)
            if not type_only_result:
                return None
            targets = type_only_result.targets
        filtered_targets = [tr for tr in targets if tr.target_name == name]
        if not filtered_targets:
            return None
        return filtered_targets[0]

    def _find_by_yaml_str(self, yaml_str: str, type_str: str) -> TargetResult | None:
        """Find a target by yaml_lines content.

        Args:
            yaml_str: yaml_lines content to match.
            type_str: Target type filter.

        Returns:
            TargetResult or None if not found.
        """
        type_only_result = self._filter(type_str)
        if not type_only_result:
            return None
        filtered_targets = [
            tr
            for tr in type_only_result.targets
            if tr.nodes
            and tr.nodes[0].node
            and getattr(getattr(tr.nodes[0].node, "spec", None), "yaml_lines", "") == yaml_str
        ]
        if not filtered_targets:
            return None
        return filtered_targets[0]

    def _filter(self, type_str: str) -> ARIResult:
        """Filter targets by type.

        Args:
            type_str: Target type (playbook, role, taskfile).

        Returns:
            New ARIResult with only matching targets.
        """
        filtered_targets = [tr for tr in self.targets if tr.target_type == type_str]
        return ARIResult(targets=filtered_targets)
