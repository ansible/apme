"""Auto-register all transforms into a default registry."""

from __future__ import annotations

from apme_engine.remediation.registry import TransformRegistry
from apme_engine.remediation.transforms.L007_shell_to_command import fix_shell_to_command
from apme_engine.remediation.transforms.L008_local_action import fix_local_action
from apme_engine.remediation.transforms.L009_empty_string import fix_empty_string
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

    # Structured transforms (parse-once, modify in-place)
    reg.register("L007", structured=fix_shell_to_command)
    reg.register("L008", structured=fix_local_action)
    reg.register("L009", structured=fix_empty_string)
    reg.register("L011", structured=fix_literal_bool)
    reg.register("L012", structured=fix_latest)
    reg.register("L013", structured=fix_changed_when)
    reg.register("L015", structured=fix_jinja_when)
    reg.register("L018", structured=fix_become)
    reg.register("L021", structured=fix_missing_mode)
    reg.register("L022", structured=fix_pipefail)
    reg.register("L025", structured=fix_name_casing)
    reg.register("L043", structured=fix_bare_vars)
    reg.register("L046", structured=fix_free_form)

    # L020 operates on raw lines (YAML 1.1 octal ambiguity) — legacy string path
    reg.register("L020", fix_octal_mode)

    # Ansible validator rules (carry resolved_fqcn from ansible-core)
    # M001-M004 all report FQCN violations, so the same fix applies
    reg.register("M001", structured=fix_fqcn)
    reg.register("M002", structured=fix_fqcn)
    reg.register("M003", structured=fix_fqcn)
    reg.register("M004", structured=fix_fqcn)

    # OPA L005 also detects non-FQCN; reuse the same fixer (falls back to static map)
    reg.register("L005", structured=fix_fqcn)

    # Migration rules
    reg.register("M006", structured=fix_become_unreachable)
    reg.register("M008", structured=fix_bare_include)
    reg.register("M009", structured=fix_with_to_loop)

    return reg
