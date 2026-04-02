"""Static default severity assignments for all rules (ADR-043).

This is the authoritative, single-source mapping of rule_id -> Severity.
Individual rule classes and Rego policies reference this table rather than
carrying their own severity.  The criteria follow the ADR-043 decision tree:

  1. Security vulnerability?         -> CRITICAL
  2. Runtime breakage today?          -> ERROR
  3. Imminent breakage / risky?       -> HIGH
  4. Probably a bug / anti-pattern?   -> MEDIUM
  5. Best practice / convention?      -> LOW
  6. Informational / style?           -> INFO
"""

from __future__ import annotations

from enum import IntEnum


class Severity(IntEnum):
    """Impact-based severity levels (ADR-043).

    Numeric values enable threshold gating (e.g. fail CI if any >= ERROR).

    Attributes:
        UNSPECIFIED: Default/unset value.
        INFO: Advisory — informational, style, or "consider this".
        LOW: Best practice — works but violates maintainability conventions.
        MEDIUM: Correctness smell — likely a bug or anti-pattern.
        HIGH: Behavioral risk — may cause incorrect behavior or imminent breakage.
        ERROR: Runtime breakage — will fail when executed.
        CRITICAL: Security threat — credentials exposed, secrets in code.
    """

    UNSPECIFIED = 0
    INFO = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    ERROR = 5
    CRITICAL = 6


SEVERITY_DEFAULTS: dict[str, Severity] = {
    # ── Native rules ────────────────────────────────────────────────────
    # Low: FQCN / naming / best-practice
    "L039": Severity.LOW,
    "L050": Severity.LOW,
    "L026": Severity.LOW,
    "L030": Severity.LOW,
    "L027": Severity.LOW,
    "L032": Severity.LOW,
    "L033": Severity.LOW,
    "L034": Severity.LOW,
    "L035": Severity.LOW,
    "L036": Severity.LOW,
    "L041": Severity.LOW,
    "L043": Severity.LOW,
    "L044": Severity.LOW,
    "L046": Severity.LOW,
    "L048": Severity.LOW,
    "L049": Severity.LOW,
    "L051": Severity.LOW,
    "L052": Severity.LOW,
    "L053": Severity.LOW,
    "L054": Severity.LOW,
    "L055": Severity.LOW,
    "L074": Severity.LOW,
    "L075": Severity.LOW,
    "L076": Severity.LOW,
    "L077": Severity.LOW,
    "L078": Severity.LOW,
    "L079": Severity.LOW,
    "L080": Severity.LOW,
    "L081": Severity.LOW,
    "L082": Severity.LOW,
    "L083": Severity.LOW,
    "L084": Severity.LOW,
    "L085": Severity.LOW,
    "L086": Severity.LOW,
    "L087": Severity.LOW,
    "L088": Severity.LOW,
    "L089": Severity.LOW,
    "L090": Severity.LOW,
    "L091": Severity.LOW,
    "L092": Severity.LOW,
    "L093": Severity.LOW,
    "L094": Severity.LOW,
    "L097": Severity.LOW,
    "L103": Severity.LOW,
    "L104": Severity.LOW,
    "L105": Severity.LOW,
    "M019": Severity.LOW,
    "M020": Severity.LOW,
    "M027": Severity.LOW,
    # Info: advisory / style / reports
    "L040": Severity.INFO,
    "L042": Severity.INFO,
    "L056": Severity.INFO,
    "L060": Severity.INFO,
    "L073": Severity.LOW,
    "L099": Severity.INFO,
    "R117": Severity.INFO,
    "R401": Severity.INFO,
    # Medium: correctness smell / probable bug
    "L037": Severity.MEDIUM,
    "L038": Severity.MEDIUM,
    "L047": Severity.HIGH,
    "L100": Severity.MEDIUM,
    "L101": Severity.MEDIUM,
    "L102": Severity.MEDIUM,
    "M014": Severity.MEDIUM,
    "M015": Severity.MEDIUM,
    "M022": Severity.MEDIUM,
    "M026": Severity.MEDIUM,
    "M030": Severity.MEDIUM,
    "R101": Severity.MEDIUM,
    "R103": Severity.MEDIUM,
    "R104": Severity.MEDIUM,
    "R105": Severity.MEDIUM,
    "R106": Severity.MEDIUM,
    "R107": Severity.MEDIUM,
    "R108": Severity.MEDIUM,
    "R109": Severity.MEDIUM,
    "R111": Severity.MEDIUM,
    "R112": Severity.MEDIUM,
    "R113": Severity.MEDIUM,
    "R114": Severity.MEDIUM,
    "R115": Severity.MEDIUM,
    # High: deprecated / imminent breakage / insecure
    "L095": Severity.ERROR,
    "L096": Severity.HIGH,
    "L098": Severity.ERROR,
    "M005": Severity.HIGH,
    "M010": Severity.HIGH,
    # Low: best practice / convention
    "L045": Severity.LOW,
    # ── OPA rules ───────────────────────────────────────────────────────
    "L003": Severity.LOW,
    "L004": Severity.HIGH,
    "L005": Severity.LOW,
    "L006": Severity.LOW,
    "L007": Severity.LOW,
    "L008": Severity.LOW,
    "L009": Severity.MEDIUM,
    "L010": Severity.MEDIUM,
    "L011": Severity.LOW,
    "L012": Severity.LOW,
    "L013": Severity.MEDIUM,
    "L014": Severity.LOW,
    "L015": Severity.LOW,
    "L016": Severity.INFO,
    "L017": Severity.LOW,
    "L018": Severity.HIGH,
    "L019": Severity.LOW,
    "L020": Severity.HIGH,
    "L021": Severity.LOW,
    "L022": Severity.LOW,
    "L023": Severity.INFO,
    "L024": Severity.LOW,
    "L025": Severity.LOW,
    "L061": Severity.LOW,
    "L062": Severity.LOW,
    "L063": Severity.LOW,
    "L064": Severity.LOW,
    "L065": Severity.LOW,
    "L066": Severity.LOW,
    "L067": Severity.INFO,
    "L068": Severity.INFO,
    "L069": Severity.INFO,
    "L070": Severity.INFO,
    "L071": Severity.INFO,
    "L072": Severity.INFO,
    "M006": Severity.HIGH,
    "M008": Severity.HIGH,
    "M009": Severity.HIGH,
    "M011": Severity.HIGH,
    "M016": Severity.HIGH,
    "M017": Severity.HIGH,
    "M018": Severity.HIGH,
    "M021": Severity.HIGH,
    "M023": Severity.HIGH,
    "M024": Severity.HIGH,
    "M025": Severity.HIGH,
    "M028": Severity.HIGH,
    "R118": Severity.INFO,
    # ── Ansible rules ───────────────────────────────────────────────────
    "L057": Severity.ERROR,
    "L058": Severity.ERROR,
    "L059": Severity.ERROR,
    "M001": Severity.HIGH,
    "M002": Severity.HIGH,
    "M003": Severity.HIGH,
    "M004": Severity.ERROR,
    # Ansible validator P-rules (runtime validation failures)
    "P001": Severity.ERROR,
    "P002": Severity.ERROR,
    "P003": Severity.ERROR,
    "P004": Severity.ERROR,
    # ── Gitleaks (all secrets are critical) ─────────────────────────────
    # SEC:* rules get CRITICAL; handled via prefix match in get_severity()
    # ── Infrastructure / meta rules ─────────────────────────────────────
    "INFRA-001": Severity.ERROR,
    "INFRA-002": Severity.ERROR,
}


