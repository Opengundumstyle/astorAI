"""Catalog: product list, product detail, ingest."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from astor.api import repo, roles
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/products")
def products(
    q: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    items, total = repo.list_products(session, q, category, page, page_size)
    return {
        "items": [roles.gate_product(i, role) for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/products/{product_id}")
def product_detail(
    product_id: str,
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    detail = repo.get_product_detail(session, product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return roles.gate_detail(detail, role)
