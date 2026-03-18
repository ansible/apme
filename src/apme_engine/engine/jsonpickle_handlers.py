"""Custom jsonpickle handlers so AnsibleRunContext and RunTargetList encode as objects, not iterators.

Without these, jsonpickle encodes any object with __iter__/__next__ as py/iterator,
which decodes as list_iterator and breaks the native validator.
"""

from __future__ import annotations

import jsonpickle.handlers  # type: ignore[import-not-found]


def register_engine_handlers() -> None:
    """Register handlers for AnsibleRunContext and RunTargetList. Idempotent."""
    from apme_engine.engine import models as _models

    AnsibleRunContext = _models.AnsibleRunContext  # noqa: N806
    RunTargetList = _models.RunTargetList  # noqa: N806

    if jsonpickle.handlers.registry.get(RunTargetList) is not None:
        return  # already registered

    class RunTargetListHandler(jsonpickle.handlers.BaseHandler):  # type: ignore[misc]
        """Encode RunTargetList as a normal object (items + _i), not as an iterator."""

        def flatten(self, obj: object, data: dict[str, object]) -> dict[str, object]:
            """Flatten RunTargetList into a jsonpickle-compatible dict.

            Args:
                obj: The RunTargetList instance to flatten.
                data: The dict to populate with serialized data.

            Returns:
                The populated data dict.
            """
            data["py/object"] = "apme_engine.engine.models.RunTargetList"
            data["items"] = self.context.flatten(getattr(obj, "items", []), reset=False)
            data["_i"] = 0
            return data

        def restore(self, data: dict[str, object]) -> object:
            """Restore a RunTargetList from a jsonpickle dict.

            Args:
                data: The serialized dict.

            Returns:
                Restored RunTargetList instance.
            """
            items = self.context.restore(data.get("items", []), reset=False)
            return RunTargetList(items=items, _i=0)

    class AnsibleRunContextHandler(jsonpickle.handlers.BaseHandler):  # type: ignore[misc]
        """Encode AnsibleRunContext as a normal object, not as an iterator."""

        def flatten(self, obj: object, data: dict[str, object]) -> dict[str, object]:
            """Flatten AnsibleRunContext into a jsonpickle-compatible dict.

            Args:
                obj: The AnsibleRunContext instance to flatten.
                data: The dict to populate with serialized data.

            Returns:
                The populated data dict.
            """
            data["py/object"] = "apme_engine.engine.models.AnsibleRunContext"
            data["sequence"] = self.context.flatten(getattr(obj, "sequence", RunTargetList()), reset=False)
            data["root_key"] = getattr(obj, "root_key", "") or ""
            data["parent"] = self.context.flatten(getattr(obj, "parent", None), reset=False)
            data["ram_client"] = self.context.flatten(getattr(obj, "ram_client", None), reset=False)
            data["scan_metadata"] = self.context.flatten(getattr(obj, "scan_metadata", {}), reset=False)
            data["current"] = self.context.flatten(getattr(obj, "current", None), reset=False)
            data["_i"] = 0
            data["last_item"] = getattr(obj, "last_item", False)
            data["vars"] = self.context.flatten(getattr(obj, "vars", None), reset=False)
            data["host_info"] = self.context.flatten(getattr(obj, "host_info", None), reset=False)
            return data

        def restore(self, data: dict[str, object]) -> object:
            """Restore an AnsibleRunContext from a jsonpickle dict.

            Args:
                data: The serialized dict.

            Returns:
                Restored AnsibleRunContext instance.
            """
            sequence = self.context.restore(data.get("sequence"), reset=False)
            parent = self.context.restore(data.get("parent"), reset=False)
            ram_client = self.context.restore(data.get("ram_client"), reset=False)
            scan_metadata = self.context.restore(data.get("scan_metadata", {}), reset=False)
            current = self.context.restore(data.get("current"), reset=False)
            vars_ = self.context.restore(data.get("vars"), reset=False)
            host_info = self.context.restore(data.get("host_info"), reset=False)
            root_key = data.get("root_key", "") or ""
            last_item = data.get("last_item", False)
            return AnsibleRunContext(
                sequence=sequence,
                root_key=str(root_key),
                parent=parent,
                ram_client=ram_client,
                scan_metadata=scan_metadata or {},
                current=current,
                _i=0,
                last_item=bool(last_item),
                vars=vars_,
                host_info=host_info,
            )

    jsonpickle.handlers.registry.register(RunTargetList, RunTargetListHandler)
    jsonpickle.handlers.registry.register(AnsibleRunContext, AnsibleRunContextHandler)
