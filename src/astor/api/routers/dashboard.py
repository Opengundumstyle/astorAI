"""Dashboard stats."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from astor.api import repo
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    return repo.get_stats(session)


@router.get("/sourcing-requests")
def sourcing_requests(
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    """Captured sourcing requests for the team, newest first."""
    items = repo.list_sourcing_requests(session, limit=min(limit, 200))
    return {"items": items, "count": len(items)}
