"""Rule catalog endpoints — list and detail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apme_gateway.models.schemas import RuleDetail, RuleOut
from apme_gateway.services.rule_catalog import get_rule, list_rules

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
async def get_rules(
    validator: str | None = None,
    has_fixer: bool | None = None,
):
    rules = list_rules()
    if validator:
        rules = [r for r in rules if r.validator == validator]
    if has_fixer is not None:
        rules = [r for r in rules if r.has_fixer == has_fixer]
    return [
        RuleOut(
            rule_id=r.rule_id,
            validator=r.validator,
            description=r.description,
            has_fixer=r.has_fixer,
        )
        for r in rules
    ]


@router.get("/{rule_id}", response_model=RuleDetail)
async def get_rule_detail(rule_id: str):
    rule = get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return RuleDetail(
        rule_id=rule.rule_id,
        validator=rule.validator,
        description=rule.description,
        has_fixer=rule.has_fixer,
        examples=rule.examples,
    )
