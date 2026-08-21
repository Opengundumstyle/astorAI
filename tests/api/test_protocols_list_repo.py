# tests/api/test_protocols_list_repo.py
"""DB-gated: exercises the browse-list query against real Postgres.
Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_protocols_list_repo.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from astor.api import repo
from astor.db.base import session_scope
from astor.db.models import Protocol

MARK = "plist-test-" + uuid.uuid4().hex[:8]  # unique source tag → find + clean up

# The dev Postgres is a shared, pre-populated instance (real protocols.io corpus),
# so the search term must be a per-run-unique token or assertions would be
# contaminated by pre-existing rows.
TOKEN = "zqtitle" + uuid.uuid4().hex[:8]


def _p(title, authors, *, rank=0.0):
    return Protocol(
        source=MARK, source_id=uuid.uuid4().hex, source_uri="u", title=title,
        authors=authors, license="cc-by", servable=True, rank_score=rank,
        materials=[], steps=[],
    )


def test_rows_carry_first_author_for_disambiguation():
    with session_scope() as s:
        try:
            s.add_all([
                _p(f"{TOKEN} Western Blot", ["Karyna Tarasova", "Florien Jenner"], rank=5.0),
                _p(f"{TOKEN} Western Blot", ["Payal Patel"], rank=3.0),
                _p(f"{TOKEN} Western Blot", [], rank=1.0),
            ])
            s.flush()

            items, total = repo.list_protocols(s, TOKEN, 1, 20)
            assert total == 3
            assert [i["first_author"] for i in items] == ["Karyna Tarasova", "Payal Patel", None]
        finally:
            s.rollback()
            s.query(Protocol).filter(Protocol.source == MARK).delete()
            s.commit()
