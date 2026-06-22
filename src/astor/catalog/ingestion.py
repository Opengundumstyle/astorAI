"""Catalog ingestion as discrete, idempotent steps.

Each step is a plain function. In M1 they run in-process and in sequence; the
boundaries are drawn so a queue/worker fleet can drive them unchanged once
ingestion volume outgrows synchronous runs (the deferred scaling move).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astor.catalog import extraction, normalization
from astor.catalog.schemas import NormalizedItem
from astor.db.models import Product, Supplier, SupplierOffer

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    supplier: str
    extracted: int = 0
    products_upserted: int = 0
    offers_upserted: int = 0
    products_to_match: list[str] = field(default_factory=list)


# -- step 1: extract -------------------------------------------------------- #
def extract_step(source: Path) -> list:
    return extraction.for_source(source).extract(source)


# -- step 2: normalize ------------------------------------------------------ #
def normalize_step(raw: list) -> list[NormalizedItem]:
    return [normalization.normalize(r) for r in raw]


# -- step 3: upsert (idempotent on natural keys) ---------------------------- #
def _get_or_create_supplier(session: Session, name: str, region: str, tier: str) -> Supplier:
    sup = session.scalar(select(Supplier).where(Supplier.name == name))
    if sup is None:
        sup = Supplier(name=name, region=region, tier=tier)
        session.add(sup)
        session.flush()
    return sup


def upsert_step(
    session: Session, supplier: Supplier, items: list[NormalizedItem]
) -> IngestResult:
    res = IngestResult(supplier=supplier.name, extracted=len(items))
    for item in items:
        p = item.product
        # Upsert product on (brand, mpn). When mpn is absent we cannot dedupe
        # safely, so we insert and rely on the matcher to find equivalences.
        if p.brand and p.mpn:
            stmt = (
                insert(Product)
                .values(category=p.category, name=p.name, brand=p.brand, mpn=p.mpn, specs=p.specs)
                .on_conflict_do_update(
                    constraint="uq_product_brand_mpn",
                    set_={"name": p.name, "category": p.category, "specs": p.specs},
                )
                .returning(Product.id)
            )
            product_id = session.execute(stmt).scalar_one()
        else:
            prod = Product(category=p.category, name=p.name, brand=p.brand, mpn=p.mpn, specs=p.specs)
            session.add(prod)
            session.flush()
            product_id = prod.id
        res.products_upserted += 1
        res.products_to_match.append(str(product_id))

        o = item.offer
        offer_stmt = (
            insert(SupplierOffer)
            .values(
                supplier_id=supplier.id,
                product_id=product_id,
                supplier_sku=o.supplier_sku,
                pack_size=o.pack_size,
                cost=o.cost,
                currency=o.currency,
                stock=o.stock,
                lead_time_days=o.lead_time_days,
            )
            .on_conflict_do_update(
                constraint="uq_offer_supplier_sku",
                set_={
                    "product_id": product_id,
                    "cost": o.cost,
                    "currency": o.currency,
                    "stock": o.stock,
                    "lead_time_days": o.lead_time_days,
                    "pack_size": o.pack_size,
                },
            )
        )
        session.execute(offer_stmt)
        res.offers_upserted += 1
    return res


# -- orchestration (M1: in-process; later: queue-driven) -------------------- #
def ingest(
    session: Session, source: Path, supplier_name: str, region: str = "CN", tier: str = "public"
) -> IngestResult:
    raw = extract_step(source)
    items = normalize_step(raw)
    supplier = _get_or_create_supplier(session, supplier_name, region, tier)
    result = upsert_step(session, supplier, items)
    log.info(
        "ingested %s: %d extracted, %d products, %d offers",
        supplier_name, result.extracted, result.products_upserted, result.offers_upserted,
    )
    return result
