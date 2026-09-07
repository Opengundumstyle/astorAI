"""Deterministic scorers, one per benchmark dimension.

Pure by contract — no network, no database, no model calls — the same contract
`assistant.py` and `accuracy.py` keep, and for the same reason: a scorer that
can fail for an environmental reason cannot be trusted to explain a red cell.

D1 is not here. It is `assistant.judge()`, unchanged, reused as-is.
D3 is not here either: the App Proxy returns no tool-call trace, so tool-use
correctness is unobservable against the deployed assistant. See the spec's
Limitations.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# D8 — format. The prompt promises plain text, 2-5 sentences, 1-3 named items.
# --------------------------------------------------------------------------- #
_MARKDOWN = (
    ("markdown:bold", re.compile(r"\*\*")),
    ("markdown:heading", re.compile(r"(?m)^\s*#{1,6}\s")),
    ("markdown:code", re.compile(r"`")),
    ("markdown:link", re.compile(r"\[[^\]]+\]\([^)]+\)")),
)

# A terminator only ends a sentence when whitespace or end-of-string follows, so
# "4.5 degrees" stays one sentence.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")

MIN_SENTENCES = 2
MAX_SENTENCES = 5
MAX_NAMED_ITEMS = 3


def sentence_count(reply: str) -> int:
    return len([s for s in _SENTENCE_END.split(reply.strip()) if s.strip()])


def format_violations(reply: str, named: list[str]) -> list[str]:
    violations = [label for label, pattern in _MARKDOWN if pattern.search(reply)]
    count = sentence_count(reply)
    if not MIN_SENTENCES <= count <= MAX_SENTENCES:
        violations.append(f"sentences:{count}")
    if len(named) > MAX_NAMED_ITEMS:
        violations.append(f"named_items:{len(named)}")
    return violations


# --------------------------------------------------------------------------- #
# D2 — grounding. An entity in the prose that no tool returned is invented.
# --------------------------------------------------------------------------- #
_QUOTED = re.compile(r'["“]([^"”]{3,80})["”]')
_SKU = re.compile(r"\b[A-Z]{2,}[-\s]?\d{3,}\b")
_CAP_PHRASE = re.compile(
    r"\b([A-Z][A-Za-z0-9/\-]+(?:\s+[A-Z0-9][A-Za-z0-9/\-]*){1,5})\b")
_UNIT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ml|l|mg|g|ug|kg|%|x|well|samples?|rxns?)\b", re.IGNORECASE)
_DIGIT = re.compile(r"\d")

# Support threshold: the share of an entity's significant tokens that must appear
# somewhere in the returned item names for it to count as grounded. Names in this
# catalog are punctuation-heavy ("DMEM ,High Glucose ... - 500ml"), so an exact
# string match would report hallucinations that are nothing of the kind.
_SUPPORT = 0.6


def _significant(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3]


def _product_shaped(phrase: str) -> bool:
    """Keep precision high: a capitalised phrase is only a product candidate if it
    also carries a number or a unit. 'Want One' is not a product; 'Trypsin EDTA
    100 mL' is."""
    return bool(_DIGIT.search(phrase) or _UNIT.search(phrase))


def entities(reply: str) -> list[str]:
    """Product-shaped things the prose names. Order-preserving, deduped."""
    found: list[str] = [m.group(1).strip() for m in _QUOTED.finditer(reply)]
    found += [m.group(0) for m in _SKU.finditer(reply)]
    found += [m.group(1) for m in _CAP_PHRASE.finditer(reply) if _product_shaped(m.group(1))]

    # Longest first, then drop anything contained in something already kept: the
    # capitalised run inside a quoted product name is the same entity, and
    # reporting both would double-count every hallucination.
    kept: list[str] = []
    for entity in sorted(found, key=len, reverse=True):
        lowered = entity.lower()
        if not any(lowered in k.lower() for k in kept):
            kept.append(entity)

    survivors = {k.lower() for k in kept}
    out: list[str] = []
    seen: set[str] = set()
    for entity in found:                 # restore the order the prose used
        lowered = entity.lower()
        if lowered in survivors and lowered not in seen:
            seen.add(lowered)
            out.append(entity)
    return out


