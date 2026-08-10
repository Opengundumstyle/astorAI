# Materials → SKU Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link each protocol material to the Astor product SKU it corresponds to (exact catalog match, then semantic fallback), persisting confident links in a bidirectional table.

**Architecture:** Mirror the existing `catalog/matcher.py` (embedding + pgvector cosine ANN → `scoring.confidence`/`classify`). The new `material_matcher.py` computes matches over **injectable `exact_lookup`/`ann_candidates` seams** (default = real SQL) so the orchestration is fully offline-testable; a separate `persist_links` upserts into a new `ProtocolMaterialLink` table. A CLI runs it across servable protocols.

**Tech Stack:** Python 3.11, SQLAlchemy 2 + pgvector, pydantic, pytest. Reuses `astor.catalog.scoring`, `astor.catalog.embeddings`, `astor.config.settings`. No new dependencies.

## Global Constraints

- **No new dependencies.** Reuse `scoring`, `get_embedder`, pgvector, existing thresholds.
- **Reuse thresholds unchanged:** `settings.equiv_exact_threshold`, `settings.equiv_substitute_threshold`, `settings.equiv_candidates`. No new config.
- **Confident links only:** persist `exact` + above-`substitute`-threshold `substitute`; a `None` classification is dropped.
- **Hybrid, exact-first:** if a material has BOTH `vendor` and `catalog_no` and they match a product's `brand`+`mpn` (case-insensitive), that is an `exact`/`method=catalog` link at confidence 1.0 — taken before any embedding.
- **Best-per-material:** at most one link per (protocol, material line) — the single highest-confidence product.
- **Idempotent:** upsert on `uq_protocol_material_link`; re-running refreshes confidence, never duplicates.
- **Per-material isolation:** one material's embedding/query failure is logged and skipped, never aborts the protocol or batch.
- **Servable-only:** only servable protocols carry non-empty materials (persistence gate), so they are the only match set.
- Migration `down_revision` chains from the current head `0004_protocol_serving_basis`.

---

### Task 1: `ProtocolMaterialLink` model + migration 0005

**Files:**
- Modify: `src/astor/db/models.py` (add model after `Protocol`)
- Create: `migrations/versions/0005_protocol_material_links.py`
- Test: `tests/test_material_matcher.py` (new)

**Interfaces:**
- Produces: `ProtocolMaterialLink` with columns `id, protocol_id, product_id, material_name, confidence, kind, method, reviewed` + timestamps; `__tablename__ = "protocol_material_links"`; unique constraint `uq_protocol_material_link` on `(protocol_id, product_id, material_name)`; check `ck_protocol_material_link_kind`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_material_matcher.py
from astor.db.models import ProtocolMaterialLink

def test_link_table_shape():
    t = ProtocolMaterialLink.__table__
    assert t.name == "protocol_material_links"
    cols = set(t.columns.keys())
    assert {"protocol_id", "product_id", "material_name",
            "confidence", "kind", "method", "reviewed"} <= cols
    constraints = {c.name for c in t.constraints}
    assert "uq_protocol_material_link" in constraints
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_material_matcher.py::test_link_table_shape -v`
Expected: FAIL (`ImportError: cannot import name 'ProtocolMaterialLink'`)

- [ ] **Step 3: Implement the model**

In `src/astor/db/models.py`, after the `Protocol` class (imports `ForeignKey`, `Float`, `String`, `Text`, `CheckConstraint`, `UniqueConstraint`, `func`, `_uuid_pk`, `TimestampMixin` are all already present in this file):

```python
class ProtocolMaterialLink(Base, TimestampMixin):
    """A confident link from one protocol material line to an Astor product SKU.

    Bidirectional by construction: both FK columns are indexed, so "products used
    in protocol X" and "protocols using product Y" are index scans. `material_name`
    is part of the identity because distinct lines may resolve to the same product
    and each carries its own provenance.
    """

    __tablename__ = "protocol_material_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    protocol_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_name: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)      # exact | substitute
    method: Mapped[str] = mapped_column(String(32), nullable=False)    # catalog | vector+rules
    reviewed: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("protocol_id", "product_id", "material_name",
                         name="uq_protocol_material_link"),
        CheckConstraint("kind in ('exact','substitute')",
                        name="ck_protocol_material_link_kind"),
    )
