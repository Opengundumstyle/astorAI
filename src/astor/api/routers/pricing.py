"""Landed-cost breakdown for a product (cheapest offer)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from astor.api import repo, roles
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["pricing"])


@router.get("/products/{product_id}/landed-cost")
def landed_cost_endpoint(
    product_id: str,
    qty: int = Query(1, ge=1),
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    bd = repo.landed_for_product(session, product_id, qty)
    if bd is None:
        raise HTTPException(status_code=404, detail="no priceable offer for product")
    return roles.gate_landed(bd, role)