# Proto enum value -> Severity
_PROTO_TO_SEVERITY: dict[int, Severity] = {
    0: Severity.UNSPECIFIED,
    1: Severity.INFO,
    2: Severity.LOW,
    3: Severity.MEDIUM,
    4: Severity.HIGH,
    5: Severity.ERROR,
    6: Severity.CRITICAL,
}

# Severity -> human-readable label (lowercase, for dict/CLI output)
SEVERITY_LABELS: dict[Severity, str] = {
    Severity.UNSPECIFIED: "unspecified",
    Severity.INFO: "info",
    Severity.LOW: "low",
    Severity.MEDIUM: "medium",
    Severity.HIGH: "high",
    Severity.ERROR: "error",
    Severity.CRITICAL: "critical",
}

# Reverse: label -> Severity
_LABEL_TO_SEVERITY: dict[str, Severity] = {v: k for k, v in SEVERITY_LABELS.items()}


def get_severity(rule_id: str) -> Severity:
    """Look up the default severity for a rule ID.

    Falls back to MEDIUM for unknown rules.  SEC: prefix rules always
    return CRITICAL.

    Args:
        rule_id: Rule identifier (e.g. "L021", "SEC:generic-api-key").

    Returns:
        The default Severity for the rule.
    """
    if rule_id.startswith("SEC:"):
        return Severity.CRITICAL
    return SEVERITY_DEFAULTS.get(rule_id, Severity.MEDIUM)


def severity_from_label(label: str) -> Severity:
    """Convert a human-readable label to Severity enum.

    Args:
        label: Lowercase label like "high", "error", "critical".

    Returns:
        Corresponding Severity, or MEDIUM for unknown labels.
    """
    return _LABEL_TO_SEVERITY.get(label.lower(), Severity.MEDIUM)


def severity_to_label(sev: Severity) -> str:
    """Convert a Severity enum to its human-readable label.

    Args:
        sev: Severity enum value.

    Returns:
        Lowercase label string.
    """
    return SEVERITY_LABELS.get(sev, "medium")


def severity_from_proto(proto_val: int) -> Severity:
    """Convert a proto Severity enum int to the Python Severity.

    Unknown/future enum values fall back to MEDIUM so they remain
    distinguishable from an explicitly unset severity (UNSPECIFIED).

    Args:
        proto_val: Integer value from the proto Severity enum.

    Returns:
        Corresponding Severity.
    """
    return _PROTO_TO_SEVERITY.get(proto_val, Severity.MEDIUM)


def severity_to_proto(sev: Severity) -> int:
    """Convert a Python Severity to the proto enum int value.

    Args:
        sev: Python Severity enum.

    Returns:
        Integer value for the proto Severity enum.
    """
    return int(sev)