```

- [ ] **Step 4: Create the migration**

`migrations/versions/0005_protocol_material_links.py`:

```python
"""protocol_material_links: material -> product SKU links

Bidirectional link table produced by the material matcher. Additive.

Revision ID: 0005_protocol_material_links
Revises: 0004_protocol_serving_basis
Create Date: 2026-08-10 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_protocol_material_links"
down_revision = "0004_protocol_serving_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocol_material_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("material_name", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("protocol_id", "product_id", "material_name",
                            name="uq_protocol_material_link"),
        sa.CheckConstraint("kind in ('exact','substitute')",
                           name="ck_protocol_material_link_kind"),
    )
    op.create_index("ix_pml_protocol_id", "protocol_material_links", ["protocol_id"])
    op.create_index("ix_pml_product_id", "protocol_material_links", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_pml_product_id", table_name="protocol_material_links")
    op.drop_index("ix_pml_protocol_id", table_name="protocol_material_links")
    op.drop_table("protocol_material_links")
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_material_matcher.py::test_link_table_shape -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/astor/db/models.py migrations/versions/0005_protocol_material_links.py tests/test_material_matcher.py
git commit -m "feat: ProtocolMaterialLink table + migration 0005"
```

---

### Task 2: Matcher core — views + `match_protocol_materials` (injectable seams)

**Files:**
- Create: `src/astor/protocols/material_matcher.py`
- Test: `tests/test_material_matcher.py`

**Interfaces:**
- Consumes: `scoring.ProductView`, `scoring.confidence`, `scoring.classify`, `get_embedder`, `settings.equiv_*`.
- Produces:
  - `MaterialMatch` dataclass: `product_id: str, material_name: str, confidence: float, kind: str, method: str`.
  - `_material_view(name, vendor, catalog_no) -> scoring.ProductView`.
  - `_view(product) -> scoring.ProductView`.
  - `_find_exact(session, vendor, catalog_no) -> Product | None` (real SQL; default `exact_lookup`).
  - `_ann_candidates(session, vector, limit) -> list[tuple[Product, float]]` (real SQL; default `ann_candidates`, returns `(product, distance)`).
  - `match_protocol_materials(session, protocol, embedder=None, *, exact_lookup=_find_exact, ann_candidates=_ann_candidates) -> list[MaterialMatch]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_material_matcher.py
import types
import pytest
from astor.protocols import material_matcher as mm

def _prod(id, name, brand=None, mpn=None, category="", specs=None):
    return types.SimpleNamespace(id=id, name=name, brand=brand, mpn=mpn,
                                 category=category, specs=specs or {})

def _protocol(materials):
    return types.SimpleNamespace(id="proto-1", materials=materials)

class _Emb:
    def embed(self, texts): return [[0.0] for _ in texts]

def test_exact_catalog_match_wins_without_embedding():
    called = {"embed": 0}
    class _E:
        def embed(self, texts): called["embed"] += 1; return [[0.0]]
    p = _protocol([{"name": "RNeasy Mini Kit", "vendor": "Qiagen", "catalog_no": "74104"}])
    exact = lambda s, v, c: _prod("prod-9", "RNeasy", brand="Qiagen", mpn="74104")
    out = mm.match_protocol_materials(None, p, _E(),
                                      exact_lookup=exact, ann_candidates=lambda *a: [])
    assert len(out) == 1
    assert (out[0].product_id, out[0].kind, out[0].method) == ("prod-9", "exact", "catalog")
    assert out[0].confidence == 1.0
    assert called["embed"] == 0            # exact path never embeds

def test_semantic_keeps_best_confident_candidate():
    p = _protocol([{"name": "TRIzol Reagent"}])
    # two neighbours: one clearly exact-level (dist 0.02 -> sim 0.98), one below substitute
    cands = [(_prod("hi", "TRIzol"), 0.02), (_prod("lo", "something else"), 0.5)]
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cands)
    assert len(out) == 1
    assert out[0].product_id == "hi"
    assert out[0].method == "vector+rules"
    assert out[0].kind in ("exact", "substitute")

def test_semantic_drops_when_all_below_threshold():
    p = _protocol([{"name": "obscure thing"}])
    cands = [(_prod("x", "unrelated"), 0.6), (_prod("y", "also unrelated"), 0.7)]
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cands)
    assert out == []

def test_brand_mpn_agreement_bonus_lifts_to_exact():
    # A semantic candidate that also shares brand+mpn gets +0.50 -> exact.
    p = _protocol([{"name": "Widget", "vendor": "Acme", "catalog_no": "W-1"}])
    # exact_lookup misses (e.g. product brand cased differently), but ANN surfaces it
    cand = [(_prod("z", "Widget", brand="Acme", mpn="W-1"), 0.55)]  # sim 0.45 + 0.50 = 0.95
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cand)
    assert out[0].kind == "exact"

def test_duplicate_material_names_matched_once():
    calls = {"n": 0}
    def ann(s, v, n): calls["n"] += 1; return []
    p = _protocol([{"name": "TRIzol"}, {"name": "trizol"}, {"name": "TRIzol"}])
    mm.match_protocol_materials(None, p, _Emb(), exact_lookup=lambda *a: None, ann_candidates=ann)
    assert calls["n"] == 1                 # case-insensitive dedupe

def test_material_view_maps_vendor_and_catalog_to_brand_mpn():
    v = mm._material_view("X", "Acme", "W-1")
    assert v.brand == "Acme" and v.mpn == "W-1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_material_matcher.py -k "not link_table_shape" -v`
Expected: FAIL (`ModuleNotFoundError: astor.protocols.material_matcher`)

- [ ] **Step 3: Implement the matcher core**

```python
# src/astor/protocols/material_matcher.py
"""Link a protocol's materials to Astor product SKUs.

