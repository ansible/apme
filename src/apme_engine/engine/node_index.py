"""NodeIndex -- flat key-based lookup over hierarchy payload nodes.

Built from the ``hierarchy_payload`` produced by
``ARIScanner.build_hierarchy_payload()``.  Each node dict carries at
minimum ``key``, ``type``, ``file``, ``line``, and ``defined_in``.
"""

from __future__ import annotations

NodeDict = dict[str, object]

KEY_SEPARATOR = "#"


class NodeIndex:
    """Flat index of hierarchy nodes keyed by their unique key string.

    Also maintains a secondary ``(file, line)`` index for violations
    that arrive without a ``path`` set.
    """

    __slots__ = ("_by_key", "_by_file_line")

    def __init__(self, hierarchy_payload: dict[str, object]) -> None:
        """Build the index from a hierarchy payload dict.

        Args:
            hierarchy_payload: The dict returned by
                ``ARIScanner.build_hierarchy_payload()``.  Expected shape::

                    {"hierarchy": [{"nodes": [node_dict, ...]}, ...], ...}
        """
        self._by_key: dict[str, NodeDict] = {}
        self._by_file_line: dict[tuple[str, int], NodeDict] = {}

        hierarchy = hierarchy_payload.get("hierarchy")
        if not isinstance(hierarchy, list):
            return
        for tree in hierarchy:
            if not isinstance(tree, dict):
                continue
            nodes = tree.get("nodes")
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                key = node.get("key", "")
                if not isinstance(key, str) or not key:
                    continue
                self._by_key[key] = node
                file_path = node.get("file", "")
                line = node.get("line")
                if isinstance(file_path, str) and file_path and line is not None:
                    first_line = int(line[0]) if isinstance(line, list | tuple) else int(str(line))
                    self._by_file_line[(file_path, first_line)] = node

    def get(self, key: str) -> NodeDict | None:
        """Look up a node by its key.

        Args:
            key: Node key string.

        Returns:
            Node dict or None if not found.
        """
        return self._by_key.get(key)

    def __contains__(self, key: object) -> bool:
        """Check whether a key exists in the index.

        Args:
            key: Node key string.

        Returns:
            True if key is present.
        """
        return key in self._by_key

    def __len__(self) -> int:
        """Return the number of indexed nodes.

        Returns:
            Count of nodes.
        """
        return len(self._by_key)

    def find_by_file_line(self, file: str, line: int) -> NodeDict | None:
        """Look up a node by file path and first line number.

        Args:
            file: Absolute or relative file path.
            line: 1-indexed line number.

        Returns:
            Node dict or None if not found.
        """
        return self._by_file_line.get((file, line))

    @staticmethod
    def parent_key(key: str) -> str | None:
        """Derive the parent key by stripping the last segment.

        Args:
            key: Node key string (segments separated by ``#``).

        Returns:
            Parent key string, or None if there is no parent segment.
        """
        idx = key.rfind(KEY_SEPARATOR)
        if idx <= 0:
            return None
        return key[:idx]

    def ancestors(self, key: str) -> list[NodeDict]:
        """Return ancestor nodes from parent up to root.

        Args:
            key: Starting node key.

        Returns:
            List of ancestor node dicts (nearest parent first).
        """
        result: list[NodeDict] = []
        current = key
        while True:
            parent = self.parent_key(current)
            if parent is None:
                break
            node = self._by_key.get(parent)
            if node is not None:
                result.append(node)
            current = parent
        return result
