"""Equivalence-matching accuracy harness.

Measures the SAME scoring logic the live matcher uses (via astor.catalog.scoring)
against a labelled gold set, without needing a database. Run it with a real
embedder to get the number that decides whether equivalence matching is good
enough for your category -- the core risk this project hinges on.

Gold format (CSV):  a_key,b_key,kind     kind in {exact, substitute, none}
Products (CSV):     key,category,name,brand,mpn,specs(json)
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from astor.catalog import scoring
from astor.catalog.embeddings import Embedder
from astor.catalog.normalization import NormalizedProduct, canonical_text

_POSITIVE = {"exact", "substitute"}


# ---------------------------------------------------------------- pure metrics #
def cosine(u: np.ndarray, v: np.ndarray) -> float:
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu == 0 or nv == 0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def match_metrics(pred: list[str], gold: list[str]) -> dict:
    """Binary match detection (positive = exact|substitute) + kind accuracy."""
    tp = fp = fn = tn = 0
    kind_correct = kind_total = 0
    for p, g in zip(pred, gold):
        p_pos, g_pos = p in _POSITIVE, g in _POSITIVE
        if p_pos and g_pos:
            tp += 1
        elif p_pos and not g_pos:
            fp += 1
        elif not p_pos and g_pos:
            fn += 1
        else:
            tn += 1
        if g_pos:
            kind_total += 1
            if p == g:
                kind_correct += 1
    precision, recall, f1 = prf(tp, fp, fn)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "kind_accuracy": round(kind_correct / kind_total, 4) if kind_total else None,
        "pairs": len(pred),
    }


# ---------------------------------------------------------------- harness #
@dataclass
class GoldPair:
    a_key: str
    b_key: str
    kind: str


@dataclass
class EvalReport:
    thresholds: tuple[float, float]
    metrics: dict
    sweep: list[dict] = field(default_factory=list)

    def render(self) -> str:
        m = self.metrics
        lines = [
            f"thresholds  exact>={self.thresholds[0]}  substitute>={self.thresholds[1]}",
            f"pairs={m['pairs']}  tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']}",
            f"precision={m['precision']}  recall={m['recall']}  f1={m['f1']}  "
            f"kind_accuracy={m['kind_accuracy']}",
        ]
        if self.sweep:
            lines.append("\nsubstitute-threshold sweep:")
            lines.append("  thr    precision  recall   f1")
            for row in self.sweep:
                lines.append(
                    f"  {row['substitute_threshold']:.2f}   {row['precision']:.3f}"
                    f"      {row['recall']:.3f}    {row['f1']:.3f}"
                )
        return "\n".join(lines)


def _load_products(path: Path) -> dict[str, scoring.ProductView]:
    out: dict[str, scoring.ProductView] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            specs = json.loads(row["specs"]) if row.get("specs") else {}
            out[row["key"]] = scoring.ProductView(
                category=row.get("category", ""), name=row.get("name", ""),
                brand=row.get("brand") or None, mpn=row.get("mpn") or None, specs=specs,
            )
    return out


def _load_gold(path: Path) -> list[GoldPair]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [GoldPair(r["a_key"], r["b_key"], r["kind"].strip()) for r in csv.DictReader(fh)]


def _text(v: scoring.ProductView) -> str:
    return canonical_text(
        NormalizedProduct(category=v.category, name=v.name, brand=v.brand, mpn=v.mpn, specs=v.specs)
    )


def evaluate(
    products: dict[str, scoring.ProductView],
    gold: list[GoldPair],
    embedder: Embedder,
    exact_threshold: float = 0.92,
    substitute_threshold: float = 0.80,
    sweep: bool = True,
) -> EvalReport:
    keys = list(products)
    vecs = {k: np.asarray(e) for k, e in zip(keys, embedder.embed([_text(products[k]) for k in keys]))}

    # Precompute similarity + base confidence per gold pair once.
    rows = []
    for gp in gold:
        a, b = products[gp.a_key], products[gp.b_key]
        sim = cosine(vecs[gp.a_key], vecs[gp.b_key])
        conf = scoring.confidence(sim, a, b)
        rows.append((conf, gp.kind))

    def metrics_at(exact_thr: float, sub_thr: float) -> dict:
        pred = [scoring.classify(conf, exact_thr, sub_thr) or "none" for conf, _ in rows]
        gold_labels = [k for _, k in rows]
        return match_metrics(pred, gold_labels)

    report = EvalReport(
        thresholds=(exact_threshold, substitute_threshold),
        metrics=metrics_at(exact_threshold, substitute_threshold),
    )
    if sweep:
        for thr in [round(x, 2) for x in np.arange(0.50, 0.96, 0.05)]:
            report.sweep.append({"substitute_threshold": thr, **metrics_at(exact_threshold, thr)})
    return report


def run(products_csv: Path, gold_csv: Path, embedder: Embedder, **kw) -> EvalReport:
    return evaluate(_load_products(products_csv), _load_gold(gold_csv), embedder, **kw)
