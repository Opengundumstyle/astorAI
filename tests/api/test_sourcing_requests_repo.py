"""DB-gated. Run: RUN_DB_TESTS=1 pytest tests/api/test_sourcing_requests_repo.py"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="needs Postgres; set RUN_DB_TESTS=1"
)

from astor.api import repo
from astor.db.base import Base, engine, session_scope
from astor.db.models import SourcingRequest

TOKEN = "srq-" + uuid.uuid4().hex[:8]


def _ensure_table():
    Base.metadata.create_all(engine, tables=[SourcingRequest.__table__])


def _cleanup():
    with session_scope() as s:
        s.query(SourcingRequest).filter(
            SourcingRequest.requested_item.like(f"{TOKEN}%")
        ).delete(synchronize_session=False)


def test_create_and_list_roundtrip():
    _ensure_table()
    try:
        with session_scope() as s:
            r = repo.create_sourcing_request(
                s, requested_item=f"{TOKEN} Anti-FLAG antibody",
                context="western blot for FLAG tag", shop="astor-dev.myshopify.com",
                customer_id="cust-1", email="lab@uni.edu")
            assert r["status"] == "new"
            assert r["id"]
        with session_scope() as s:
            got = [i for i in repo.list_sourcing_requests(s, limit=50)
                   if TOKEN in i["requested_item"]]
            assert len(got) == 1
            g = got[0]
            assert g["shop"] == "astor-dev.myshopify.com"
            assert g["customer_id"] == "cust-1"
            assert g["email"] == "lab@uni.edu"
            assert g["status"] == "new"
            assert g["created_at"]  # timestamp populated
    finally:
        _cleanup()


def test_list_is_newest_first():
    _ensure_table()
    try:
        # separate transactions so each row gets a distinct now() (Postgres now() is
        # transaction-start time — same within one transaction).
        for i in range(3):
            with session_scope() as s:
                repo.create_sourcing_request(s, requested_item=f"{TOKEN} item {i}", context="")
        with session_scope() as s:
            got = [i for i in repo.list_sourcing_requests(s, limit=50)
                   if TOKEN in i["requested_item"]]
            assert got[0]["requested_item"].endswith("item 2")
            assert got[-1]["requested_item"].endswith("item 0")
    finally:
        _cleanup()


def test_optional_fields_null_when_absent():
    _ensure_table()
    try:
        with session_scope() as s:
            repo.create_sourcing_request(s, requested_item=f"{TOKEN} minimal", context="")
        with session_scope() as s:
            g = [i for i in repo.list_sourcing_requests(s)
                 if TOKEN in i["requested_item"]][0]
            assert g["shop"] is None and g["customer_id"] is None and g["email"] is None
    finally:
        _cleanup()
