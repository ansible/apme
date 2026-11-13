"""Global Git requirement policy endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from apme_gateway.db import get_session
from apme_gateway.db.models import GitRequirementsSettings

router = APIRouter(prefix="/api/v1/settings/git-requirements", tags=["settings"])


class GitRequirementsPolicy(BaseModel):  # type: ignore[misc]
    """Global opt-in for fetching Git collections and roles.

    Attributes:
        enabled: Whether projects may fetch Git requirements.
    """

    enabled: bool = False


@router.get("")  # type: ignore[untyped-decorator]
async def get_git_requirements_policy() -> GitRequirementsPolicy:
    """Return global policy, disabled until explicitly enabled.

    Returns:
        Current global Git requirements policy.
    """
    async with get_session() as db:
        row = await db.get(GitRequirementsSettings, 1)
        return GitRequirementsPolicy(enabled=bool(row and row.enabled))


@router.put("")  # type: ignore[untyped-decorator]
async def set_git_requirements_policy(body: GitRequirementsPolicy) -> GitRequirementsPolicy:
    """Persist global policy.

    Args:
        body: New global policy.

    Returns:
        Saved policy.
    """
    async with get_session() as db:
        statement = insert(GitRequirementsSettings).values(id=1, enabled=body.enabled)
        await db.execute(statement.on_conflict_do_update(index_elements=["id"], set_={"enabled": body.enabled}))
        await db.commit()
    return body
