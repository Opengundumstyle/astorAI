"""Catalog: product list, product detail, ingest."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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


_ALLOWED_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xlsm"}


@router.post("/ingest")
def ingest_catalog(
    file: UploadFile = File(...),
    supplier: str = Form(...),
    region: str = Form("CN"),
    tier: str = Form("public"),
    run_match: bool = Form(True),
    session: Session = Depends(get_session),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type '{suffix}'; expected CSV or XLSX",
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
    try:
        return repo.run_ingest(session, tmp_path, supplier, region, tier, run_match)
    finally:
        tmp_path.unlink(missing_ok=True)
