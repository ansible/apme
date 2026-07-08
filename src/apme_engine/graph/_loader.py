"""Dynamic class loader for graph rules (ADR-059).

Extracted from ``apme_engine.engine.utils`` so that ``graph/scanner.py``
does not depend on the engine package.
"""

from __future__ import annotations

import os
import sys
import traceback
from importlib.util import module_from_spec, spec_from_file_location
from inspect import isclass


def load_classes_in_dir(
    dir_path: str,
    target_class: type[object],
    base_dir: str = "",
    only_subclass: bool = True,
    fail_on_error: bool = False,
) -> tuple[list[type[object]], list[str]]:
    """Discover and load classes from Python files in a directory.

    Args:
        dir_path: Directory to scan for .py files (excluding _test.py).
        target_class: Only include subclasses of this type if only_subclass.
        base_dir: Optional base path to resolve dir_path.
        only_subclass: If True, only yield subclasses of target_class.
        fail_on_error: If True, re-raise on load errors.

    Returns:
        Tuple of (list of classes, list of error messages).

    Raises:
        ValueError: If dir_path is not found.
    """
    search_path = dir_path
    found = False
    if os.path.exists(search_path):
        found = True
    if not found and base_dir:
        self_path = os.path.abspath(base_dir)
        search_path = os.path.join(os.path.dirname(self_path), dir_path)
        if os.path.exists(search_path):
            found = True

    if not found:
        raise ValueError(f'Path not found "{dir_path}"')

    files = os.listdir(search_path)
    scripts = [os.path.join(search_path, f) for f in files if f.endswith(".py") and not f.endswith("_test.py")]
    classes = []
    errors = []
    for s in scripts:
        try:
            short_module_name = os.path.basename(s)[:-3]
            spec = spec_from_file_location(short_module_name, s)
            if spec is None or spec.loader is None:
                continue
            mod = module_from_spec(spec)
            had_prev = short_module_name in sys.modules
            prev_mod = sys.modules.get(short_module_name)
            sys.modules[short_module_name] = mod
            try:
                spec.loader.exec_module(mod)
            finally:
                if had_prev:
                    sys.modules[short_module_name] = prev_mod  # type: ignore[assignment]
                else:
                    sys.modules.pop(short_module_name, None)
            for k in mod.__dict__:
                cls = getattr(mod, k)
                if not callable(cls):
                    continue
                if not isclass(cls):
                    continue
                if not issubclass(cls, target_class):
                    continue
                if only_subclass and cls == target_class:
                    continue
                classes.append(cls)
        except Exception as err:
            exc = traceback.format_exc()
            msg = f"failed to load a rule module {s}: {exc}"
            if fail_on_error:
                raise ValueError(msg) from err
            else:
                errors.append(msg)
    return classes, errors
