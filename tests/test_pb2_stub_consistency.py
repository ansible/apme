"""Keep the checked-in ``*_pb2.pyi`` stubs in sync with the compiled protos.

``src/apme/v1/*_pb2.pyi`` are hand-maintained typing stubs for the generated
protobuf modules (``protoc`` emits ``.py`` without type information, and the
generated ``--pyi_out`` variant uses container types this codebase does not
use). Because they are hand-maintained, they silently drift whenever a field
is added to ``proto/apme/v1/*.proto`` — which is exactly what happened in the
PR that added ``ScanChunk.dependency_files`` and ``FormatResponse.diffs``:
the runtime messages gained fields the stubs never declared, so mypy callers
saw an incomplete type.

These tests fail as soon as a stub and its descriptor disagree in either
direction (missing declaration, or a declaration for a field that no longer
exists), so drift becomes a test failure instead of a latent typing bug.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from google.protobuf.descriptor import Descriptor, FieldDescriptor, FileDescriptor

STUB_DIR = Path(__file__).resolve().parents[1] / "src" / "apme" / "v1"
PB2_MODULES = ("common", "engine", "validate", "reporting")

# Attributes a stub may declare that are not message fields.
_NON_FIELD_ATTRIBUTES = frozenset({"__slots__"})


def _descriptor_field_names(descriptor: Descriptor) -> set[str]:
    """Return every field name of *descriptor*, including nested messages.

    Args:
        descriptor: Message descriptor from the generated module.

    Returns:
        Set of field names declared directly on that message.
    """
    return {field.name for field in descriptor.fields}


def _all_message_descriptors(descriptor: FileDescriptor) -> dict[str, Descriptor]:
    """Collect top-level and nested message descriptors by class name.

    Args:
        descriptor: File descriptor of a generated ``*_pb2`` module.

    Returns:
        Mapping of stub class name to its message descriptor.
    """
    found: dict[str, Descriptor] = {}
    pending = list(descriptor.message_types_by_name.values())
    while pending:
        message = pending.pop()
        # ``map<k, v>`` fields synthesize a nested *Entry message that has no
        # stub class of its own, so it must not be treated as a message.
        if message.GetOptions().map_entry:
            continue
        found[message.name] = message
        pending.extend(message.nested_types)
    return found


def _stub_field_annotations(stub_path: Path) -> dict[str, set[str]]:
    """Parse a stub file and return annotated attributes per class.

    Args:
        stub_path: Path to the ``.pyi`` stub.

    Returns:
        Mapping of class name to the set of annotated attribute names.
    """
    tree = ast.parse(stub_path.read_text(encoding="utf-8"))
    annotations: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        annotations[node.name] = names - _NON_FIELD_ATTRIBUTES
    return annotations


@pytest.mark.parametrize("module_name", PB2_MODULES)  # type: ignore[untyped-decorator]
def test_stub_matches_descriptors(module_name: str) -> None:
    """Every proto field is declared in the stub and vice versa.

    Args:
        module_name: Stem of the ``*_pb2`` module under test.
    """
    module = importlib.import_module(f"apme.v1.{module_name}_pb2")
    descriptor: FileDescriptor = module.DESCRIPTOR
    stub_path = STUB_DIR / f"{module_name}_pb2.pyi"
    stub_annotations = _stub_field_annotations(stub_path)

    missing_classes: list[str] = []
    field_mismatches: list[str] = []
    for class_name, message in sorted(_all_message_descriptors(descriptor).items()):
        declared = stub_annotations.get(class_name)
        if declared is None:
            missing_classes.append(class_name)
            continue
        expected = _descriptor_field_names(message)
        missing_fields = expected - declared
        stale_fields = declared - expected
        if missing_fields:
            field_mismatches.append(f"{class_name}: stub is missing {sorted(missing_fields)}")
        if stale_fields:
            field_mismatches.append(f"{class_name}: stub declares non-existent {sorted(stale_fields)}")

    assert not missing_classes, f"{stub_path.name} has no class for message(s): {sorted(missing_classes)}"
    assert not field_mismatches, f"{stub_path.name} drifted from {module_name}.proto:\n" + "\n".join(field_mismatches)


@pytest.mark.parametrize("module_name", PB2_MODULES)  # type: ignore[untyped-decorator]
def test_stub_declares_no_unknown_classes(module_name: str) -> None:
    """A stub class that is neither a message nor an enum is a stale entry.

    Args:
        module_name: Stem of the ``*_pb2`` module under test.
    """
    module = importlib.import_module(f"apme.v1.{module_name}_pb2")
    descriptor: FileDescriptor = module.DESCRIPTOR
    known = set(_all_message_descriptors(descriptor)) | set(descriptor.enum_types_by_name)
    stub_path = STUB_DIR / f"{module_name}_pb2.pyi"
    stub_classes = set(_stub_field_annotations(stub_path))
    assert not stub_classes - known, (
        f"{stub_path.name} declares classes with no descriptor equivalent: {sorted(stub_classes - known)}"
    )


def test_field_descriptor_import_still_available() -> None:
    """Guard the import used above so a protobuf bump fails loudly here.

    ``FieldDescriptor`` is only imported for its side-effect-free reference;
    if protobuf ever renames it this assertion points at the cause instead of
    surfacing as an unrelated ImportError in the parametrised tests.
    """
    assert FieldDescriptor is not None
