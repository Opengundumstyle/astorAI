# tests/api/test_search_semantic_repo.py
"""DB-gated: the pgvector query behind the zero-lexical-hit fallback.

Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_search_semantic_repo.py

The embedder is injected, so this costs no API call and the nearest neighbour is
deterministic: the query vector IS the planted product's vector.
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from astor.api import repo
from astor.config import settings
from astor.db.base import session_scope
from astor.db.models import Product

DIM = settings.embedding_dim
TOKEN = "zqsem" + uuid.uuid4().hex[:8]


def _vec(slot: float):
    """A unit vector pointing almost entirely at one axis; `slot` picks the axis."""
    v = [0.0] * DIM
    v[int(slot) % DIM] = 1.0
    return v


class _FixedEmbedder:
    def __init__(self, vector):
        self._vector = vector

    def embed(self, texts):
        return [self._vector for _ in texts]


def _p(name, vector, *, sellable=True):
    return Product(name=name, category="cell_culture", brand="Astor",
                   sellable=sellable, specs={}, embedding=vector)


def test_finds_the_nearest_product_when_no_word_matches():
    with session_scope() as s:
        try:
            near = _p(f"{TOKEN} Accutase Cell Detachment Solution", _vec(3))
            s.add_all([near, _p(f"{TOKEN} Unrelated Pipette Tips", _vec(700))])
            s.flush()

            rows = repo.search_products_semantic(
                s, "something to detach adherent cells", 5,
                embedder=_FixedEmbedder(_vec(3)))

            names = [r["name"] for r in rows]
            assert names[0] == f"{TOKEN} Accutase Cell Detachment Solution", (
                "the planted nearest neighbour must rank first")
        finally:
            s.rollback()


def test_non_sellable_products_are_never_rescued():
    """The lexical path is fail-closed on `sellable`; the fallback must not become
    the hole that reintroduces archived stock."""
    with session_scope() as s:
        try:
            s.add(_p(f"{TOKEN} Archived Medium", _vec(11), sellable=False))
            s.flush()

            rows = repo.search_products_semantic(
                s, "medium", 20, embedder=_FixedEmbedder(_vec(11)))

            assert all(TOKEN not in r["name"] for r in rows)
        finally:
            s.rollback()


def test_blank_query_returns_nothing_without_embedding_it():
    class _Explode:
        def embed(self, texts):
            raise AssertionError("must not embed a blank query")

    with session_scope() as s:
        assert repo.search_products_semantic(s, "   ", 5, embedder=_Explode()) == []
