"""Ingestion-quality audit: what did the catalog record vs. what the source said?

Usage:
    python -m scripts.audit_ingestion            # summary
    python -m scripts.audit_ingestion --samples  # with example rows
    python -m scripts.audit_ingestion --json     # machine-readable

Reports per-field population, placeholder contamination, data that is present in
one column but never reached its own, brand-normalization collisions, encoding
damage, and internal keys that escape into the embedded text.

Read-only. The rules live in `astor.catalog.audit` and are unit-tested; this file
only queries and formats, so the report can never drift from the tested logic.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from sqlalchemy import func, select

from astor.catalog import audit
from astor.db.base import session_scope
from astor.db.models import Product, SupplierOffer


def _pct(n: int, total: int) -> str:
    return f"{100.0 * n / total:5.1f}%" if total else "    —"


def collect(session) -> dict:
    products = session.scalars(select(Product)).all()
    total = len(products)
    out: dict = {"products": total, "fields": {}, "findings": {}, "samples": {}}

    # --- per-field population, counting placeholders as absent ---
    for field in ("name", "brand", "mpn", "category"):
        blank = sum(1 for p in products if audit.is_placeholder(getattr(p, field)))
        out["fields"][field] = {"populated": total - blank, "blank": blank}

    # --- data present in the name but never extracted to its own column ---
    # Two tiers: parenthesised codes are safe to auto-fill; bare code-shaped
    # tokens share their shape with compound and analyte names (AZD8055, CA125)
    # and must be reviewed, or the +0.50 identity rule fires on a false match.
    recoverable = [(p.name, code) for p in products
                   if (code := audit.recoverable_mpn(p.name, p.mpn))]
    possible = [(p.name, code) for p in products
                if (code := audit.possible_mpn(p.name, p.mpn))]
    out["findings"]["recoverable_mpn"] = len(recoverable)
    out["findings"]["possible_mpn_needs_review"] = len(possible)
    out["samples"]["recoverable_mpn"] = [{"code": c, "name": n[:62]} for n, c in recoverable[:5]]
    out["samples"]["possible_mpn_needs_review"] = [
        {"code": c, "name": n[:62]} for n, c in possible[:5]]

    # --- brand normalization collisions ---
    collisions = audit.brand_collisions(p.brand for p in products)
    counts = Counter(p.brand for p in products)
    out["findings"]["brand_collisions"] = len(collisions)
    out["samples"]["brand_collisions"] = [
        {"key": k, "spellings": {s: counts[s] for s in v}} for k, v in collisions.items()
    ]

    # --- encoding damage in customer-facing names ---
    defects = [(str(p.id), p.name, d) for p in products if (d := audit.encoding_defects(p.name))]
    out["findings"]["encoding_defects"] = len(defects)
    out["findings"]["encoding_defects_by_kind"] = dict(
        Counter(kind for _, _, kinds in defects for kind in kinds))
    out["samples"]["encoding_defects"] = [
        {"kinds": d, "name": n[:66]} for _, n, d in defects[:6]
    ]

    # --- placeholder values stored inside specs ---
    ph = Counter()
    internal = Counter()
    for p in products:
        for key, value in (p.specs or {}).items():
            if audit.is_placeholder(str(value)):
                ph[f"{key}={value}"] += 1
        for key in audit.internal_spec_keys(p.specs):
            internal[key] += 1
    out["findings"]["spec_placeholder_values"] = sum(ph.values())
    out["samples"]["spec_placeholder_values"] = ph.most_common(5)
    out["findings"]["internal_spec_keys"] = sum(internal.values())
    out["samples"]["internal_spec_keys"] = internal.most_common(5)

    # --- offers: the commercial fields ---
    offers = session.scalar(select(func.count(SupplierOffer.id))) or 0
    out["offers"] = offers
    out["findings"]["offer_cost_zero"] = session.scalar(
        select(func.count(SupplierOffer.id)).where(SupplierOffer.cost == 0)) or 0
    out["findings"]["offer_lead_time_null"] = session.scalar(
        select(func.count(SupplierOffer.id))
        .where(SupplierOffer.lead_time_days.is_(None))) or 0
    return out


def render(r: dict, samples: bool) -> str:
    total, offers = r["products"], r["offers"]
    L = [f"INGESTION AUDIT — {total:,} products, {offers:,} offers", "=" * 72, "",
         "FIELD POPULATION", "  field       populated        blank"]
    for field, v in r["fields"].items():
        L.append(f"  {field:<10} {v['populated']:>8,} {_pct(v['populated'], total)}"
                 f"   {v['blank']:>7,} {_pct(v['blank'], total)}")

    f = r["findings"]
    L += ["", "FINDINGS", "  " + "-" * 68]
    rows = [
        ("catalogue code in name, safe to auto-fill mpn", f["recoverable_mpn"], total),
        ("code-shaped token in name, needs review first", f["possible_mpn_needs_review"], total),
        ("brand spellings that denote one supplier", f["brand_collisions"], None),
        ("names with encoding damage", f["encoding_defects"], total),
        ("placeholder values stored in specs", f["spec_placeholder_values"], None),
        ("internal (_prefixed) spec keys shipped to the vector", f["internal_spec_keys"], None),
        ("offers priced at zero", f["offer_cost_zero"], offers),
        ("offers with no lead time", f["offer_lead_time_null"], offers),
    ]
    for label, n, denom in rows:
        share = f"  {_pct(n, denom)}" if denom else ""
        L.append(f"  {label:<52} {n:>7,}{share}")
    if f["encoding_defects_by_kind"]:
        L.append(f"    by kind: {f['encoding_defects_by_kind']}")

    if samples:
        L += ["", "SAMPLES", "  " + "-" * 68]
        for key, rowset in r["samples"].items():
            if not rowset:
                continue
            L.append(f"  {key}:")
            for item in rowset:
                L.append(f"    {item}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="include example rows")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    with session_scope() as session:
        report = collect(session)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json
          else render(report, args.samples))


if __name__ == "__main__":
    main()
