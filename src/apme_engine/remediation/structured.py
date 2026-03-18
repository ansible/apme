"""StructuredFile — parse-once wrapper for ruamel.yaml round-trip data.

A StructuredFile holds the parsed CommentedMap/CommentedSeq for a single
YAML file.  Transforms operate on the in-memory structure and the file
is serialized only when the engine needs to write it to disk.
"""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from apme_engine.engine.yaml_utils import FormattedYAML


class StructuredFile:
    """Parsed YAML file with in-place mutation and deferred serialization."""

    __slots__ = ("file_path", "data", "dirty", "_yaml")

    def __init__(
        self,
        file_path: str,
        data: CommentedMap | CommentedSeq,
        yaml_instance: FormattedYAML,
    ) -> None:
        """Initialize a StructuredFile.

        Args:
            file_path: Absolute path to the source file.
            data: Already-parsed ruamel.yaml round-trip structure.
            yaml_instance: Configured FormattedYAML used for serialization.
        """
        self.file_path = file_path
        self.data = data
        self.dirty = False
        self._yaml = yaml_instance

    @classmethod
    def from_content(cls, file_path: str, content: str) -> StructuredFile | None:
        """Parse YAML content into a StructuredFile.

        Args:
            file_path: Path to associate with this file.
            content: Raw YAML string.

        Returns:
            StructuredFile on success, None if the YAML is unparseable.
        """
        yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
        try:
            data = yaml.load(content)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, CommentedMap | CommentedSeq):
            return None
        return cls(file_path, data, yaml)

    def serialize(self) -> str:
        """Serialize the in-memory structure back to a YAML string.

        Returns:
            YAML string produced by FormattedYAML.dumps.
        """
        return self._yaml.dumps(self.data)

    def find_task(self, line: int) -> CommentedMap | None:
        """Locate the task CommentedMap at a 1-based line number.

        Defers the import of ``find_task_at_line`` to avoid a circular
        dependency (structured -> transforms._helpers -> transforms.__init__
        -> L007 etc. -> structured).

        Args:
            line: 1-indexed line number from a violation.

        Returns:
            Task CommentedMap or None if not found.
        """
        from apme_engine.remediation.transforms._helpers import find_task_at_line

        return find_task_at_line(self.data, line)

    def mark_dirty(self) -> None:
        """Mark this file as having been modified by a transform."""
        self.dirty = True

    def reset_dirty(self) -> None:
        """Clear the dirty flag (e.g. after serialization)."""
        self.dirty = False
