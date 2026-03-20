"""Rule catalog endpoints: list all rules, get rule detail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apme_gateway.models.schemas import RuleListOut, RuleOut
from apme_gateway.services.rule_catalog import get_rule, load_rules

router = APIRouter(prefix="/api/v1", tags=["rules"])


@router.get("/rules", response_model=RuleListOut)
async def list_rules() -> RuleListOut:
    """List all rules from the rule catalog."""
    rules = load_rules()
    items = [
        RuleOut(
            rule_id=r.rule_id,
            description=r.description,
            level=r.level,
            validator=r.validator,
            fixable=r.fixable,
            scope=r.scope,
            tags=r.tags,
        )
        for r in rules
    ]
    return RuleListOut(rules=items, total=len(items))


@router.get("/rules/{rule_id}", response_model=RuleOut)
async def get_rule_detail(rule_id: str) -> RuleOut:
    """Get detail for a single rule."""
    r = get_rule(rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return RuleOut(
        rule_id=r.rule_id,
        description=r.description,
        level=r.level,
        validator=r.validator,
        fixable=r.fixable,
        scope=r.scope,
        tags=r.tags,
    )
