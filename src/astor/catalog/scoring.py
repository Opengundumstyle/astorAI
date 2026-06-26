"""Pure equivalence-scoring logic, shared by the live matcher and the eval harness.

Keeping this DB-free and ORM-free means the accuracy harness measures exactly the
logic that runs in production, not a reimplementation that can silently drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProductView:
    """Minimal projection of a product needed for scoring."""

    category: str
    name: str
    brand: str | None = None
    mpn: str | None = None
    specs: dict = field(default_factory=dict)


def attribute_bonus(a: ProductView, b: ProductView) -> float:
    """Structured agreement signal layered on top of vector similarity."""
    score = 0.0
    if a.category and a.category == b.category:
        score += 0.05
    a_specs, b_specs = a.specs or {}, b.specs or {}
    shared = set(a_specs) & set(b_specs)
    if shared:
        agree = sum(1 for k in shared if str(a_specs[k]).lower() == str(b_specs[k]).lower())
        score += 0.10 * (agree / len(shared))
    # Same brand + same mpn is a definitional exact match.
    if a.brand and a.brand == b.brand and a.mpn and a.mpn == b.mpn:
        score += 0.50
    return score


def confidence(similarity: float, a: ProductView, b: ProductView) -> float:
    return min(1.0, max(0.0, similarity) + attribute_bonus(a, b))


def classify(conf: float, exact_threshold: float, substitute_threshold: float) -> str | None:
    if conf >= exact_threshold:
        return "exact"
    if conf >= substitute_threshold:
        return "substitute"
    return None