Same shape as catalog/matcher.py (embedding -> pgvector ANN -> scoring), but
material -> product. Exact catalog identity is taken first; a semantic name match
is the fallback that gives coverage, since most protocols.io materials are
name-only. Candidate generation is injected (`exact_lookup`/`ann_candidates`) so
the orchestration is testable with no database.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select

from astor.catalog import scoring
from astor.catalog.embeddings import Embedder, get_embedder
from astor.config import settings
from astor.db.models import Product

log = logging.getLogger(__name__)


@dataclass
class MaterialMatch:
    product_id: str
    material_name: str
    confidence: float
    kind: str      # 'exact' | 'substitute'
    method: str    # 'catalog' | 'vector+rules'


def _material_view(name: str, vendor: str | None, catalog_no: str | None) -> scoring.ProductView:
    # category unknown for a protocol material; specs empty. vendor->brand,
    # catalog_no->mpn so scoring's +0.50 brand+mpn bonus fires when they agree.
    return scoring.ProductView(category="", name=name, brand=vendor, mpn=catalog_no)


def _view(product) -> scoring.ProductView:
    return scoring.ProductView(
        category=product.category or "", name=product.name,
        brand=product.brand, mpn=product.mpn, specs=product.specs or {},
    )


def _find_exact(session, vendor: str | None, catalog_no: str | None):
    """Deterministic catalog identity, case-insensitive. Only when BOTH present."""
    if not (vendor and catalog_no):
        return None
    return session.scalar(
        select(Product).where(
            func.lower(Product.brand) == vendor.strip().lower(),
            func.lower(Product.mpn) == catalog_no.strip().lower(),
        )
    )


def _ann_candidates(session, vector, limit: int) -> list[tuple]:
    """Top-`limit` products by cosine distance to `vector` (embedding NOT NULL)."""
    rows = session.execute(
        select(Product, Product.embedding.cosine_distance(vector).label("dist"))
        .where(Product.embedding.isnot(None))
        .order_by("dist")
        .limit(limit)
    ).all()
    return [(p, float(d)) for p, d in rows]


