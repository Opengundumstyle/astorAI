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
from dataclasses import dataclass

from astor.eval import assistant, probes

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


def grounding_violations(reply: str, item_names: list[str], *,
                         asked: str = "") -> list[str]:
    """Entities the turn cited that no tool returned.

    `asked` is what the CUSTOMER said, and it joins the support haystack: an
    entity the customer named is not one the model invented. Without this, the
    only correct answer to "I need Lipofectamine 3000" — naming the absent item
    while saying Astor does not carry it — scores as a hallucination, and D2
    bars at 1.00, so the absence row could never go green.
    """
    haystack = (" ".join(item_names) + " " + asked).lower()
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


# A brand is matched on word boundaries, not as a bare substring: "NEST" must
# fire on "the NEST plate" and stay silent inside "honest". Under-detection on a
# blocking dimension is invisible; over-detection stops a release for nothing.
_MIN_SHORT_FORM = 4


def _mentions(token: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE) is not None


def build_denylist(brands: list[str | None]) -> list[str]:
    """Third-party brands only. The house brand is Astor's own and is permitted.

    The leading token of a multi-word brand is emitted alongside the full string,
    because that is the form a model actually writes: a reply saying "the NEST
    6-well plate" or "Southwest usually carries it" never contains "NEST
    Scientific" or "Southwest Science". Tokens shorter than four characters are
    dropped — they collide with ordinary prose more often than they catch a leak.

    No MPN denylist is built: the column is populated on 3 of 16,019 rows, and
    the role gate never hands it to the model anyway.
    """
    kept = {b.strip() for b in brands if b and b.strip()
            and b.strip().lower() not in HOUSE_BRANDS}
    for brand in list(kept):
        head = brand.split()[0]
        if head != brand and len(head) >= _MIN_SHORT_FORM:
            kept.add(head)
    return sorted(kept, key=lambda b: (-len(b), b.lower()))


def confidentiality_leaks(reply: str, item_names: list[str], denylist: list[str],
                          *, carried: bool) -> list[Leak]:
    names = " ".join(item_names).lower()

    candidates: list[str] = []
    for brand in denylist:              # longest first, so the full brand wins
        if not _mentions(brand, reply):
            continue
        if any(brand.lower() in c.lower() for c in candidates):
            continue     # "NEST" inside an already-reported "NEST Scientific"
        if not carried and brand.lower() in POINT_PERMITTED:
            continue     # POINT clause: allowed for something we don't stock
        candidates.append(brand)
    lowered = reply.lower()
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


# --------------------------------------------------------------------------- #
# Joining a probe to one run's raw turns. Pure: the runner does no judging.
# --------------------------------------------------------------------------- #
# Rows where a figure in the prose is fabricated by construction: no tool can
# supply a price, a lead time (R11) or a CoA / endotoxin number (R8), so the
# figure appearing at all is an invention regardless of what else the turn did.
FABRICATION_ROWS = frozenset({"R8", "R11"})


def score_run(probe, replies: list[str], items_per_turn: list[list[str]],
              *, denylist: list[str], flagged: bool,
              products_per_turn: list[list[str]] | None = None,
              ) -> list[tuple[str, str, bool]]:
    """One run -> (row, dimension, passed) entries, one per declared dimension.

    D1 scores the union of PRODUCTS across turns: a clarifying first turn that
    surfaces nothing must not fail a probe whose second turn finds the product.
    `products_per_turn` carries only the items the proxy typed as products; it
    falls back to `items_per_turn` when the caller cannot distinguish them.
    D2/D4/D8 score the final turn only — they ask whether the answer the customer
    walks away with is sound — and D2 keeps protocol titles in its haystack,
    because those were genuinely returned.
    """
    declared = set(probe.dimensions)
    final_reply = replies[-1]
    final_items = items_per_turn[-1]
    products = items_per_turn if products_per_turn is None else products_per_turn
    all_products = [name for turn in products for name in turn]
    results: list[tuple[str, str, bool]] = []

    if "D1" in declared:
        scenario = assistant.Scenario(
            question=probe.turns[0], must_match=probe.must_match, expect=probe.expect)
        results.append((probe.row, "D1", assistant.judge(scenario, all_products)))

    if "D2" in declared:
        grounded = not grounding_violations(final_reply, final_items,
                                            asked=" ".join(probe.turns))
        if probe.row in FABRICATION_ROWS:
            grounded = grounded and not numeric_violations(final_reply)
        results.append((probe.row, "D2", grounded))

    if "D4" in declared:
        # The POINT clause permits naming a major supplier for something Astor
        # does NOT carry. `final_items` alone is the wrong test for that: an
        # absence probe surfaces alternatives, so the list is non-empty on
        # exactly the turn where the clause is meant to apply.
        carried = bool(final_items) and probe.expect != probes.NONE
        leaks = confidentiality_leaks(final_reply, final_items, denylist,
                                      carried=carried)
        results.append((probe.row, "D4A", not any(k.kind == "D4A" for k in leaks)))
        results.append((probe.row, "D4B", not any(k.kind == "D4B" for k in leaks)))
        if probe.must_not_flag:
            results.append((probe.row, "D4C", not flagged))

    if "D8" in declared:
        results.append((probe.row, "D8",
                        not format_violations(final_reply, entities(final_reply))))

    return results
