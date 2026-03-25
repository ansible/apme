"""M029: Inventory scripts must include _meta.hostvars (enforced in 2.23).

Detection scope is limited: this rule fires when it sees an inventory
script plugin task (script-based inventory) without evidence of _meta.
Full detection requires running the script, so this is informational.
"""

from dataclasses import dataclass

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    Severity,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class InventoryScriptMissingMetaRule(Rule):
    """Informational rule about inventory script _meta requirement.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M029"
    description: str = "Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)"
    enabled: bool = False
    name: str = "InventoryScriptMissingMeta"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, ctx: AnsibleRunContext) -> bool:
        """This rule is disabled by default (informational).

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            False; the rule is disabled by default and does not evaluate contexts.

        """
        return False

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """No-op: informational rule, not statically detectable.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            None; static detection is not implemented for this rule.

        """
        return None
