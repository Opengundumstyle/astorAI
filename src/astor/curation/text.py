"""Script-aware tokens for short bilingual strings.

Latin runs become lowercase words (hyphens and plus signs kept, so 'rt-qpcr'
survives). CJK runs become character bigrams, which is the cheapest thing that
matches '没有条带' against '完全没有条带' without a segmenter. A lone CJK
character is kept as a unigram so it is not silently dropped.
"""
from __future__ import annotations

import re

_LATIN = re.compile(r"[a-z0-9][a-z0-9\-\+]*")
_CJK = re.compile(r"[一-鿿]+")

STOPWORDS = frozenset({"the", "a", "an", "my", "i", "is", "are", "it", "of", "and", "to",
                       "in", "on", "with", "for", "有", "了", "很", "我", "的"})


def tokens(s: str) -> set[str]:
    s = (s or "").lower()
    out: set[str] = set()
    for w in _LATIN.findall(s):
        if w not in STOPWORDS:
            out.add(w)
    for run in _CJK.findall(s):
        if len(run) == 1:
            out.add(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in STOPWORDS:
                out.add(bigram)
    return out


def overlap(query: set[str], doc: set[str]) -> float:
    """Fraction of query tokens present in the document. Query-recall, because a
    customer's symptom is short and the row's text is long."""
    if not query:
        return 0.0
    return len(query & doc) / len(query)
