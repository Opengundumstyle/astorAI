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