def match_protocol_materials(
    session, protocol, embedder: Embedder | None = None,
    *, exact_lookup=_find_exact, ann_candidates=_ann_candidates,
) -> list[MaterialMatch]:
    embedder = embedder or get_embedder()
    seen: set[str] = set()
    matches: list[MaterialMatch] = []

    for m in (protocol.materials or []):
        name = (m.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        vendor, catalog_no = m.get("vendor"), m.get("catalog_no")

        prod = exact_lookup(session, vendor, catalog_no)
        if prod is not None:
            matches.append(MaterialMatch(str(prod.id), name, 1.0, "exact", "catalog"))
            continue

        try:
            vec = embedder.embed([name])[0]
        except Exception as exc:  # noqa: BLE001 — one material must not abort the batch
            log.warning("embed failed for material %r: %s", name, exc)
            continue

        mview = _material_view(name, vendor, catalog_no)
        best: MaterialMatch | None = None
        for cand, dist in ann_candidates(session, vec, settings.equiv_candidates):
            conf = scoring.confidence(1.0 - dist, mview, _view(cand))
            kind = scoring.classify(
                conf, settings.equiv_exact_threshold, settings.equiv_substitute_threshold)
            if kind is None:
                continue
            if best is None or conf > best.confidence:
                best = MaterialMatch(str(cand.id), name, round(conf, 4), kind, "vector+rules")
        if best is not None:
            matches.append(best)

    return matches
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_material_matcher.py -k "not link_table_shape" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/material_matcher.py tests/test_material_matcher.py
git commit -m "feat: material_matcher core (exact-first, semantic fallback, injectable seams)"
```

---

### Task 3: `persist_links` upsert

**Files:**
- Modify: `src/astor/protocols/material_matcher.py`
- Test: `tests/test_material_matcher.py`

**Interfaces:**
- Consumes: `MaterialMatch` (Task 2), `ProtocolMaterialLink` (Task 1).
- Produces: `persist_links(session, protocol_id, matches: list[MaterialMatch]) -> int` — upserts each match on `uq_protocol_material_link`, returns count.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_material_matcher.py
def test_persist_links_upserts_each_match():
    executed = []
    class _Session:
        def execute(self, stmt): executed.append(stmt)
    matches = [
        mm.MaterialMatch("prod-1", "TRIzol", 0.95, "exact", "vector+rules"),
        mm.MaterialMatch("prod-2", "tubes", 0.83, "substitute", "vector+rules"),
    ]
    n = mm.persist_links(_Session(), "proto-1", matches)
    assert n == 2
    assert len(executed) == 2   # one upsert statement per match
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_material_matcher.py::test_persist_links_upserts_each_match -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'persist_links'`)

- [ ] **Step 3: Implement**

Add to `material_matcher.py` (add `from sqlalchemy.dialects.postgresql import insert` and `from astor.db.models import Product, ProtocolMaterialLink` to imports):

```python
def persist_links(session, protocol_id, matches: list[MaterialMatch]) -> int:
    """Idempotent upsert of links for one protocol. Returns the number written."""
    for mm_ in matches:
        session.execute(
            insert(ProtocolMaterialLink)
            .values(
                protocol_id=protocol_id, product_id=mm_.product_id,
                material_name=mm_.material_name, confidence=mm_.confidence,
                kind=mm_.kind, method=mm_.method, reviewed=False,
            )
            .on_conflict_do_update(
                constraint="uq_protocol_material_link",
                set_={"confidence": mm_.confidence, "kind": mm_.kind, "method": mm_.method},
            )
        )
    return len(matches)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_material_matcher.py::test_persist_links_upserts_each_match -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/material_matcher.py tests/test_material_matcher.py
git commit -m "feat: persist_links idempotent upsert of material->product links"
```

---

### Task 4: Runner CLI `scripts/match_materials.py`

**Files:**
- Create: `scripts/match_materials.py`
- Test: manual (documented dry-run; thin shell over tested functions).

**Interfaces:**
- Consumes: `match_protocol_materials`, `persist_links`, `Protocol`, `session_scope`, `get_embedder`.
- Produces: a CLI. `--dry-run` matches and reports counts without committing; `--limit N` bounds the pass.

- [ ] **Step 1: Implement the CLI**

