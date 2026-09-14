"""DB-gated: the sync writer and the offer key it relies on.

Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_sync_apply_repo.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from sqlalchemy import select

from astor.catalog import sync
from astor.catalog.ingestion import ingest_extracted, normalize_step
from astor.catalog.schemas import ExtractedProduct
from astor.config import settings
from astor.db.base import session_scope
from astor.db.models import Product, Supplier, SupplierOffer

TOKEN = "zqsync" + uuid.uuid4().hex[:8]
SUPPLIER = f"{TOKEN} Shopify"


def _x(sku, name, *, external_id, price=1.0, sellable=True):
    return ExtractedProduct(supplier_sku=sku, external_id=external_id, name=name,
                            brand="NEST", category="cell_culture", cost=price,
                            currency="USD", sellable=sellable)


def _supplier(s):
    return s.scalar(select(Supplier).where(Supplier.name == SUPPLIER))


def test_ingest_stores_the_external_id_on_the_offer():
    with session_scope() as s:
        try:
            ingest_extracted(s, [_x("A-1", f"{TOKEN} Flask", external_id="11")], SUPPLIER)
            offer = s.scalar(select(SupplierOffer).where(SupplierOffer.supplier_sku == "A-1",
                                                         SupplierOffer.supplier_id == _supplier(s).id))
            assert offer.external_id == "11"
        finally:
            s.rollback()


def test_apply_updates_inserts_marks_vanished_and_backfills_the_key():
    with session_scope() as s:
        try:
            # First load, before external ids existed: strip them to simulate.
            ingest_extracted(s, [_x("AS-707003", f"{TOKEN} Flask 25", external_id=None),
                                 _x("AS-999", f"{TOKEN} Gone", external_id=None)], SUPPLIER)
            sup = _supplier(s)
            for p in s.scalars(select(Product).where(Product.name.like(f"{TOKEN}%"))):
                p.embedding = [0.0] * len(p.embedding) if p.embedding else None
            s.flush()

            feed = normalize_step([
                # Renamed SKU, same title: first-run match is by exact name+brand.
                _x("NT707003", f"{TOKEN} Flask 25", external_id="11", price=7.5),
                _x("NT-NEW", f"{TOKEN} Brand new variant", external_id="12"),
            ])
            plan = sync.plan_sync(feed, sync.load_existing(s, sup))
            counts = sync.apply_plan(s, plan, sup)
            assert counts == {"updated": 1, "inserted": 1, "vanished": 1, "collisions": 0}

            renamed = s.scalar(select(SupplierOffer).where(SupplierOffer.supplier_id == sup.id,
                                                           SupplierOffer.external_id == "11"))
            assert renamed is not None, "first run must backfill the variant id"
            assert renamed.supplier_sku == "NT707003"
            assert float(renamed.cost) == 7.5
            assert renamed.product.name == f"{TOKEN} Flask 25"

            new = s.scalar(select(SupplierOffer).where(SupplierOffer.supplier_id == sup.id,
                                                       SupplierOffer.external_id == "12"))
            assert new is not None and new.product.name == f"{TOKEN} Brand new variant"

            gone = s.scalar(select(Product).where(Product.name == f"{TOKEN} Gone"))
            assert gone.sellable is False, "vanished variant -> unsellable, never deleted"

            # Second run: the key is stamped, so a retitled variant matches by id
            # alone and its now-stale vector is cleared for re-embedding.
            renamed.product.embedding = [0.0] * settings.embedding_dim
            new.product.embedding = [0.0] * settings.embedding_dim
            s.flush()
            feed = normalize_step([
                _x("NT707003", f"{TOKEN} Flask 25 cm2, vented", external_id="11", price=7.5),
                _x("NT-NEW", f"{TOKEN} Brand new variant", external_id="12"),
            ])
            counts = sync.apply_plan(s, sync.plan_sync(feed, sync.load_existing(s, sup)), sup)
            assert counts == {"updated": 2, "inserted": 0, "vanished": 1, "collisions": 0}
            s.refresh(renamed.product); s.refresh(new.product)
            assert renamed.product.name == f"{TOKEN} Flask 25 cm2, vented"
            assert renamed.product.embedding is None, "title changed -> vector cleared"
            assert new.product.embedding is not None, "unchanged text keeps its vector"
        finally:
            s.rollback()
