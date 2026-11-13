"""Stable diagnostic identities shared by output deduplication and the ledger."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from apme_engine.rule_ids import normalize_rule_id

_ARGSPEC_MODULE = re.compile(
    r"^(?P<prefix>Unsupported parameters for |"
    r"Missing required parameter '[^']+' for |"
    r"Choice for parameter '[^']+' of |"
    r"Parameters are mutually exclusive for |"
    r"Parameters must be used together for )"
    r"\S+?(?P<suffix>: .*| is not one of: .*|)$",
    re.DOTALL,
)


def diagnostic_discriminator(violation: Mapping[str, object]) -> str:
    """Distinguish argspec diagnostics sharing a source task and rule.

    L058/L059 can report coverage warnings and several argument errors for
    different invocations of one task. Severity overrides and module spelling
    changes during FQCN remediation do not change the diagnostic's identity.
    Other rules retain their existing per-node, per-rule identity.

    Args:
        violation: Finding whose rule and argument diagnostic identify its kind.

    Returns:
        Stable digest for argspec diagnostics, or an empty legacy discriminator.
    """
    if normalize_rule_id(str(violation.get("rule_id", ""))) not in {"L058", "L059"}:
        return ""
    message = str(violation.get("message") or "")
    diagnostic = _ARGSPEC_MODULE.sub(r"\g<prefix><module>\g<suffix>", message)
    return hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