```python
"""Match servable protocols' materials to Astor product SKUs.

Usage:
    python -m scripts.match_materials --dry-run          # counts, no writes
    python -m scripts.match_materials --limit 50         # first 50 protocols
    python -m scripts.match_materials                    # all servable protocols
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from astor.catalog.embeddings import get_embedder
from astor.db.base import session_scope
from astor.db.models import Protocol
from astor.protocols import material_matcher


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap protocols processed")
    ap.add_argument("--dry-run", action="store_true", help="match + count, no DB writes")
    args = ap.parse_args()

    embedder = get_embedder()
    protocols_seen = links = exact = substitute = with_link = 0

    with session_scope() as session:
        stmt = select(Protocol).where(Protocol.servable.is_(True))
        if args.limit:
            stmt = stmt.limit(args.limit)
        for proto in session.scalars(stmt):
            if not proto.materials:
                continue
            protocols_seen += 1
            matches = material_matcher.match_protocol_materials(session, proto, embedder)
            if matches:
                with_link += 1
                links += len(matches)
                exact += sum(1 for m in matches if m.kind == "exact")
                substitute += sum(1 for m in matches if m.kind == "substitute")
                if not args.dry_run:
                    material_matcher.persist_links(session, proto.id, matches)
        if args.dry_run:
            session.rollback()

    print(f"protocols={protocols_seen} with_link={with_link} links={links} "
          f"exact={exact} substitute={substitute} "
          f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and shows help (no DB needed)**

Run: `python -m scripts.match_materials --help`
Expected: argparse usage prints; exit 0. (A real run needs the DB — user runs it.)

- [ ] **Step 3: Commit**

```bash
git add scripts/match_materials.py
git commit -m "feat: match_materials CLI (dry-run/limit, servable protocols only)"
```

---

### Task 5: Full-suite regression

**Files:**
- Test: full `pytest`

**Interfaces:** none — proves the whole suite is green with the new model + matcher.

- [ ] **Step 1: Run the new file**

Run: `pytest tests/test_material_matcher.py -v`
Expected: PASS (Task 1 + Task 2 + Task 3 tests).

- [ ] **Step 2: Run the entire suite**

Run: `pytest -q`
Expected: PASS, no regressions. `ProtocolMaterialLink` importing into `astor.db.models` must not break existing model/metadata tests.

- [ ] **Step 3: Commit (only if anything needed fixing; otherwise skip)**

```bash
git commit -am "test: material matcher suite green"
```

---

## Self-Review

**Spec coverage:**
- `ProtocolMaterialLink` table + migration → Task 1. ✓
- Exact catalog match first → Task 2 (`_find_exact` + exact branch). ✓
- Semantic ANN fallback, reuse scoring/embedder/pgvector → Task 2. ✓
- Confident-only (drop `None`) → Task 2 (`classify` None → skip). ✓
- Best-per-material → Task 2 (`best` tracking). ✓
- `_material_view` adapter (vendor→brand, catalog_no→mpn; +0.50 bonus) → Task 2. ✓
- Idempotent upsert on `uq_protocol_material_link` → Task 3. ✓
- Runner, dry-run, servable-only, limit → Task 4. ✓
- Per-material error isolation → Task 2 (try/except around embed). ✓
- Offline tests with fake embedder + injected seams → Tasks 1-3. ✓

**Deferred (per spec non-goals):** material-specific thresholds, buffer-component resolution, read/API surface, product re-embedding.

**Placeholder scan:** none — every step has complete code.

**Type consistency:** `MaterialMatch(product_id, material_name, confidence, kind, method)` used identically across Tasks 2-4; `match_protocol_materials`/`persist_links`/`_find_exact`/`_ann_candidates` signatures match between definition and callers; constraint name `uq_protocol_material_link` identical in model (Task 1), migration (Task 1), and upsert (Task 3).

**Verified against code:** `embedder.embed([text]) -> [[float]]` (`embeddings.py:29`); `scoring.confidence`/`classify` signatures (`scoring.py:38-47`); `insert(...).on_conflict_do_update(constraint=...)` pattern (`matcher.py:83`); migration head `0004_protocol_serving_basis`; `Product.embedding.cosine_distance(...)` ANN construct (`matcher.py:70`).

**One caveat for the implementer:** `_find_exact` and `_ann_candidates` contain the only real SQL and are NOT exercised by the offline tests (the tests inject fakes in their place). They are thin and mirror `match_product` exactly; they get real coverage when the user runs `scripts/match_materials.py` against the live DB. Do not add a DB-backed test for them in this plan — the project's matcher tests are offline by the same choice.
