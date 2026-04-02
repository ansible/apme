"""Auto-register all transforms into a default registry."""

from __future__ import annotations

from apme_engine.remediation.registry import TransformRegistry
from apme_engine.remediation.transforms.L007_shell_to_command import fix_shell_to_command
from apme_engine.remediation.transforms.L008_local_action import fix_local_action
from apme_engine.remediation.transforms.L009_empty_string import fix_empty_string
from apme_engine.remediation.transforms.L010_ignore_errors import fix_ignore_errors
from apme_engine.remediation.transforms.L011_literal_bool import fix_literal_bool
from apme_engine.remediation.transforms.L012_latest import fix_latest
from apme_engine.remediation.transforms.L013_changed_when import fix_changed_when
from apme_engine.remediation.transforms.L015_jinja_when import fix_jinja_when
from apme_engine.remediation.transforms.L018_become import fix_become
from apme_engine.remediation.transforms.L020_octal_mode import fix_octal_mode
from apme_engine.remediation.transforms.L021_missing_mode import fix_missing_mode
from apme_engine.remediation.transforms.L022_pipefail import fix_pipefail
from apme_engine.remediation.transforms.L025_name_casing import fix_name_casing
from apme_engine.remediation.transforms.L043_bare_vars import fix_bare_vars
from apme_engine.remediation.transforms.L046_no_free_form import fix_free_form
from apme_engine.remediation.transforms.M001_fqcn import fix_fqcn
from apme_engine.remediation.transforms.M006_become_unreachable import fix_become_unreachable
from apme_engine.remediation.transforms.M008_bare_include import fix_bare_include
from apme_engine.remediation.transforms.M009_with_to_loop import fix_with_to_loop


def build_default_registry() -> TransformRegistry:
    """Create a registry with all built-in transforms.

    Returns:
        TransformRegistry populated with L/M rule transforms.
    """
    reg = TransformRegistry()

    # Node transforms (CommentedMap task, used via apply_structured find_task wrapper)
    reg.register("L007", node=fix_shell_to_command)
    reg.register("L008", node=fix_local_action)
    reg.register("L009", node=fix_empty_string)
    reg.register("L010", node=fix_ignore_errors)
    reg.register("L011", node=fix_literal_bool)
    reg.register("L012", node=fix_latest)
    reg.register("L013", node=fix_changed_when)
    reg.register("L015", node=fix_jinja_when)
    reg.register("L018", node=fix_become)
    reg.register("L021", node=fix_missing_mode)
    reg.register("L022", node=fix_pipefail)
    reg.register("L025", node=fix_name_casing)
    reg.register("L043", node=fix_bare_vars)
    reg.register("L046", node=fix_free_form)

    # L020 operates on raw lines (YAML 1.1 octal ambiguity) — legacy string path
    reg.register("L020", fix_octal_mode)

    # Ansible validator rules (carry resolved_fqcn from ansible-core)
    # M001-M004 all report FQCN violations, so the same fix applies
    reg.register("M001", node=fix_fqcn)
    reg.register("M002", node=fix_fqcn)
    reg.register("M003", node=fix_fqcn)
    reg.register("M004", node=fix_fqcn)

    # OPA L002/L005 and native L026 also detect non-FQCN; reuse the same fixer
    reg.register("L002", node=fix_fqcn)
    reg.register("L005", node=fix_fqcn)
    reg.register("L026", node=fix_fqcn)

    # Migration rules
    reg.register("M006", node=fix_become_unreachable)
    reg.register("M008", node=fix_bare_include)
    reg.register("M009", node=fix_with_to_loop)

    return reg
