# Materials → SKU matching — Design

**Date:** 2026-08-10
**Status:** Approved (design), pending implementation plan
**Scope:** Build (offline-tested) the material→product matcher that links protocol
materials to Astor product SKUs, producing a bidirectional link table. Runs against
the live DB by the user; my sandbox cannot reach Postgres.

## Problem

The harvested corpus (862 protocols across 9 catalog-aligned categories) sits in the
`protocols` table with a `materials` JSONB list (`{name, amount, vendor, catalog_no}`
per line). Nothing connects those materials to the 16,026 Astor products. The product
value — a customer searching a product sees the protocols that use it, and a protocol
page lists the buyable products it needs — requires that link.

The existing product↔product matcher (`src/astor/catalog/matcher.py::match_product`)
already does the hard parts: embedding + pgvector cosine ANN candidate generation →
`scoring.confidence` (vector similarity + structured attribute bonuses) →
`scoring.classify` (exact/substitute by threshold) → upsert `Equivalence` rows. This
feature is the same shape, material→product instead of product→product, and reuses
that machinery unchanged.

## Goal

For each material in a servable protocol, find the Astor product it corresponds to
and persist a confident link, so both directions ("products in protocol X",
"protocols using product Y") are single-index-scan queries.

## Decisions (from brainstorming)

- **Hybrid matching:** exact catalog match first, semantic name fallback second.
  Most protocols.io materials are name-only (no catalog number), so exact alone would
  match almost nothing — the semantic fallback is what gives coverage. Where a catalog
  number IS present, the deterministic exact match is taken and never discarded.
- **Confident links only:** persist `exact` + above-`substitute`-threshold `substitute`;
  drop `None` classifications. A wrong product on a protocol page erodes trust more than
  a missing one.
- **Build offline-testable, user runs against live DB** (same pattern as the harvester).

## Key facts (verified in code)

- `Product` (`db/models.py:80`): `brand`, `mpn` (MPN = manufacturer catalog number),
  `embedding` (`Vector`, HNSW cosine index `ix_product_embedding_hnsw`), unique
  `(brand, mpn)`. Embeddings are real Voyage `voyage-3` vectors (dim 1024).
- `scoring.ProductView(category, name, brand, mpn, specs)` and
  `scoring.confidence(similarity, a, b)` = `similarity + attribute_bonus`, where
  `attribute_bonus` adds **+0.50 when `a.brand==b.brand` and `a.mpn==b.mpn`**, +0.05
  same category, +0.10×spec-agreement (`scoring.py:22-39`). Pure/DB-free — reusable
  directly for a material→product comparison.
- `scoring.classify(conf, exact_thr, substitute_thr)` → `"exact"|"substitute"|None`.
- Thresholds live in config: `equiv_exact_threshold` (0.92), `equiv_substitute_threshold`
  (0.80), `equiv_candidates` (20).
- `matcher.ensure_embedding(session, product, embedder)` embeds only when NULL; the
  material path needs an analogous "embed this short text" call on the embedder.
- Persistence gate: non-servable protocols carry empty `materials`, so iterating
  servable protocols with non-empty materials is the correct and only match set.

## Design

### 1. Data model — `ProtocolMaterialLink` (+ migration `0005`)

```
protocol_material_links
  id            uuid pk
  protocol_id   uuid  FK -> protocols(id) ON DELETE CASCADE, indexed
  product_id    uuid  FK -> products(id)  ON DELETE CASCADE, indexed
  material_name text  not null          -- the material line this link is for
  confidence    float not null
  kind          text  not null          -- 'exact' | 'substitute'
  method        text  not null          -- 'catalog' | 'vector+rules'
  reviewed      bool  not null default false
  (timestamps via TimestampMixin)
  UNIQUE (protocol_id, product_id, material_name)   -- uq_protocol_material_link
  CHECK  kind in ('exact','substitute')             -- ck_protocol_material_link_kind
```

`material_name` is part of the key because one protocol can link to one product for a
specific material line, and two different lines could resolve to the same product; the
triple keeps upserts idempotent without collapsing distinct provenance. Both FK indexes
serve the two customer-facing directions.

### 2. Matcher — `src/astor/protocols/material_matcher.py`

