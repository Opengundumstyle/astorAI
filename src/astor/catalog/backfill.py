"""Re-embed products with a real provider and stamp provenance.

Pure helpers (hashing, staleness) are unit-tested; the DB orchestration loop is
runbook-verified against the dev DB (this repo has no DB-backed tests). The text
that gets embedded is ALWAYS canonical_text() -- the same string the matcher and
eval harness use -- so vectors and matching agree.
"""
from __future__ import annotations

import hashlib

from astor.catalog.normalization import canonical_text
from astor.catalog.schemas import NormalizedProduct

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
