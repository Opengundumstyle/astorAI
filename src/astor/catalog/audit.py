"""Ingestion-quality checks: did the catalog record what the source actually said?

Pure — no ORM, no database, no I/O — for the same reason `scoring` and `search`
are: the audit must exercise rules that can be unit-tested and re-run, not a
one-off query someone wrote once and threw away. `scripts/audit_ingestion.py`
drives these over the live catalog.

WHY THIS IS NOT A LABELLING PROBLEM
    Matching asks "is X equivalent to Y" — a judgement with no external
    truth-maker for the substitute class, which is why it needs a domain expert.
    Extraction asks "did we copy this field correctly" — the truth is in the
    source record, so it is verifiable by comparison. No biologist required.

Every rule here corresponds to a defect measured in the live catalog on
2026-09-07, and each one is upstream of the matching layer: `mpn` being blank on
16,016 of 16,019 products is what disabled `scoring`'s +0.50 brand+MPN rule, the
largest single term in the equivalence confidence formula.
"""
from __future__ import annotations

import re

# Values a source writes to mean "nothing", which a naive importer stores as data.
# "0" is deliberately absent: a zero quantity is a fact, not a blank.
_PLACEHOLDERS = {
    "", "-", "--", "n/a", "na", "none", "null", "nil",
    "default title", "unknown", "tbd", "undefined",
}

# A catalogue number: 1-4 capitals then 3+ digits (TBI4816, P520, MP201).
_CODE = re.compile(r"\b([A-Z]{1,4}[0-9]{3,})\b")
# The same shape inside parentheses, which is where a vendor SKU reliably sits.
# Needs 2+ capitals so "(1000 ML)" and "(1:1 Mixture)" cannot qualify.
_PAREN_CODE = re.compile(r"\(([A-Z]{2,}[0-9]{3,})\)")

_APOSTROPHE_LOOKALIKES = "ˇ´`"
_REPLACEMENT = "�"


def is_placeholder(value: str | None) -> bool:
    """True when a stored value means 'the source had nothing here'."""
    if value is None:
        return True
    return value.strip().casefold() in _PLACEHOLDERS


def code_in_name(name: str | None) -> str | None:
    """The catalogue number a product name carries, if any.

    A parenthesised code wins over a bare one: in
    `AZD8055, mTOR kinase inhibitor (TBI4816) - 10 MG` the bare token is the
    COMPOUND name and only the parenthesised token is the vendor's SKU.
    """
    if not name:
        return None
    paren = _PAREN_CODE.search(name)
    if paren:
        return paren.group(1)
    bare = _CODE.search(name)
    return bare.group(1) if bare else None


def recoverable_mpn(name: str | None, mpn: str | None) -> str | None:
    """A catalogue number safe to auto-fill into an empty `mpn`.

    Parenthesised only. Not a parsing bug that it is missing: `mpn` is sourced
    from a Shopify metafield or `variant.barcode` (`shopify_source.py:230`) and
    both are empty upstream, so the value was in the name the whole time.

    Deliberately conservative. The first audit run over the live catalog matched
    `Tribo™ Human CA125 ELISA Kit` -> "CA125", which is a biomarker, not a SKU.
    Writing that to `mpn` would make `scoring`'s +0.50 brand+MPN rule fire on an
    analyte name — a false identity claim, and worse than the blank it replaced.
    Bare tokens go to `possible_mpn` for review instead.
    """
    if not is_placeholder(mpn) or not name:
        return None
    paren = _PAREN_CODE.search(name)
    return paren.group(1) if paren else None


def possible_mpn(name: str | None, mpn: str | None) -> str | None:
    """A code-shaped token outside parentheses: review before trusting it.

    Same shape as a SKU, but the corpus proves the shape is not sufficient —
    compound names (AZD8055) and analyte names (CA125) share it.
    """
    if not is_placeholder(mpn) or not name:
        return None
    if _PAREN_CODE.search(name):
        return None
    bare = _CODE.search(name)
    return bare.group(1) if bare else None


def normalize_brand(brand: str | None) -> str:
    """Collision key: case- and punctuation-insensitive."""
    return re.sub(r"[^a-z0-9]+", "", (brand or "").casefold())


def brand_collisions(brands) -> dict[str, list[str]]:
    """Distinct spellings that denote one supplier, keyed by their shared form.

    `Astor Scientific` and `AstorScientific` are one brand stored as two, which
    suppresses genuine same-brand matches and inflates the corpus's apparent
    brand diversity.
    """
    groups: dict[str, list[str]] = {}
    for brand in brands:
        if not brand:
            continue
        key = normalize_brand(brand)
        if not key:
            continue
        seen = groups.setdefault(key, [])
        if brand not in seen:
            seen.append(brand)
    return {k: v for k, v in groups.items() if len(v) > 1}


def encoding_defects(text: str | None) -> list[str]:
    """Character-level damage that reaches a customer-facing card verbatim."""
    if not text:
        return []
    found = []
    if any(ch in text for ch in _APOSTROPHE_LOOKALIKES):
        found.append("mangled_apostrophe")
    if _REPLACEMENT in text:
        found.append("replacement_char")
    if "  " in text:
        found.append("double_space")
    return found


def internal_spec_keys(specs: dict | None) -> list[str]:
    """Underscore-prefixed keys, which are internal bookkeeping.

    `chat/tools._public_specs` strips these before the assistant sees them, but
    `normalization.canonical_text` embeds them, so they reach the vector and the
    attribute bonus. The two paths disagree; this reports the exposure.
    """
    return sorted(k for k in (specs or {}) if k.startswith("_"))
