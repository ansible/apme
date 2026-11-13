"""Stable diagnostic identities shared by output deduplication and the ledger."""

from __future__ import annotations

import hashlib
import json
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
    """Distinguish argspec and Molecule diagnostics sharing a source and rule.

    L058/L059 can report coverage warnings and several argument errors for
    different invocations of one task. Severity overrides and module spelling
    changes during FQCN remediation do not change the diagnostic's identity.
    Molecule L115-L119 findings distinguish YAML paths and diagnostic messages,
    including separate keys in one flow mapping. Other rules retain their
    existing per-node, per-rule identity.

    Args:
        violation: Finding whose rule and diagnostic identify its kind.

    Returns:
        Stable diagnostic digest, or an empty legacy discriminator.
    """
    rule_id = normalize_rule_id(str(violation.get("rule_id", "")))
    if rule_id in {"L115", "L116", "L117", "L118", "L119"}:
        identity = [str(violation.get("path") or ""), str(violation.get("message") or "")]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
    if rule_id not in {"L058", "L059"}:
        return ""
    message = str(violation.get("message") or "")
    diagnostic = _ARGSPEC_MODULE.sub(r"\g<prefix><module>\g<suffix>", message)
    return hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
