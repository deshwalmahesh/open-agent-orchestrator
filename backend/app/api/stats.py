"""Per-user usage metrics (Phase 5d): runs, reviews, thumbs, top tools/sub-agents.
Minimal foundation for a metrics dashboard — extend with time ranges/charts later."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import get_user_stats
from app.users import current_active_user

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
async def my_stats(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    return await get_user_stats(session, user_id=user.id)
