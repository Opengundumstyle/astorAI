"""DB-gated: `astor_sku` on every buyer-facing shape is the Shopify channel SKU.

Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_storefront_sku_repo.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from sqlalchemy import select

from astor.api import repo
from astor.config import settings
from astor.db.base import session_scope
from astor.db.models import Equivalence, Product, Supplier, SupplierOffer

TOKEN = "zqsku" + uuid.uuid4().hex[:8]


def _supplier(s, name, region):
    sup = s.scalar(select(Supplier).where(Supplier.name == name))
    if sup is None:
        sup = Supplier(name=name, region=region, tier="public")
        s.add(sup)
        s.flush()
    return sup


def _product(name):
    return Product(name=name, category="cell_culture", brand="Astor", sellable=True, specs={})


def _offer(sup, product, sku):
    return SupplierOffer(supplier_id=sup.id, product_id=product.id, supplier_sku=sku,
                         cost=1, currency="USD")


def _plant(s):
    channel = _supplier(s, settings.shopify_supplier_name, "US")
    upstream = _supplier(s, f"{TOKEN} Upstream CN", "CN")
    listed = _product(f"{TOKEN} Flask listed on the store")
    upstream_only = _product(f"{TOKEN} Flask with an upstream offer only")
    orphan = _product(f"{TOKEN} Flask with no offer")
    s.add_all([listed, upstream_only, orphan])
    s.flush()
    s.add_all([
        _offer(channel, listed, f"{TOKEN}-AS-1"),
        _offer(upstream, listed, f"{TOKEN}-VZ-1"),
        _offer(upstream, upstream_only, f"{TOKEN}-VZ-2"),
    ])
    s.add(Equivalence(product_id=orphan.id, equivalent_id=listed.id,
                      confidence=0.9, kind="substitute"))
    s.flush()
    return listed, upstream_only, orphan


def test_list_products_reports_the_channel_sku_and_null_otherwise():
    with session_scope() as s:
        try:
            listed, upstream_only, orphan = _plant(s)
            rows, _ = repo.list_products(s, TOKEN, None, 1, 10)
            by_id = {r["id"]: r["astor_sku"] for r in rows}
            assert by_id[str(listed.id)] == f"{TOKEN}-AS-1"
            assert by_id[str(upstream_only.id)] is None, "upstream part numbers never leak"
            assert by_id[str(orphan.id)] is None
        finally:
            s.rollback()


def test_product_detail_and_its_equivalents_carry_the_channel_sku():
    with session_scope() as s:
        try:
            listed, _, orphan = _plant(s)
            d = repo.get_product_detail(s, str(orphan.id))
            assert d["astor_sku"] is None
            assert d["equivalents"][0]["astor_sku"] == f"{TOKEN}-AS-1"
            assert repo.get_product_detail(s, str(listed.id))["astor_sku"] == f"{TOKEN}-AS-1"
        finally:
            s.rollback()
