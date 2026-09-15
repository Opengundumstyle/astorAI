"""Match a customer's failed-experiment description to curated troubleshooting rows.

Two stages, both deterministic given the tables: token overlap first, and if
nothing clears KEYWORD_FLOOR, cosine similarity over row embeddings computed once
when the Matcher is built. No LLM call. The tool layer decides how to present
`drafted` rows; this module only finds them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

from astor.api import repo
from astor.curation import text
from astor.curation.loader import CurationTables, TroubleshootingEntry

log = logging.getLogger(__name__)

KEYWORD_FLOOR = 0.2

# Words that place a symptom in a category when the model did not say which.
# Hand-written, like protocols/categories.py SEED_SYNONYMS, and scored by overlap
# with the tokeniser so a Chinese hint is matched as bigrams.
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "western_blot": ("western", "blot", "immunoblot", "wb", "membrane", "bands", "band", "ecl",
                     "条带", "转膜", "免疫印迹", "显影"),
    "rt_qpcr": ("qpcr", "rt-qpcr", "pcr", "ct", "amplification", "melt", "primer", "primers",
                "ntc", "cdna", "扩增", "引物", "熔解", "内参", "逆转录"),
    "elisa": ("elisa", "plate", "od", "standard", "absorbance", "tmb", "wells", "标准曲线", "酶联",
              "显色", "复孔", "空白"),
    "cell_culture_transfection": ("transfection", "transfect", "cells", "culture", "medium", "confluent",
                                  "plasmid", "mycoplasma", "转染", "细胞", "培养基", "支原体", "传代"),
}


def classify_category(symptom: str, categories: Iterable[str]) -> str | None:
    """The category whose hint words the symptom mentions most. A tie is an
    ambiguous symptom ('曲线' is a bigram of both 标准曲线 and 熔解曲线), so it
    returns None and the caller searches every category rather than guessing."""
    q = text.tokens(symptom)
    scores: list[tuple[int, str]] = []
    for cid in sorted(categories):
        hint_tokens: set[str] = set()
        for h in CATEGORY_HINTS.get(cid, ()):
            hint_tokens |= text.tokens(h)
        scores.append((len(q & hint_tokens), cid))
    scores.sort(key=lambda t: (-t[0], t[1]))
    if not scores or scores[0][0] == 0:
        return None
    if len(scores) > 1 and scores[1][0] == scores[0][0]:
        return None
    return scores[0][1]


@dataclass(frozen=True)
class Hit:
    entry: TroubleshootingEntry
    score: float


def _doc_text(e: TroubleshootingEntry) -> str:
    return f"{e.symptom} {e.likely_cause}"


def _cosine(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u)) or 1.0
    nv = math.sqrt(sum(b * b for b in v)) or 1.0
    return dot / (nu * nv)


class Matcher:
    def __init__(self, tables: CurationTables, embedder=None) -> None:
        self.tables = tables
        self.entries = list(tables.troubleshooting)
        self._doc_tokens = [text.tokens(_doc_text(e)) for e in self.entries]
        self._embedder = embedder
        self._doc_vectors: list[list[float]] | None = None
        if embedder is not None and self.entries:
            try:
                self._doc_vectors = embedder.embed([_doc_text(e) for e in self.entries])
            except Exception:  # noqa: BLE001 — a dead embedder degrades to keyword-only
                log.warning("troubleshooting row embeddings unavailable; keyword-only", exc_info=True)
                self._embedder = None

    def _candidates(self, symptom: str, category: str | None) -> list[int]:
        cats = set(self.tables.categories)
        if category in cats:
            chosen = {category}
        else:
            guess = classify_category(symptom, cats)
            chosen = {guess} if guess else cats
        return [i for i, e in enumerate(self.entries) if e.category_id in chosen]

    def search(self, symptom: str, *, category: str | None, limit: int) -> tuple[list[Hit], str]:
        idx = self._candidates(symptom, category)
        q = text.tokens(symptom)
        scored = [(text.overlap(q, self._doc_tokens[i]), i) for i in idx]
        keyword = sorted(((s, i) for s, i in scored if s >= KEYWORD_FLOOR), key=lambda t: (-t[0], t[1]))
        if keyword:
            return [Hit(self.entries[i], round(s, 3)) for s, i in keyword[:limit]], "keyword"
        if self._embedder is None or self._doc_vectors is None:
            return [], "keyword"
        try:
            qv = self._embedder.embed([symptom])[0]
        except Exception:  # noqa: BLE001
            log.warning("troubleshooting query embedding failed", exc_info=True)
            return [], "keyword"
        sem = sorted(((_cosine(qv, self._doc_vectors[i]), i) for i in idx), key=lambda t: (-t[0], t[1]))
        return [Hit(self.entries[i], round(s, 3)) for s, i in sem[:limit] if s > 0], "semantic"


def _search_term(description: str) -> str:
    """'二抗 —— 检测一抗、带酶标' or '引物 / 探针 / target primers' -> the last
    English segment, which is what the lexical product ranker can use."""
    parts = [p.strip() for p in description.replace("——", "/").split("/") if p.strip()]
    latin = [p for p in parts if any(c.isascii() and c.isalpha() for c in p)]
    return (latin[-1] if latin else parts[-1] if parts else description).strip()


def resolve_products(session, entry: TroubleshootingEntry, tables: CurationTables, *,
                     limit: int = 3) -> tuple[list[dict], str | None]:
    """Turn a row's fix_role into catalog rows. Returns (products, lab_usually_owns_it).
    Products are raw repo rows; the caller gates them for the buyer."""
    if not entry.fix_role:
        return [], None
    role = tables.roles[entry.fix_role]
    if role.lab_usually_owns_it == "yes":
        return [], "yes"
    rows, _ = repo.list_products(session, _search_term(role.plain_description), None, 1, limit)
    return rows, role.lab_usually_owns_it
