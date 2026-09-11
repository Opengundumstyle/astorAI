"""Build the equivalence gold set from the domain expert's returned workbook.

Usage:
    python -m scripts.build_equivalence_gold                # parse + report only
    python -m scripts.build_equivalence_gold --db           # also join to catalog ids,
                                                            # write data/eval/equivalence_*.csv,
                                                            # and score the live matcher on it

Inputs  docs/curation/equivalence-pairs-mary.xlsx   (Mary's verdicts, one sheet)
Outputs docs/curation/equivalence-gold.csv          (every pair, parsed kind + her note)
        data/eval/equivalence_gold.csv              (a_key,b_key,kind — harness format, --db)
        data/eval/equivalence_products.csv          (key,category,name,brand,mpn,specs, --db)

With --db the script also scores the pairs with the SAME confidence formula the
live matcher uses (astor.catalog.scoring) on the embeddings already stored in
the catalog, so no re-embedding and no API key is needed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from sqlalchemy import select

from astor.catalog import scoring
from astor.config import settings
from astor.eval import equivalence_gold as eg
from astor.eval.accuracy import cosine, match_metrics

XLSX = Path("docs/curation/equivalence-pairs-mary.xlsx")
GOLD_CSV = Path("docs/curation/equivalence-gold.csv")
HARNESS_GOLD = Path("data/eval/equivalence_gold.csv")
HARNESS_PRODUCTS = Path("data/eval/equivalence_products.csv")


def report(rows: list[eg.GoldRow]) -> None:
    cmp = eg.compare_models(rows)
    print(f"pairs {len(rows)}  labelled {cmp.labelled}  unlabelled {len(rows) - cmp.labelled}")
    print(f"expert sided with: model1 {cmp.model1_agrees}  model2 {cmp.model2_agrees}  neither {cmp.neither}")
    print(f"kappa vs expert:   model1 {cmp.kappa_model1:.3f}  model2 {cmp.kappa_model2:.3f}")
    print(f"neither: {' '.join(cmp.neither_ids)}")
    print("\nper rule (exact / substitute / none / unlabelled -> majority):")
    for rule, s in eg.rule_summary(rows).items():
        print(f"  {rule:<18} {s['exact']:>3} {s['substitute']:>3} {s['none']:>3} {s['unlabelled']:>3}  -> {s['majority']}")
    dups = eg.duplicate_pairs(rows)
    if dups:
        print(f"\nduplicate pairs: {dups}")


def resolve_keys(rows: list[eg.GoldRow]) -> tuple[dict[tuple[str, str], str], dict[str, scoring.ProductView], dict[str, np.ndarray]]:
    """(name, brand) -> product id, deterministic on duplicate catalog rows (lowest id)."""
    from astor.db.base import session_scope
    from astor.db.models import Product

    wanted = {(r.a, r.brand_a) for r in rows} | {(r.b, r.brand_b) for r in rows}
    keys: dict[tuple[str, str], str] = {}
    views: dict[str, scoring.ProductView] = {}
    vecs: dict[str, np.ndarray] = {}
    ambiguous = 0
    with session_scope() as s:
        for name, brand in sorted(wanted):
            hits = s.scalars(
                select(Product).where(Product.name == name, Product.brand == brand).order_by(Product.id)
            ).all()
            if not hits:
                continue
            if len(hits) > 1:
                ambiguous += 1
            p = hits[0]
            keys[(name, brand)] = str(p.id)
            views[str(p.id)] = scoring.ProductView(
                category=p.category, name=p.name, brand=p.brand, mpn=p.mpn, specs=dict(p.specs or {}))
            if p.embedding is not None:
                vecs[str(p.id)] = np.asarray(p.embedding, dtype=float)
    print(f"\ncatalog join: {len(keys)}/{len(wanted)} products resolved, {ambiguous} had duplicate catalog rows (lowest id kept)")
    return keys, views, vecs


def score_live_matcher(harness: list[dict], views, vecs) -> None:
    """What the production confidence formula says about the pairs the expert settled."""
    confs, gold = [], []
    for h in harness:
        a, b = views[h["a_key"]], views[h["b_key"]]
        confs.append(scoring.confidence(cosine(vecs[h["a_key"]], vecs[h["b_key"]]), a, b))
        gold.append(h["kind"])
    ex, sub = settings.equiv_exact_threshold, settings.equiv_substitute_threshold
    print(f"\nlive matcher on the expert-settled pairs (exact>={ex}, substitute>={sub}):")
    m = match_metrics([scoring.classify(c, ex, sub) or "none" for c in confs], gold)
    print(f"  {m}")
    print("  substitute-threshold sweep (exact fixed):")
    print("  thr    prec   recall  f1     kind_acc")
    for thr in np.arange(0.70, 1.001, 0.02):
        m = match_metrics([scoring.classify(c, ex, float(thr)) or "none" for c in confs], gold)
        print(f"  {thr:.2f}   {m['precision']:.3f}  {m['recall']:.3f}  {m['f1']:.3f}  {m['kind_accuracy']}")
    lo, hi = min(confs), max(confs)
    print(f"  confidence range on this set: {lo:.3f}..{hi:.3f}  "
          f"(gold none: {sum(1 for g in gold if g == 'none')}, positive: {sum(1 for g in gold if g != 'none')})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", type=Path, default=XLSX)
    ap.add_argument("--db", action="store_true", help="join to catalog ids and score the live matcher")
    args = ap.parse_args()

    rows = eg.load_xlsx(args.xlsx)
    eg.write_gold_csv(rows, GOLD_CSV)
    print(f"wrote {GOLD_CSV}")
    report(rows)

    if not args.db:
        return
    keys, views, vecs = resolve_keys(rows)
    harness = eg.harness_rows(rows, keys)
    HARNESS_GOLD.parent.mkdir(parents=True, exist_ok=True)
    with HARNESS_GOLD.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["a_key", "b_key", "kind", "pair_id"])
        w.writeheader()
        w.writerows(harness)
    used = {h["a_key"] for h in harness} | {h["b_key"] for h in harness}
    with HARNESS_PRODUCTS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["key", "category", "name", "brand", "mpn", "specs"])
        w.writeheader()
        for k in sorted(used):
            v = views[k]
            w.writerow({"key": k, "category": v.category, "name": v.name, "brand": v.brand or "",
                        "mpn": v.mpn or "", "specs": json.dumps(v.specs, ensure_ascii=False)})
    print(f"wrote {HARNESS_GOLD} ({len(harness)} pairs) and {HARNESS_PRODUCTS} ({len(used)} products)")
    score_live_matcher(harness, views, vecs)


if __name__ == "__main__":
    main()
