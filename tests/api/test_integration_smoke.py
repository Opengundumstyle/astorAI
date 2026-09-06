"""End-to-end smoke test. Skipped unless a real Postgres+pgvector is reachable.

Run locally with:  docker compose up -d  &&  alembic upgrade head  &&  pytest
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres+pgvector; set RUN_DB_TESTS=1 to run",
)


def test_seed_then_stats_and_detail():
    from astor.api import repo
    from astor.api.seed import seed_demo
    from astor.db.base import session_scope

    with session_scope() as session:
        seed_demo(session)
        stats = repo.get_stats(session)
        assert stats["products"] >= 1
        items, total = repo.list_products(session, None, None, 1, 20)
        assert total >= 1
        detail = repo.get_product_detail(session, items[0]["id"])
        assert detail is not None and detail["astor_sku"].startswith("ASR-")


def test_search_ranks_and_filters_against_real_postgres():
    """The pure rules are covered by tests/test_search.py; this proves the SQL
    prefilter + df counts actually execute and agree with them.

    Inserts a dedicated supplier and removes it afterwards, so running against a
    populated dev database leaves no residue.
    """
    from sqlalchemy import delete, select

    from astor.api import repo
    from astor.catalog.ingestion import ingest_extracted
    from astor.catalog.schemas import ExtractedProduct
    from astor.db.base import session_scope
    from astor.db.models import Product, Supplier, SupplierOffer

    catalog = [
        ("SEARCHTEST Cell culture plate, 6 well, flat base", "ST-6W"),
        ("SEARCHTEST NEST 96 Well Cell Culture Plate, U-bottom", "ST-96W"),
        ("SEARCHTEST Eagle\u2019s Minimum Essential Medium (EMEM) 1x", "ST-EMEM"),
    ]
    try:
        with session_scope() as session:
            ingest_extracted(
                session,
                [ExtractedProduct(supplier_sku=sku, name=name, category="consumables",
                                  brand="SearchTest", mpn=None, pack_size=None, cost=1.0,
                                  currency="USD", stock=1, lead_time_days=None, specs={})
                 for name, sku in catalog],
                supplier_name="SearchTest Supplier", region="US", tier="public",
            )

        with session_scope() as session:
            items, _ = repo.list_products(session, "6 well cell culture plate", None, 1, 50)
            mine = [i["name"] for i in items if i["name"].startswith("SEARCHTEST")]
            assert any("6 well" in n for n in mine), mine
            assert not any("96 Well" in n for n in mine), mine

            items, _ = repo.list_products(session, "Eagle's Minimum Essential Medium", None, 1, 50)
            assert any("EMEM" in i["name"] for i in items), "ASCII apostrophe failed to match"

            assert repo.list_products(session, "!!!", None, 1, 20) == ([], 0)
    finally:
        with session_scope() as session:
            sup = session.scalar(select(Supplier).where(Supplier.name == "SearchTest Supplier"))
            if sup is not None:
                session.execute(delete(SupplierOffer).where(SupplierOffer.supplier_id == sup.id))
                session.execute(delete(Product).where(Product.brand == "SearchTest"))
                session.execute(delete(Supplier).where(Supplier.id == sup.id))
