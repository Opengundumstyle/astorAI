"""Re-embed products with a real provider and stamp provenance.

Pure helpers (hashing, staleness) are unit-tested; the DB orchestration loop is
runbook-verified against the dev DB (this repo has no DB-backed tests). The text
that gets embedded is ALWAYS canonical_text() -- the same string the matcher and
eval harness use -- so vectors and matching agree.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from astor.catalog.embeddings import Embedder
from astor.catalog.normalization import canonical_text
from astor.catalog.schemas import NormalizedProduct
from astor.db.models import Product

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "voyage-3"


def product_canonical_text(product) -> str:
    np = NormalizedProduct(
        category=product.category, name=product.name, brand=product.brand,
        mpn=product.mpn, specs=product.specs or {},
    )
    return canonical_text(np)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_stale(stored_model, stored_hash, current_model: str, current_text: str) -> bool:
    """True when the stored embedding provenance does not match current model+text."""
    if stored_model != current_model:
        return True
    return stored_hash != text_hash(current_text)


@dataclass
class BackfillStats:
    total: int
    embedded: int
    skipped: int


def _batched(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def backfill_embeddings(
    session, embedder: Embedder, *, only_stale: bool, batch_size: int = 128,
    sleep_between_batches: float = 0.0,
) -> BackfillStats:
    """Re-embed products and stamp provenance. Idempotent when only_stale=True."""
    products = list(session.execute(select(Product)).scalars())
    todo = []
    for p in products:
        txt = product_canonical_text(p)
        if only_stale and not is_stale(p.embedding_model, p.embedding_text_hash, EMBEDDING_MODEL, txt):
            continue
        todo.append((p, txt))

    embedded = 0
    total_batches = (len(todo) + batch_size - 1) // batch_size
    for i, chunk in enumerate(_batched(todo, batch_size), start=1):
        if i > 1 and sleep_between_batches > 0:
            time.sleep(sleep_between_batches)  # pace requests for free-tier 3 RPM / 10K TPM limits
        vectors = embedder.embed([t for _, t in chunk])
        for (p, txt), vec in zip(chunk, vectors):
            p.embedding = vec
            p.embedding_model = EMBEDDING_MODEL
            p.embedding_text_hash = text_hash(txt)
        session.flush()
        session.commit()  # persist each batch so --only-stale resumes after an interruption
        embedded += len(chunk)
        log.info("backfill batch %d/%d — embedded %d/%d", i, total_batches, embedded, len(todo))

    return BackfillStats(total=len(products), embedded=embedded, skipped=len(products) - embedded)
