"""FQCN-to-semantic-field mapping for risk-oriented GraphRules.

Each ``RiskProfile`` captures the *risk type* a module introduces
and maps its **module_options** keys to semantic roles (``src``,
``dest``, ``cmd``, ``pkg``, ``key``, etc.).  Rules use
``get_risk_profile()`` to look up the profile for a node's
declared ``module`` field and then read the relevant fields from
``ContentNode.module_options``.

The mapping is derived from the legacy per-module annotators in
``apme_engine/engine/annotators/ansible.builtin/`` but is kept
as a static table so graph rules have no dependency on that
pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """Semantic field mapping for a single FQCN.

    Attributes:
        risk_type: Category string (``cmd_exec``, ``inbound``, etc.).
        fields: Map of *semantic name* to *module_options key*.
            For fallback chains use a tuple of keys as the value
            via ``field_chain`` instead.
        field_chains: Map of *semantic name* to ordered fallback keys.
            The first non-``None`` value found in ``module_options`` wins.
        method_gate: If set, the profile applies only when the
            ``method`` option is one of these values (uri PUT/POST/PATCH).
    """

    risk_type: str
    fields: dict[str, str] = field(default_factory=dict)
    field_chains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    method_gate: frozenset[str] | None = None


# -- CMD_EXEC ---------------------------------------------------------------

_CMD_EXEC_PROFILE = RiskProfile(
    risk_type="cmd_exec",
    field_chains={"cmd": ("_raw_params", "cmd", "argv")},
)

_CMD_EXEC_EXPECT = RiskProfile(
    risk_type="cmd_exec",
    field_chains={"cmd": ("_raw_params", "command", "cmd", "argv")},
)

# -- INBOUND -----------------------------------------------------------------

_INBOUND_GET_URL = RiskProfile(
    risk_type="inbound",
    fields={"src": "url", "dest": "dest"},
)

_INBOUND_GIT = RiskProfile(
    risk_type="inbound",
    fields={"src": "repo", "dest": "dest"},
)

_INBOUND_SUBVERSION = RiskProfile(
    risk_type="inbound",
    fields={"src": "repo", "dest": "dest"},
)

_INBOUND_UNARCHIVE = RiskProfile(
    risk_type="inbound",
    fields={"src": "src", "dest": "dest"},
)

# -- OUTBOUND ----------------------------------------------------------------

_OUTBOUND_URI = RiskProfile(
    risk_type="outbound",
    fields={"dest": "url", "src": "body"},
    method_gate=frozenset({"PUT", "POST", "PATCH"}),
)

# -- FILE_CHANGE -------------------------------------------------------------

_FILE_CHANGE_PATH_STATE = RiskProfile(
    risk_type="file_change",
    field_chains={"path": ("path", "dest")},
    fields={"state": "state"},
)

_FILE_CHANGE_PATH_SRC = RiskProfile(
    risk_type="file_change",
    field_chains={"path": ("path", "dest")},
    fields={"src": "src", "state": "state"},
)

_FILE_CHANGE_DEST_SRC = RiskProfile(
    risk_type="file_change",
    fields={"path": "dest", "src": "src"},
)

# -- PACKAGE_INSTALL ---------------------------------------------------------

_PKG_INSTALL_YUM_DNF = RiskProfile(
    risk_type="package_install",
    fields={
        "pkg": "name",
        "validate_certs": "validate_certs",
        "disable_gpg_check": "disable_gpg_check",
        "allow_downgrade": "allow_downgrade",
    },
)

_PKG_INSTALL_APT = RiskProfile(
    risk_type="package_install",
    field_chains={"pkg": ("name", "pkg", "deb")},
)

_PKG_INSTALL_PIP = RiskProfile(
    risk_type="package_install",
    field_chains={"pkg": ("name", "requirements")},
)

# -- CONFIG_CHANGE -----------------------------------------------------------

_CONFIG_RPM_KEY = RiskProfile(
    risk_type="config_change",
    fields={"key": "key", "state": "state"},
)

_CONFIG_APT_KEY = RiskProfile(
    risk_type="config_change",
    field_chains={"key": ("url", "data", "keyserver")},
    fields={"state": "state"},
)


# ---------------------------------------------------------------------------
# Master table: FQCN -> RiskProfile
# ---------------------------------------------------------------------------

RISK_PROFILES: dict[str, RiskProfile] = {
    # CMD_EXEC
    "ansible.builtin.command": _CMD_EXEC_PROFILE,
    "ansible.builtin.shell": _CMD_EXEC_PROFILE,
    "ansible.builtin.raw": _CMD_EXEC_PROFILE,
    "ansible.builtin.script": _CMD_EXEC_PROFILE,
    "ansible.builtin.expect": _CMD_EXEC_EXPECT,
    # INBOUND
    "ansible.builtin.get_url": _INBOUND_GET_URL,
    "ansible.builtin.git": _INBOUND_GIT,
    "ansible.builtin.subversion": _INBOUND_SUBVERSION,
    "ansible.builtin.unarchive": _INBOUND_UNARCHIVE,
    # OUTBOUND
    "ansible.builtin.uri": _OUTBOUND_URI,
    # FILE_CHANGE
    "ansible.builtin.file": _FILE_CHANGE_PATH_STATE,
    "ansible.builtin.lineinfile": _FILE_CHANGE_PATH_STATE,
    "ansible.builtin.blockinfile": _FILE_CHANGE_PATH_STATE,
    "ansible.builtin.replace": RiskProfile(
        risk_type="file_change",
        field_chains={"path": ("path", "dest")},
    ),
    "ansible.builtin.template": _FILE_CHANGE_DEST_SRC,
    "ansible.builtin.assemble": _FILE_CHANGE_DEST_SRC,
    "ansible.builtin.copy": _FILE_CHANGE_PATH_SRC,
    # PACKAGE_INSTALL
    "ansible.builtin.yum": _PKG_INSTALL_YUM_DNF,
    "ansible.builtin.dnf": _PKG_INSTALL_YUM_DNF,
    "ansible.builtin.apt": _PKG_INSTALL_APT,
    "ansible.builtin.pip": _PKG_INSTALL_PIP,
    # CONFIG_CHANGE
    "ansible.builtin.rpm_key": _CONFIG_RPM_KEY,
    "ansible.builtin.apt_key": _CONFIG_APT_KEY,
}

_PREFIX = "ansible.builtin."
RISK_PROFILES.update({k.removeprefix(_PREFIX): v for k, v in list(RISK_PROFILES.items()) if k.startswith(_PREFIX)})


def get_risk_profile(module: str) -> RiskProfile | None:
    """Look up the risk profile for a module.

    Accepts both FQCN (``ansible.builtin.copy``) and short
    (``copy``) module names.

    Args:
        module: Declared module name from the task.

    Returns:
        Matching ``RiskProfile``, or ``None`` if the module has
        no risk mapping.
    """
    return RISK_PROFILES.get(module) if module else None


def resolve_field(
    module_options: Mapping[str, object],
    profile: RiskProfile,
    semantic_name: str,
) -> str | None:
    """Extract a semantic field value from module_options.

    Checks ``profile.fields`` for a direct key, then
    ``profile.field_chains`` for a fallback chain.  Returns the
    first non-``None`` string value found, or ``None``.

    Args:
        module_options: The raw module arguments dict.
        profile: Risk profile with field mappings.
        semantic_name: Semantic role name (``src``, ``dest``, etc.).

    Returns:
        The string value, or ``None`` if not present.
    """
    if semantic_name in profile.fields:
        val = module_options.get(profile.fields[semantic_name])
        if val is not None:
            return str(val)
        return None

    chain = profile.field_chains.get(semantic_name)
    if chain:
        for key in chain:
            val = module_options.get(key)
            if val is not None:
                return str(val)
    return None
