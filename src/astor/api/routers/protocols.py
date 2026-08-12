"""Protocols: a protocol's product shopping list (the cart-builder)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from astor.api import repo
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["protocols"])


@router.get("/protocols")
def list_protocols(
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    """Browse servable protocols, most catalog-connected first."""
    items, total = repo.list_protocols(session, q, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/protocols/{protocol_id}/materials")
def protocol_materials(
    protocol_id: str,
    reviewed_only: bool = False,
    limit: int = Query(100, ge=1, le=300),
    session: Session = Depends(get_session),
) -> dict:
    """The Astor products a protocol needs (forward of the material→SKU links)."""
    result = repo.protocol_materials(
        session, protocol_id, reviewed_only=reviewed_only, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="protocol not found")
    return {
        "protocol_id": protocol_id,
        "protocol_title": result["protocol_title"],
        "source_uri": result["source_uri"],
        "count": len(result["materials"]),
        "materials": result["materials"],
    }
