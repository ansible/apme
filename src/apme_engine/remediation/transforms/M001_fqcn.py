"""M001/M003: Rewrite short module names to FQCN.

M001 violations carry ``resolved_fqcn`` from ansible-core's plugin loader.
Falls back to a static builtin map when resolved_fqcn is not available.
"""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import (
    get_module_key,
    rename_key,
    violation_line_to_int,
)

_BUILTIN_FQCN: dict[str, str] = {
    "debug": "ansible.builtin.debug",
    "command": "ansible.builtin.command",
    "shell": "ansible.builtin.shell",
    "raw": "ansible.builtin.raw",
    "script": "ansible.builtin.script",
    "copy": "ansible.builtin.copy",
    "file": "ansible.builtin.file",
    "template": "ansible.builtin.template",
    "yum": "ansible.builtin.yum",
    "apt": "ansible.builtin.apt",
    "pip": "ansible.builtin.pip",
    "service": "ansible.builtin.service",
    "systemd": "ansible.builtin.systemd",
    "user": "ansible.builtin.user",
    "group": "ansible.builtin.group",
    "cron": "ansible.builtin.cron",
    "git": "ansible.builtin.git",
    "get_url": "ansible.builtin.get_url",
    "uri": "ansible.builtin.uri",
    "unarchive": "ansible.builtin.unarchive",
    "lineinfile": "ansible.builtin.lineinfile",
    "replace": "ansible.builtin.replace",
    "blockinfile": "ansible.builtin.blockinfile",
    "stat": "ansible.builtin.stat",
    "find": "ansible.builtin.find",
    "fetch": "ansible.builtin.fetch",
    "synchronize": "ansible.builtin.synchronize",
    "package": "ansible.builtin.package",
    "dnf": "ansible.builtin.dnf",
    "setup": "ansible.builtin.setup",
    "ping": "ansible.builtin.ping",
    "assert": "ansible.builtin.assert",
    "fail": "ansible.builtin.fail",
    "set_fact": "ansible.builtin.set_fact",
    "pause": "ansible.builtin.pause",
    "wait_for": "ansible.builtin.wait_for",
    "wait_for_connection": "ansible.builtin.wait_for_connection",
    "include_tasks": "ansible.builtin.include_tasks",
    "import_tasks": "ansible.builtin.import_tasks",
    "include_role": "ansible.builtin.include_role",
    "import_role": "ansible.builtin.import_role",
    "include_vars": "ansible.builtin.include_vars",
    "add_host": "ansible.builtin.add_host",
    "group_by": "ansible.builtin.group_by",
    "hostname": "ansible.builtin.hostname",
    "iptables": "ansible.builtin.iptables",
    "known_hosts": "ansible.builtin.known_hosts",
    "mount": "ansible.builtin.mount",
    "reboot": "ansible.builtin.reboot",
    "tempfile": "ansible.builtin.tempfile",
    "meta": "ansible.builtin.meta",
    "async_status": "ansible.builtin.async_status",
    "gather_facts": "ansible.builtin.gather_facts",
    "package_facts": "ansible.builtin.package_facts",
    "slurp": "ansible.builtin.slurp",
}


def _resolve_fqcn(violation: ViolationDict, current_key: str) -> str | None:
    """Get the target FQCN from the violation or fall back to the static map.

    Args:
        violation: Violation dict (may have resolved_fqcn from ansible-core).
        current_key: Current short module name.

    Returns:
        FQCN string, or None if not resolvable.
    """
    fqcn = violation.get("resolved_fqcn")
    if fqcn is not None and str(fqcn) != current_key:
        return str(fqcn)
    return _BUILTIN_FQCN.get(current_key)


def fix_fqcn(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Rename a short module name to its FQCN.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line and optional resolved_fqcn.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    module_key = get_module_key(task)
    if module_key is None:
        return False

    fqcn = _resolve_fqcn(violation, module_key)
    if fqcn is None or fqcn == module_key:
        return False

    rename_key(task, module_key, fqcn)
    return True
