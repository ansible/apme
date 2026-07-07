"""Static ansible-core version applicability for rules (ADR-057).

This is the authoritative mapping of rule_id -> PEP 440 version specifier
indicating which ansible-core versions the rule applies to.  Follows the
same central-table pattern as ``severity_defaults.py`` (ADR-043).

Specifiers use ``packaging.specifiers.SpecifierSet`` and are validated at
import time — a malformed specifier raises ``InvalidSpecifier`` immediately.

Only version-sensitive rules appear here (primarily M-series).  Rules
without an entry are version-agnostic (L, R, P, SEC, A categories).
"""

from __future__ import annotations

import packaging.version
from packaging.specifiers import SpecifierSet
from packaging.version import Version

VERSION_DEFAULTS: dict[str, SpecifierSet] = {
    # ── Ansible validator (introspection-based, version-aware via venv) ──
    "M001": SpecifierSet(">=2.9"),  # FQCN resolution
    "M002": SpecifierSet(">=2.9"),  # deprecated module
    "M003": SpecifierSet(">=2.9"),  # module redirect
    "M004": SpecifierSet(">=2.9"),  # removed module
    # ── ansible-core 2.18 changes ────────────────────────────────────────
    "M010": SpecifierSet(">=2.18"),  # Python 2 interpreter dropped
    # ── ansible-core 2.19 changes ────────────────────────────────────────
    "M005": SpecifierSet(">=2.19"),  # data tagging trust model inversion
    "M006": SpecifierSet(">=2.19"),  # become timeout unreachable
    "M008": SpecifierSet(">=2.19"),  # bare include removed
    "M009": SpecifierSet(">=2.19"),  # with_* loop deprecation
    "M011": SpecifierSet(">=2.19"),  # network collection incompatibilities
    # ── Deprecation pipeline (2.21–2.24 removals) ────────────────────────
    "M018": SpecifierSet(">=2.21"),  # removal version 2.21
    "M023": SpecifierSet(">=2.22"),  # follow_redirects string deprecated
    "M014": SpecifierSet(">=2.24"),  # top-level fact variables removed
    "M015": SpecifierSet(">=2.23"),  # play_hosts magic variable removed
    "M016": SpecifierSet(">=2.23"),  # empty when: deprecated
    "M017": SpecifierSet(">=2.23"),  # action: mapping deprecated
    "M019": SpecifierSet(">=2.23"),  # !!omap / !!pairs YAML tags removed
    "M020": SpecifierSet(">=2.23"),  # vault encrypted tag removed
    "M021": SpecifierSet(">=2.23"),  # empty args: deprecated
    "M022": SpecifierSet(">=2.23"),  # tree/oneline callback plugins removed
    "M024": SpecifierSet(">=2.24"),  # removal version 2.24
    "M025": SpecifierSet(">=2.23"),  # removal version 2.23
    "M026": SpecifierSet(">=2.23"),  # invalid inventory variable names
    "M027": SpecifierSet(">=2.23"),  # legacy kv merged with args deprecated
    "M028": SpecifierSet(">=2.23"),  # first_found auto-splitting deprecated
    "M029": SpecifierSet(">=2.23"),  # inventory script missing _meta
    "M030": SpecifierSet(">=2.23"),  # broken conditional expressions
}

_STR_CACHE: dict[str, str] = {rule_id: str(spec) for rule_id, spec in VERSION_DEFAULTS.items()}


def get_version_specifier(rule_id: str) -> SpecifierSet | None:
    """Look up the ansible-core version specifier for a rule.

    Args:
        rule_id: Rule identifier (e.g. ``"M014"``).

    Returns:
        The ``SpecifierSet`` for the rule, or ``None`` if version-agnostic.
    """
    return VERSION_DEFAULTS.get(rule_id)


def get_version_spec_str(rule_id: str) -> str:
    """Look up the ansible-core version specifier string for a rule.

    Suitable for proto fields and wire formats.

    Args:
        rule_id: Rule identifier (e.g. ``"M014"``).

    Returns:
        PEP 440 specifier string (e.g. ``">=2.24"``), or empty string
        if the rule is version-agnostic.
    """
    return _STR_CACHE.get(rule_id, "")


def is_applicable(rule_id: str, ansible_core_version: str) -> bool:
    """Check whether a rule applies to a given ansible-core version.

    Args:
        rule_id: Rule identifier (e.g. ``"M014"``).
        ansible_core_version: Target version string (e.g. ``"2.19"``).

    Returns:
        ``True`` if the rule applies (or has no version constraint),
        ``False`` if the rule is not relevant for the given version.
    """
    spec = VERSION_DEFAULTS.get(rule_id)
    if spec is None:
        return True
    if not ansible_core_version:
        return True
    try:
        return spec.contains(Version(ansible_core_version))
    except packaging.version.InvalidVersion:
        return True
