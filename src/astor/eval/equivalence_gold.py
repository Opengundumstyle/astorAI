"""Turn the domain expert's equivalence verdicts into a gold set.

Mary labelled every one of the 182 pairs on which the two labelling models
disagreed (docs/curation/equivalence-pairs.csv), free-text, in the verdict
column: '可替代，相同容量', '不可替代，一个无菌', '无法判断...'.  This module
parses that into the three kinds the accuracy harness understands
(exact / substitute / none), keeps her reasons as notes, drops the pairs she
could not call, and reports which of the two models her verdicts sided with.

The harness (astor.eval.accuracy) joins on product keys, not names, so the
export step needs a name+brand -> id map from the catalog database.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = ("exact", "substitute", "none")

# Order matters: '不可替代' must be tried before '可替代' (it contains it).
_VERDICT_WORDS: tuple[tuple[str, str | None], ...] = (
    ("不可替代", "none"),
    ("可替代", "substitute"),
    ("相同", "exact"),
    ("无法判断", None),
    ("拿不准", None),
    ("看情况", None),
)
_SEP = re.compile(r"^[\s,，;；:：]+")

_MODEL_WORDS = {"相同": "exact", "可替代": "substitute", "不可替代": "none"}


class UnresolvedProduct(KeyError):
    """A gold pair names a product the catalog map does not contain."""


def parse_verdict(raw: str | None) -> tuple[str | None, str]:
    """'可替代，相同容量' -> ('substitute', '相同容量').

    Returns (kind, note). kind is None for empty cells and for 无法判断/拿不准,
    in which case the whole cell is kept as the note. Any other leading word
    raises: a typo scored silently as a kind would poison the gold set.
    """
    text = (raw or "").strip()
    if not text:
        return None, ""
    for word, kind in _VERDICT_WORDS:
        if text.startswith(word):
            if kind is None:
                return None, text
            return kind, _SEP.sub("", text[len(word):]).strip()
    raise ValueError(f"unrecognised verdict: {text!r}")


@dataclass
class GoldRow:
    pair_id: str
    rule: str
    a: str
    brand_a: str
    b: str
    brand_b: str
    model1: str
    model2: str
    raw: str
    kind: str | None
    note: str
    similarity: float | None


def load_xlsx(path: Path) -> list[GoldRow]:
    """Read Mary's returned workbook (the pairs CSV re-saved as xlsx, one sheet)."""
    import openpyxl  # local import: not a runtime dependency of the service

    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows: list[GoldRow] = []
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r[0]:
            continue
        kind, note = parse_verdict(r[12])
        rows.append(GoldRow(
            pair_id=str(r[0]).strip(), rule=(r[2] or "").strip(),
            a=(r[3] or "").strip(), brand_a=(r[4] or "").strip(),
            b=(r[5] or "").strip(), brand_b=(r[6] or "").strip(),
            model1=_MODEL_WORDS[r[7]], model2=_MODEL_WORDS[r[9]],
            raw=(r[12] or "").strip(), kind=kind, note=note,
            similarity=float(r[11]) if r[11] is not None else None,
        ))
    return rows


# ---------------------------------------------------------------- analysis #
@dataclass
class ModelComparison:
    labelled: int
    model1_agrees: int
    model2_agrees: int
    neither: int
    neither_ids: list[str] = field(default_factory=list)
    kappa_model1: float | None = None
    kappa_model2: float | None = None


def compare_models(rows: list[GoldRow]) -> ModelComparison:
    """On the disagreement set, which model did the expert side with?"""
    lab = [r for r in rows if r.kind is not None]
    m1 = [r for r in lab if r.model1 == r.kind]
    m2 = [r for r in lab if r.model2 == r.kind]
    neither = [r.pair_id for r in lab if r.kind not in (r.model1, r.model2)]
    gold = [r.kind for r in lab]
    return ModelComparison(
        labelled=len(lab), model1_agrees=len(m1), model2_agrees=len(m2),
        neither=len(neither), neither_ids=neither,
        kappa_model1=multiclass_kappa(gold, [r.model1 for r in lab]) if lab else None,
        kappa_model2=multiclass_kappa(gold, [r.model2 for r in lab]) if lab else None,
    )


def multiclass_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa over an arbitrary label alphabet."""
    if len(a) != len(b):
        raise ValueError("label sets must be the same length")
    if not a:
        raise ValueError("cannot compute kappa on an empty label set")
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def rule_summary(rows: list[GoldRow]) -> dict[str, dict]:
    """Per bucket: how many of each kind, and the majority call."""
    out: dict[str, dict] = {}
    by_rule: dict[str, list[GoldRow]] = defaultdict(list)
    for r in rows:
        by_rule[r.rule].append(r)
    for rule, rs in by_rule.items():
        c = Counter(r.kind for r in rs)
        counts = {k: c.get(k, 0) for k in KINDS}
        counts["unlabelled"] = c.get(None, 0)
        majority = max(KINDS, key=lambda k: counts[k]) if any(counts[k] for k in KINDS) else None
        out[rule] = {**counts, "total": len(rs), "majority": majority}
    return out


def duplicate_pairs(rows: list[GoldRow]) -> list[list[str]]:
    """Pair ids that name the same two products, in either order."""
    groups: dict[frozenset, list[str]] = defaultdict(list)
    for r in rows:
        groups[frozenset((r.a, r.b))].append(r.pair_id)
    return [ids for ids in groups.values() if len(ids) > 1]


# ---------------------------------------------------------------- export #
GOLD_COLUMNS = ["pair_id", "rule", "a", "brand_a", "b", "brand_b",
                "kind", "note", "raw", "model1", "model2", "similarity"]


def write_gold_csv(rows: list[GoldRow], path: Path) -> None:
    """The reviewed set, one row per pair, unlabelled pairs kept with kind=''."""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for r in rows:
            d = asdict(r)
            d["kind"] = d["kind"] or ""
            w.writerow({k: d[k] for k in GOLD_COLUMNS})


def harness_rows(rows: list[GoldRow], keys: dict[tuple[str, str], str]) -> list[dict]:
    """Rows in astor.eval.accuracy gold format, joined to catalog ids by (name, brand)."""
    out = []
    for r in rows:
        if r.kind is None:
            continue
        try:
            a_key, b_key = keys[(r.a, r.brand_a)], keys[(r.b, r.brand_b)]
        except KeyError as e:
            raise UnresolvedProduct(f"pair {r.pair_id}: no catalog id for {e.args[0]!r}") from e
        out.append({"a_key": a_key, "b_key": b_key, "kind": r.kind, "pair_id": r.pair_id})
    return out
