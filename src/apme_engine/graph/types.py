"""Shared type definitions for the graph analysis layer (ADR-059).

Extracted from ``apme_engine.engine.models`` so that ``graph/`` has no
dependency on the ARI model hierarchy.  ``engine.models`` re-exports
everything defined here for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apme_engine.graph.severity import Severity as Severity

# ---------------------------------------------------------------------------
# YAML type aliases
# ---------------------------------------------------------------------------

YAMLScalar = str | int | float | bool | None
YAMLValue = YAMLScalar | list["YAMLValue"] | dict[str, "YAMLValue"]
YAMLDict = dict[str, YAMLValue]
YAMLList = list[YAMLValue]

# Violation dicts from validators (rule_id, level, message, file, line, path, etc.)
ViolationDict = dict[str, str | int | list[int] | bool | None]


# ---------------------------------------------------------------------------
# Rule scope & remediation enums
# ---------------------------------------------------------------------------


class RuleScope(str, Enum):
    """Structural scope at which a rule operates.

    Attributes:
        TASK: Individual task — AI can propose fixes.
        BLOCK: Block structure — AI may help.
        PLAY: Play header, vars, become — manual review.
        PLAYBOOK: Multi-play structure — manual review.
        ROLE: Role-level (meta, defaults) — manual review.
        INVENTORY: Inventory/group_vars — manual review.
        COLLECTION: Cross-repo scope — manual review.
    """

    TASK = "task"
    BLOCK = "block"
    PLAY = "play"
    PLAYBOOK = "playbook"
    ROLE = "role"
    INVENTORY = "inventory"
    COLLECTION = "collection"


class RemediationClass(str, Enum):
    """Classification of remediation complexity for violations.

    Attributes:
        AUTO_FIXABLE: Tier 1 — deterministic transform exists.
        AI_CANDIDATE: Tier 2 — AI can propose a fix.
        MANUAL_REVIEW: Tier 3 — requires human judgment.
    """

    AUTO_FIXABLE = "auto-fixable"
    AI_CANDIDATE = "ai-candidate"
    MANUAL_REVIEW = "manual-review"


class RemediationResolution(str, Enum):
    """What happened during remediation of a specific finding.

    Attributes:
        UNRESOLVED: Initial state at scan time.
        TRANSFORM_FAILED: Deterministic transform returned applied=False.
        OSCILLATION: Convergence loop detected oscillation.
        AI_PROPOSED: AI proposed a fix (pending validation).
        AI_FAILED: AI call failed or returned no result.
        AI_ABSTAINED: AI attempted but could not produce a fix.
        AI_LOW_CONFIDENCE: AI returned a low-confidence proposal.
        USER_REJECTED: User rejected the proposed fix.
        NEEDS_CROSS_FILE: Requires cross-file context (deferred to MCP tool).
        MANUAL: Requires manual review (play-level or structural issue).
        INFORMATIONAL: Report-only rule (severity=none), no fix needed.
    """

    UNRESOLVED = "unresolved"
    TRANSFORM_FAILED = "transform-failed"
    OSCILLATION = "oscillation"
    AI_PROPOSED = "ai-proposed"
    AI_FAILED = "ai-failed"
    AI_ABSTAINED = "ai-abstained"
    AI_LOW_CONFIDENCE = "ai-low-confidence"
    USER_REJECTED = "user-rejected"
    NEEDS_CROSS_FILE = "needs-cross-file"
    MANUAL = "manual"
    INFORMATIONAL = "informational"


# ---------------------------------------------------------------------------
# Executable types & rule tags
# ---------------------------------------------------------------------------


class ExecutableType:
    """Executable target type: Module, Role, or TaskFile.

    Attributes:
        MODULE_TYPE: Module executable type.
        ROLE_TYPE: Role executable type.
        TASKFILE_TYPE: Taskfile executable type.
    """

    MODULE_TYPE = "Module"
    ROLE_TYPE = "Role"
    TASKFILE_TYPE = "TaskFile"


class RuleTag:
    """Rule tags for categorization (network, command, dependency, etc.).

    Attributes:
        NETWORK: Network-related rule.
        COMMAND: Command-related rule.
        DEPENDENCY: Dependency-related rule.
        SYSTEM: System-related rule.
        PACKAGE: Package-related rule.
        CODING: Coding-related rule.
        VARIABLE: Variable-related rule.
        QUALITY: Quality-related rule.
        DEBUG: Debug-related rule.
        AAP: AAP platform-specific rule.
        PORTABILITY: Portability-related rule.
        SECURITY: Security-related rule.
    """

    NETWORK = "network"
    COMMAND = "command"
    DEPENDENCY = "dependency"
    SYSTEM = "system"
    PACKAGE = "package"
    CODING = "coding"
    VARIABLE = "variable"
    QUALITY = "quality"
    DEBUG = "debug"
    AAP = "aap"
    PORTABILITY = "portability"
    SECURITY = "security"


# ---------------------------------------------------------------------------
# Rule metadata
# ---------------------------------------------------------------------------


@dataclass
class RuleMetadata:
    """Metadata for a rule (id, description, name, version, severity, tags, scope).

    Attributes:
        rule_id: Unique rule identifier.
        description: Rule description.
        name: Rule name.
        version: Version string.
        commit_id: Commit ID.
        severity: Severity level.
        tags: Tags for categorization.
        scope: Structural scope at which the rule operates.
    """

    rule_id: str = ""
    description: str = ""
    name: str = ""

    version: str = ""
    commit_id: str = ""
    severity: str | Severity = Severity.MEDIUM
    tags: tuple[str, ...] = ()
    scope: str = RuleScope.TASK

    def get_metadata(self) -> RuleMetadata:
        """Return a standalone RuleMetadata copy of this rule's metadata.

        Returns:
            RuleMetadata with rule_id, description, name, version, commit_id,
            severity, tags, scope.
        """
        return RuleMetadata(
            rule_id=self.rule_id,
            description=self.description,
            name=self.name,
            version=self.version,
            commit_id=self.commit_id,
            severity=self.severity,
            tags=self.tags,
            scope=self.scope,
        )
