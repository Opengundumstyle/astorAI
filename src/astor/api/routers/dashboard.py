"""Dashboard stats."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from astor.api import repo
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    return repo.get_stats(session)
