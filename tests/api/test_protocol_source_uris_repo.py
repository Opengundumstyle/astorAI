# tests/api/test_protocol_source_uris_repo.py
"""DB-gated: chip click-through URL lookup against real Postgres.
Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_protocol_source_uris_repo.py
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

MARK = "psu-test-" + uuid.uuid4().hex[:8]


def _p(title, uri):
    return Protocol(
        source=MARK, source_id=uuid.uuid4().hex, source_uri=uri, title=title,
        license="cc-by", servable=True, rank_score=0.0, materials=[], steps=[],
    )


def test_maps_ids_to_source_uris_and_skips_unknown():
    with session_scope() as s:
        try:
            a = _p("WB one", "https://www.protocols.io/view/wb-one")
            b = _p("WB two", "https://www.protocols.io/view/wb-two")
            s.add_all([a, b])
            s.flush()
            ids = [str(a.id), str(b.id), str(uuid.uuid4())]

            uris = repo.protocol_source_uris(s, ids)
            assert uris == {
                str(a.id): "https://www.protocols.io/view/wb-one",
                str(b.id): "https://www.protocols.io/view/wb-two",
            }
            assert repo.protocol_source_uris(s, []) == {}
        finally:
            s.rollback()
            s.query(Protocol).filter(Protocol.source == MARK).delete()
            s.commit()
