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


# Trailing pack size: "- 500 MG", ", 1 G", "- 1 x 96-well plate", "- 100 Tests".
_PACK_SIZE = re.compile(
    r"[-,(]?\s*[\d.]+\s*(?:x\s*[\d.]+\s*)?"
    r"(?:ml|l|mg|g|µg|μg|ug|kg|ea|t|tests?|samples?|assays?|rxns?|reactions?|"
    r"pcs|piece|pieces|units?|well|plate)\b.*$",
    re.IGNORECASE,
)


def product_stem(name: str | None) -> str:
    """A product's identity with pack size and catalogue code removed.

    Two rows sharing a stem are the same product in different quantities;
    two rows with different stems are different products, even under one code.
    """
    if not name:
        return ""
    stripped = _PAREN_CODE.sub("", name)
    stripped = _PACK_SIZE.sub("", stripped)
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


def mpn_backfill_plan(records) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Which products can have `mpn` filled from their name, and why the rest cannot.

    `records` is an iterable of `(product_id, name, brand, mpn)`.
    Returns `(assignments, skipped)` where `skipped` maps `(brand, code)` to a reason.

    Keyed on `(brand, code)`: an MPN is unique only WITHIN a manufacturer.

    Two reasons a code is skipped, and they are NOT the same problem:

    `unique_constraint` — the code is on more than one product row. The schema
        declares `UniqueConstraint("brand", "mpn")`: "a (brand, mpn) pair
        identifies one canonical product". But this catalog stores each Shopify
        VARIANT as its own Product row (16,019 products / 15,991 offers, none
        with more than one offer), so pack-size variants are separate rows
        sharing one vendor code. The schema and the data disagree; a backfill
        cannot resolve that, and 1,181 codes covering 2,380 products land here.

    `two_identities` — the code is on genuinely different products (TBI2526 sits
        on both `Acipimox` and `Dehydrozingerone`). A vendor or transcription
        error that needs fixing at the source, not merely skipping.

    Note what this means for `scoring`: because the unique constraint forbids two
    distinct rows from sharing (brand, mpn), the +0.50 brand+MPN bonus can never
    fire between two products. It is unreachable by construction, not merely
    dormant for want of data — so no amount of backfilling revives it on the
    product<->product path.
    """
    by_code: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for product_id, name, brand, mpn in records:
        code = recoverable_mpn(name, mpn)
        if not code:
            continue
        by_code.setdefault((brand or "", code), []).append((str(product_id), name))

    assignments: dict[str, str] = {}
    skipped: dict[tuple[str, str], str] = {}
    for key, members in by_code.items():
        if len({product_stem(name) for _, name in members}) > 1:
            skipped[key] = "two_identities"
        elif len(members) > 1:
            skipped[key] = "unique_constraint"
        else:
            assignments[members[0][0]] = key[1]
    return assignments, skipped