```python
@dataclass
class MaterialMatch:
    product_id: str
    material_name: str
    confidence: float
    kind: str          # 'exact' | 'substitute'
    method: str        # 'catalog' | 'vector+rules'

def _material_view(name, vendor, catalog_no) -> scoring.ProductView:
    # category unknown for a protocol material; specs empty. vendor->brand,
    # catalog_no->mpn so the +0.50 brand+mpn bonus fires when they agree.
    return scoring.ProductView(category="", name=name, brand=vendor, mpn=catalog_no)

def _find_exact(session, vendor, catalog_no) -> Product | None:
    # Deterministic catalog identity, case-insensitive. Only when BOTH present.
    if not (vendor and catalog_no):
        return None
    return session.scalar(select(Product).where(
        func.lower(Product.brand) == vendor.lower(),
        func.lower(Product.mpn) == catalog_no.lower(),
    ))

def _embed_text(embedder, text) -> list[float]:
    # Reuse the catalog embedder on the bare material name.

def match_protocol_materials(session, protocol, embedder=None) -> list[MaterialMatch]:
    # For each material dict in protocol.materials:
    #   1. exact = _find_exact(...); if hit -> upsert(kind=exact, conf=1.0, method=catalog); next material
    #   2. else embed name -> ANN top-(equiv_candidates) over Product.embedding (cosine,
    #      embedding NOT NULL) -> for each: conf = scoring.confidence(1-dist, mview, _view(cand));
    #      kind = scoring.classify(conf, exact_thr, substitute_thr); if kind: upsert(method='vector+rules')
    #   Confident-only: kind is None -> skip.
    # Upsert via insert(...).on_conflict_do_update(constraint='uq_protocol_material_link',
    #      set_={confidence, kind, method}). Returns the MaterialMatch list.
```

- `_view(product)` mirrors `matcher._view` (product → ProductView).
- Duplicate material names within one protocol are de-duplicated before matching so the
  same line isn't embedded/queried twice.
- ANN query is the same construct as `match_product`:
  `select(Product, Product.embedding.cosine_distance(<vec>).label("dist")).where(
  Product.embedding.isnot(None)).order_by("dist").limit(settings.equiv_candidates)`.

### 3. Runner — `scripts/match_materials.py`

```
python -m scripts.match_materials --dry-run   # map+count coverage, no writes
python -m scripts.match_materials             # match all servable protocols, upsert links
```
- Loads servable protocols with non-empty materials (`select(Protocol).where(
  Protocol.servable.is_(True))`, filter `p.materials`), runs `match_protocol_materials`
  per protocol inside `session_scope`, prints: protocols processed, total links,
  exact vs substitute, protocols with ≥1 link, materials with no match.
- `--dry-run` runs the matcher against a read-only session copy path — simplest: run
  matching but never commit (rollback), reporting the counts it *would* write.
- `--limit N` to bound a first pass.

### 4. Error handling

- One material's embedding/query failure is logged and skipped; never aborts the
  protocol or the batch (mirrors the harvester's per-record isolation).
- A protocol with no products in range simply writes zero links — not an error.
- Idempotent: re-running upserts, so a re-run after new products/embeddings refreshes
  confidences without duplicating rows.

### 5. Testing (offline; `tests/test_material_matcher.py`)

Fake embedder (returns a fixed/among-a-small-set vector) + a stub/fake session that
serves canned Products and records upserts — mirroring the existing matcher/persistence
test seams:
- **Exact-catalog path:** a material with vendor+catalog_no equal to a product's
  brand+mpn → one `exact`/`method=catalog` link, confidence 1.0, no embedding call.
- **Semantic path:** a name-only material → ANN returns two canned neighbours (one above
  the substitute threshold, one below) → only the above-threshold one is linked, as
  `substitute`/`method=vector+rules`.
- **Confident-only:** all neighbours below threshold → zero links.
- **`_material_view` adapter:** vendor→brand, catalog_no→mpn so a semantic candidate that
  also shares brand+mpn gets the +0.50 bonus (crosses into `exact`).
- **Dedup:** a protocol listing the same material name twice embeds/queries once.
- **Idempotent upsert:** matching the same protocol twice yields one row per
  (protocol, product, material), with updated confidence.

## Non-goals (deferred)

- Material-specific thresholds (v1 reuses the product `equiv_*` thresholds).
- Buffer-component → constituent-product resolution (extraction marks these
  `buffer_component` and the procurement filter already drops them).
- A read API / frontend surface for "protocols using product Y" — this builds the link
  table; surfacing it is a separate task.
- Re-embedding products; uses the existing Voyage embeddings in place.
