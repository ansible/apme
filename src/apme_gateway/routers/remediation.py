"""Remediation proposal queue — list, accept, reject."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.deps import get_db
from apme_gateway.models.schemas import ProposalListOut, ProposalOut
from apme_gateway.models.tables import RemediationProposal

router = APIRouter(prefix="/api/v1", tags=["remediation"])


@router.get("/remediation/queue", response_model=ProposalListOut)
async def list_proposals(
    status: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> ProposalListOut:
    """List remediation proposals, optionally filtered by status."""
    q = select(RemediationProposal)
    if status:
        q = q.where(RemediationProposal.status == status)
    q = q.order_by(RemediationProposal.tier)
    result = await session.execute(q)
    rows = list(result.scalars().all())
    items = [
        ProposalOut(
            id=p.id,
            scan_id=p.scan_id,
            violation_id=p.violation_id,
            tier=p.tier,
            status=p.status,
            diff=p.diff,
            proposed_by=p.proposed_by,
        )
        for p in rows
    ]
    return ProposalListOut(items=items, total=len(items))


@router.post("/remediation/{proposal_id}/accept", response_model=ProposalOut)
async def accept_proposal(
    proposal_id: str,
    session: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Accept a remediation proposal."""
    proposal = await session.get(RemediationProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal.status = "accepted"
    await session.commit()
    await session.refresh(proposal)
    return ProposalOut(
        id=proposal.id,
        scan_id=proposal.scan_id,
        violation_id=proposal.violation_id,
        tier=proposal.tier,
        status=proposal.status,
        diff=proposal.diff,
        proposed_by=proposal.proposed_by,
    )


@router.post("/remediation/{proposal_id}/reject", response_model=ProposalOut)
async def reject_proposal(
    proposal_id: str,
    session: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Reject a remediation proposal."""
    proposal = await session.get(RemediationProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal.status = "rejected"
    await session.commit()
    await session.refresh(proposal)
    return ProposalOut(
        id=proposal.id,
        scan_id=proposal.scan_id,
        violation_id=proposal.violation_id,
        tier=proposal.tier,
        status=proposal.status,
        diff=proposal.diff,
        proposed_by=proposal.proposed_by,
    )