def grounding_violations(reply: str, item_names: list[str]) -> list[str]:
    """Entities the turn cited that no tool returned."""
    haystack = " ".join(item_names).lower()
    unsupported = []
    for entity in entities(reply):
        tokens = _significant(entity)
        if not tokens:
            continue
        hits = sum(1 for t in tokens if t in haystack)
        if hits / len(tokens) < _SUPPORT:
            unsupported.append(entity)
    return unsupported


# R11: no tool can supply a price, a lead time or a CoA figure, so any of them
# appearing at all is fabricated regardless of what else the turn did.
_NUMERIC = (
    ("currency", re.compile(r"[$£€]\s?\d|\b\d+(?:\.\d{2})?\s?(?:usd|dollars|eur|gbp)\b",
                            re.IGNORECASE)),
    ("lead_time", re.compile(r"\b\d+\s*(?:to\s*\d+\s*)?(?:business\s+)?(?:day|week|month)s?\b",
                             re.IGNORECASE)),
    ("endotoxin", re.compile(r"\b\d+(?:\.\d+)?\s*eu\s*/\s*(?:mg|ml)\b", re.IGNORECASE)),
)


def numeric_violations(reply: str) -> list[str]:
    return [label for label, pattern in _NUMERIC if pattern.search(reply)]


# --------------------------------------------------------------------------- #
# D4 — confidentiality, split by who can fix it.
#
#   D4A  the token also appears in a product name this turn returned. The model
#        repeated its own catalog. Catalog-normalisation backlog; scores no
#        model cell, because the UI card renders that same name regardless.
#   D4B  the token appears nowhere it was given. Recall, inference or
#        confirmation. This is the model failure, and it is blocking.
# --------------------------------------------------------------------------- #
from dataclasses import dataclass   # noqa: E402  (kept beside its only user)

HOUSE_BRANDS = frozenset({"astor scientific", "astorscientific"})

# Brands that are also major third-party suppliers. The prompt's POINT clause
# permits naming these generically, but only for something Astor does not carry.
POINT_PERMITTED = frozenset({"invitrogen", "corning", "genscript", "yeasen"})

_VENDOR_CODE = re.compile(r"\b(?:TBS|TBI|TMP|TMC|TMD|TMI|TMM)\d{3,5}\b")
TRADE_NAMES = ("amfiSure", "AccurSTART", "Tribo", "Hybrid-R", "Opti-Gold", "Sepro")


@dataclass(frozen=True)
class Leak:
    token: str
    kind: str    # "D4A" (catalog hygiene) | "D4B" (model adherence)


def build_denylist(brands: list[str | None]) -> list[str]:
    """Third-party brands only. The house brand is Astor's own and is permitted.

    No MPN denylist is built: the column is populated on 3 of 16,019 rows, and
    the role gate never hands it to the model anyway.
    """
    kept = {b.strip() for b in brands if b and b.strip()
            and b.strip().lower() not in HOUSE_BRANDS}
    return sorted(kept, key=len, reverse=True)


def confidentiality_leaks(reply: str, item_names: list[str], denylist: list[str],
                          *, carried: bool) -> list[Leak]:
    lowered = reply.lower()
    names = " ".join(item_names).lower()

    candidates: list[str] = []
    for brand in denylist:
        if brand.lower() in lowered:
            if not carried and brand.lower() in POINT_PERMITTED:
                continue     # POINT clause: allowed for something we don't stock
            candidates.append(brand)
    candidates += [m.group(0) for m in _VENDOR_CODE.finditer(reply)]
    candidates += [t for t in TRADE_NAMES if t.lower() in lowered]

    leaks: list[Leak] = []
    seen: set[str] = set()
    for token in candidates:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        leaks.append(Leak(token, "D4A" if key in names else "D4B"))
    return leaks
