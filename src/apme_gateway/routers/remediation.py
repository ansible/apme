"""Remediation queue endpoints — list, accept, reject AI-proposed fixes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.database import get_session
from apme_gateway.models.database import RemediationProposal
from apme_gateway.models.schemas import RemediationAction, RemediationOut

router = APIRouter(prefix="/remediation", tags=["remediation"])


def _proposal_to_out(p: RemediationProposal) -> RemediationOut:
    return RemediationOut(
        id=p.id,
        scan_id=p.scan_id,
        violation_id=p.violation_id,
        tier=p.tier,
        status=p.status,
        diff=p.diff,
        proposed_by=p.proposed_by,
        reviewed_by=p.reviewed_by,
        reviewed_at=p.reviewed_at,
    )


@router.get("/queue", response_model=list[RemediationOut])
async def list_queue(
    scan_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(RemediationProposal).where(RemediationProposal.status == "pending")
    if scan_id:
        q = q.where(RemediationProposal.scan_id == scan_id)
    q = q.order_by(RemediationProposal.id)
    result = await session.execute(q)
    return [_proposal_to_out(p) for p in result.scalars().all()]


async def _update_proposal(
    proposal_id: str,
    new_status: str,
    body: RemediationAction,
    session: AsyncSession,
) -> RemediationOut:
    proposal = await session.get(RemediationProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal already {proposal.status}",
        )
    proposal.status = new_status
    proposal.reviewed_by = body.reviewed_by or None
    proposal.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    return _proposal_to_out(proposal)


@router.post("/{proposal_id}/accept", response_model=RemediationOut)
async def accept_proposal(
    proposal_id: str,
    body: RemediationAction = RemediationAction(),
    session: AsyncSession = Depends(get_session),
):
    return await _update_proposal(proposal_id, "accepted", body, session)


@router.post("/{proposal_id}/reject", response_model=RemediationOut)
async def reject_proposal(
    proposal_id: str,
    body: RemediationAction = RemediationAction(),
    session: AsyncSession = Depends(get_session),
):
    return await _update_proposal(proposal_id, "rejected", body, session)
