# Real-embedding Backfill + Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regenerate the equivalence map on real Voyage `voyage-3` embeddings (replacing the DevEmbedder hash noise all 16,016 products + 310,390 equivalences were built on), with embedding provenance columns, a snapshot, a calibration gate, and a full rematch.

**Architecture:** Follow the repo's established split — pure, unit-testable logic lives in `src/astor/…` (like `eval.accuracy`, which runs with no DB); thin CLIs live in `scripts/…`; DB-touching orchestration is verified by runbook smoke against the live dev DB, not pytest (there are no DB-backed tests in this repo). The embedder is already behind the `Embedder` Protocol, so provider is config.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (mapped_column), Alembic, pgvector, Voyage `voyage-3`, pytest.

## Global Constraints

- Embedding provider is `voyage`, model `voyage-3`, dim **1024** — must match `EMBEDDING_DIM=1024` and the `Vector(1024)` columns. Copied verbatim: `settings.embeddings_provider == "voyage"`.
- The text that gets embedded MUST be `astor.catalog.normalization.canonical_text(NormalizedProduct(...))` — the exact same function the matcher and eval harness use. Never embed raw name/brand ad hoc.
- Alembic history is diverged from the live dev DB (recorded revision `0002_pack_size_text` does not exist in the repo; code head is `0002_protocols`; no `protocols` table in the DB). **Do NOT attempt to reconcile this** — it is a separate follow-up. Apply the schema change to the live dev DB via idempotent raw DDL, and also write the alembic `0003` migration (for fresh DBs) with `down_revision = "0002_protocols"`.
- Provider is verified live: Voyage returns dim 1024, Anthropic responds. `.venv` is the project interpreter (`.venv/bin/python`).
- Destructive steps (overwrite 16k vectors, TRUNCATE 310k equivalences) require the `pg_dump` snapshot to have succeeded first.
- Pass bars (tunable, defined once in `src/astor/eval/gate.py`): precision ≥ 0.90, kind_accuracy ≥ 0.75, sampled exact-rate < 0.40.

---

### Task 1: Provenance columns on `products`

**Files:**
- Modify: `src/astor/db/models.py` (Product class, after the `embedding` column ~line 93)
- Create: `migrations/versions/0003_embedding_provenance.py`
- Verify against: live dev DB via `.venv/bin/python`

**Interfaces:**
- Produces: `Product.embedding_model: str | None`, `Product.embedding_text_hash: str | None` (SQLAlchemy mapped columns; physical columns `products.embedding_model TEXT`, `products.embedding_text_hash TEXT`, both nullable).

- [ ] **Step 1: Add the two mapped columns to the Product model**

In `src/astor/db/models.py`, immediately after the `embedding` mapped_column in `class Product`:

```python
    # Provenance for the embedding above: which model produced it, and a hash of
    # the exact canonical_text() that was embedded. Lets staleness be a query
    # (WHERE embedding_model != 'voyage-3') instead of tribal knowledge.
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_text_hash: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 2: Write the alembic 0003 migration (for fresh DBs)**

Create `migrations/versions/0003_embedding_provenance.py`:

```python
"""embedding provenance: model + text hash on products

Adds nullable `embedding_model` and `embedding_text_hash` to `products` so a
re-embed becomes a targeted query rather than a guess. Additive and nullable.

NOTE: the live dev DB's alembic history is diverged from this repo (it records a
phantom revision and lacks the protocols table). This migration is written for
fresh DBs; the dev DB gets these columns via idempotent DDL (see the plan). Do
not reconcile that divergence here.

Revision ID: 0003_embedding_provenance
Revises: 0002_protocols
Create Date: 2026-07-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_embedding_provenance"
down_revision = "0002_protocols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("embedding_text_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "embedding_text_hash")
    op.drop_column("products", "embedding_model")
```

- [ ] **Step 3: Apply the columns to the live dev DB via idempotent DDL**

Run:

```bash
.venv/bin/python -c "
from sqlalchemy import create_engine, text
from astor.config import settings
e = create_engine(settings.database_url)
with e.begin() as c:
    c.execute(text('ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding_model TEXT'))
    c.execute(text('ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding_text_hash TEXT'))
print('columns ensured')
"
```

Expected: `columns ensured`

- [ ] **Step 4: Verify the columns exist and the model round-trips**

Run:

```bash
.venv/bin/python -c "
from sqlalchemy import create_engine, text
from astor.config import settings
from astor.db.base import session_scope
from astor.db.models import Product
e = create_engine(settings.database_url)
cols = [r[0] for r in e.connect().execute(text(
    \"select column_name from information_schema.columns where table_name='products'\"))]
assert 'embedding_model' in cols and 'embedding_text_hash' in cols, cols
with session_scope() as s:
    p = s.query(Product).first()
    _ = (p.embedding_model, p.embedding_text_hash)  # attribute access must not raise
print('OK columns present + model maps them')
"
```

Expected: `OK columns present + model maps them`

- [ ] **Step 5: Commit**

```bash
git add src/astor/db/models.py migrations/versions/0003_embedding_provenance.py
git commit -m "feat: add embedding provenance columns (model + text hash) to products"
```

---

### Task 2: Backfill pure helpers — text hash + staleness

**Files:**
- Create: `src/astor/catalog/backfill.py`
- Test: `tests/test_backfill.py`

**Interfaces:**
- Consumes: `astor.catalog.normalization.canonical_text`, `astor.catalog.schemas.NormalizedProduct`.
- Produces:
  - `EMBEDDING_MODEL = "voyage-3"` (module constant)
  - `product_canonical_text(product) -> str` — builds `NormalizedProduct` from a `Product` ORM row and returns `canonical_text(...)`.
  - `text_hash(text: str) -> str` — `sha256` hex digest.
  - `is_stale(stored_model, stored_hash, current_model, current_text) -> bool` — True when the stored provenance does not match the current model+text (i.e. needs re-embed).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backfill.py`:

```python
from astor.catalog import backfill


def test_text_hash_is_stable_and_sensitive():
    h1 = backfill.text_hash("Vazyme | 2x Taq Master Mix | molecular_biology")
    h2 = backfill.text_hash("Vazyme | 2x Taq Master Mix | molecular_biology")
    h3 = backfill.text_hash("NEB | 2X Taq PCR Master Mix | molecular_biology")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex


def test_is_stale_true_when_no_provenance():
    assert backfill.is_stale(None, None, "voyage-3", "any text") is True


def test_is_stale_true_when_model_differs():
    txt = "Vazyme | 2x Taq | molecular_biology"
    assert backfill.is_stale("dev", backfill.text_hash(txt), "voyage-3", txt) is True


def test_is_stale_true_when_text_changed():
    old = backfill.text_hash("old text")
    assert backfill.is_stale("voyage-3", old, "voyage-3", "new text") is True


def test_is_stale_false_when_model_and_text_match():
    txt = "Vazyme | 2x Taq | molecular_biology"
    assert backfill.is_stale("voyage-3", backfill.text_hash(txt), "voyage-3", txt) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'astor.catalog.backfill'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/astor/catalog/backfill.py`:

```python
"""Re-embed products with a real provider and stamp provenance.

Pure helpers (hashing, staleness) are unit-tested; the DB orchestration loop is
runbook-verified against the dev DB (this repo has no DB-backed tests). The text
that gets embedded is ALWAYS canonical_text() -- the same string the matcher and
eval harness use -- so vectors and matching agree.
"""
from __future__ import annotations

import hashlib

from astor.catalog.normalization import canonical_text
from astor.catalog.schemas import NormalizedProduct

EMBEDDING_MODEL = "voyage-3"


def product_canonical_text(product) -> str:
    np = NormalizedProduct(
        category=product.category, name=product.name, brand=product.brand,
        mpn=product.mpn, specs=product.specs or {},
    )
    return canonical_text(np)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_stale(stored_model, stored_hash, current_model: str, current_text: str) -> bool:
    """True when the stored embedding provenance does not match current model+text."""
    if stored_model != current_model:
        return True
    return stored_hash != text_hash(current_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_backfill.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/astor/catalog/backfill.py tests/test_backfill.py
git commit -m "feat: backfill helpers — canonical text hash + staleness check"
```

---

### Task 3: Backfill DB orchestration + CLI + snapshot

**Files:**
- Modify: `src/astor/catalog/backfill.py` (add the DB loop)
- Create: `scripts/backfill_embeddings.py`
- Verify against: live dev DB

**Interfaces:**
- Consumes: `is_stale`, `product_canonical_text`, `text_hash`, `EMBEDDING_MODEL` (Task 2); `astor.catalog.embeddings.Embedder`; `astor.db.models.Product`.
- Produces: `backfill_embeddings(session, embedder, *, only_stale: bool, batch_size: int = 128) -> BackfillStats` where `BackfillStats` is a dataclass `(total: int, embedded: int, skipped: int)`. Writes `embedding`, `embedding_model`, `embedding_text_hash` per product.

- [ ] **Step 1: Add the orchestration loop to `src/astor/catalog/backfill.py`**

Append to `src/astor/catalog/backfill.py`:

```python
from dataclasses import dataclass

from sqlalchemy import select

from astor.catalog.embeddings import Embedder
from astor.db.models import Product


@dataclass
class BackfillStats:
    total: int
    embedded: int
    skipped: int


def _batched(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def backfill_embeddings(
    session, embedder: Embedder, *, only_stale: bool, batch_size: int = 128
) -> BackfillStats:
    """Re-embed products and stamp provenance. Idempotent when only_stale=True."""
    products = list(session.execute(select(Product)).scalars())
    todo = []
    for p in products:
        txt = product_canonical_text(p)
        if only_stale and not is_stale(p.embedding_model, p.embedding_text_hash, EMBEDDING_MODEL, txt):
            continue
        todo.append((p, txt))

    embedded = 0
    for chunk in _batched(todo, batch_size):
        vectors = embedder.embed([t for _, t in chunk])
        for (p, txt), vec in zip(chunk, vectors):
            p.embedding = vec
            p.embedding_model = EMBEDDING_MODEL
            p.embedding_text_hash = text_hash(txt)
        session.flush()
        embedded += len(chunk)

    return BackfillStats(total=len(products), embedded=embedded, skipped=len(products) - embedded)
```

- [ ] **Step 2: Write the thin CLI with the snapshot baked into the runbook**

Create `scripts/backfill_embeddings.py`:

```python
"""Re-embed all products with the configured real provider + stamp provenance.

Usage:
    # ALWAYS snapshot first (see plan); then:
    python -m scripts.backfill_embeddings            # only re-embed stale rows
    python -m scripts.backfill_embeddings --all      # force re-embed every row

Refuses to run under the DevEmbedder — that would re-stamp garbage as real.
"""
from __future__ import annotations

import argparse

from astor.catalog import backfill
from astor.catalog.embeddings import get_embedder
from astor.config import settings
from astor.db.base import session_scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", dest="all_rows", action="store_true",
                    help="re-embed every product, not just stale ones")
    args = ap.parse_args()

    embedder = get_embedder()
    if type(embedder).__name__ == "DevEmbedder":
        raise SystemExit(
            f"Refusing to backfill with DevEmbedder (provider={settings.embeddings_provider}). "
            "Set EMBEDDINGS_PROVIDER=voyage and VOYAGE_API_KEY in .env."
        )

    with session_scope() as session:
        stats = backfill.backfill_embeddings(session, embedder, only_stale=not args.all_rows)
    print(f"total={stats.total} embedded={stats.embedded} skipped={stats.skipped} "
          f"model={backfill.EMBEDDING_MODEL}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Take the snapshot (REQUIRED before any write)**

Run:

```bash
docker exec astorai-db-1 pg_dump -U astor astor > "$SCRATCH/astor-pre-backfill-$(date +%Y%m%d-%H%M%S).sql" \
  && ls -la "$SCRATCH"/astor-pre-backfill-*.sql | tail -1
```
(where `$SCRATCH` is the session scratchpad dir)
Expected: a non-empty `.sql` file listed. If pg_dump fails, STOP — do not proceed.

- [ ] **Step 4: Run the full backfill against the live dev DB**

Run: `.venv/bin/python -m scripts.backfill_embeddings --all`
Expected: `total=16016 embedded=16016 skipped=0 model=voyage-3` (embedded count == total on the first real run).

- [ ] **Step 5: Verify provenance was stamped and idempotency holds**

Run:

```bash
.venv/bin/python -c "
from sqlalchemy import create_engine, text
from astor.config import settings
e = create_engine(settings.database_url)
with e.connect() as c:
    n = c.execute(text(\"select count(*) from products where embedding_model='voyage-3' and embedding_text_hash is not null\")).scalar()
    print('stamped voyage-3 rows:', n)
"
# second run must skip everything (idempotent)
.venv/bin/python -m scripts.backfill_embeddings
```
Expected: `stamped voyage-3 rows: 16016`, then `total=16016 embedded=0 skipped=16016 model=voyage-3`.

- [ ] **Step 6: Commit**

```bash
git add src/astor/catalog/backfill.py scripts/backfill_embeddings.py
git commit -m "feat: backfill CLI — re-embed products with voyage-3 + provenance, idempotent"
```

---

### Task 4: Calibration gate — pure decision

**Files:**
- Create: `src/astor/eval/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Produces:
  - `GateBars` dataclass: `min_precision=0.90, min_kind_accuracy=0.75, max_exact_rate=0.40`.
  - `GateResult` dataclass: `passed: bool, reasons: list[str]`.
  - `gate_decision(precision: float, kind_accuracy: float | None, exact_rate: float, bars: GateBars = GateBars()) -> GateResult`. A `None` kind_accuracy fails the gate (no positive pairs scored ⇒ can't trust it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gate.py`:

```python
from astor.eval import gate


def test_gate_passes_when_all_bars_met():
    r = gate.gate_decision(precision=0.95, kind_accuracy=0.80, exact_rate=0.10)
    assert r.passed is True
    assert r.reasons == []


def test_gate_fails_on_low_precision():
    r = gate.gate_decision(precision=0.80, kind_accuracy=0.90, exact_rate=0.10)
    assert r.passed is False
    assert any("precision" in reason for reason in r.reasons)


def test_gate_fails_on_high_exact_rate():
    r = gate.gate_decision(precision=0.95, kind_accuracy=0.90, exact_rate=0.55)
    assert r.passed is False
    assert any("exact_rate" in reason for reason in r.reasons)


def test_gate_fails_when_kind_accuracy_is_none():
    r = gate.gate_decision(precision=0.95, kind_accuracy=None, exact_rate=0.10)
    assert r.passed is False
    assert any("kind_accuracy" in reason for reason in r.reasons)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'astor.eval.gate'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/astor/eval/gate.py`:

```python
"""Calibration gate: decide whether the re-embedded map is safe to auto-rebuild.

Pure decision here; the metric inputs are produced by the harness (labeled) and a
corpus-sample sanity pass (unlabeled) in the orchestrator. The labeled gold set is
tiny (8 pairs) -- this catches grossly-wrong thresholds, it does NOT certify the
16k map. See the design spec's Limitations section.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateBars:
    min_precision: float = 0.90
    min_kind_accuracy: float = 0.75
    max_exact_rate: float = 0.40


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def gate_decision(
    precision: float, kind_accuracy: float | None, exact_rate: float,
    bars: GateBars = GateBars(),
) -> GateResult:
    reasons: list[str] = []
    if precision < bars.min_precision:
        reasons.append(f"precision {precision:.3f} < {bars.min_precision}")
    if kind_accuracy is None:
        reasons.append("kind_accuracy is None (no positive pairs scored)")
    elif kind_accuracy < bars.min_kind_accuracy:
        reasons.append(f"kind_accuracy {kind_accuracy:.3f} < {bars.min_kind_accuracy}")
    if exact_rate > bars.max_exact_rate:
        reasons.append(f"exact_rate {exact_rate:.3f} > {bars.max_exact_rate}")
    return GateResult(passed=not reasons, reasons=reasons)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gate.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/astor/eval/gate.py tests/test_gate.py
git commit -m "feat: calibration gate decision (precision, kind-accuracy, exact-rate bars)"
```

---

### Task 5: Rematch core (truncate + rebuild) + corpus sanity

**Files:**
- Modify: `src/astor/catalog/matcher.py` (add `rematch_all` and a read-only `sample_exact_rate`)
- Verify against: live dev DB

**Interfaces:**
- Consumes: `match_product` (existing), `Product`, `Equivalence`, `get_embedder`, `settings.equiv_*`.
- Produces:
  - `sample_exact_rate(session, embedder, sample_n: int = 500) -> float` — read-only; over a sample of products, the fraction that get at least one candidate classified `exact`. Writes nothing.
  - `rematch_all(session, embedder=None) -> int` — `TRUNCATE equivalences`, then run `match_product` over every product; returns total equivalences written.

- [ ] **Step 1: Add `sample_exact_rate` (read-only) to `src/astor/catalog/matcher.py`**

Append to `src/astor/catalog/matcher.py`:

```python
from sqlalchemy import func, text as sql_text


def sample_exact_rate(session: Session, embedder: Embedder, sample_n: int = 500) -> float:
    """Read-only: fraction of sampled products with >=1 candidate classified exact.

    An absurdly high rate (see the gate's max_exact_rate) means thresholds are too
    loose. Writes nothing to the DB.
    """
    sample = list(
        session.execute(
            select(Product).where(Product.embedding.isnot(None)).order_by(func.random()).limit(sample_n)
        ).scalars()
    )
    if not sample:
        return 0.0
    with_exact = 0
    for product in sample:
        pview = _view(product)
        neighbours = session.execute(
            select(Product, Product.embedding.cosine_distance(product.embedding).label("dist"))
            .where(Product.id != product.id, Product.embedding.isnot(None))
            .order_by("dist")
            .limit(settings.equiv_candidates)
        ).all()
        for cand, dist in neighbours:
            conf = scoring.confidence(1.0 - float(dist), pview, _view(cand))
            if scoring.classify(conf, settings.equiv_exact_threshold, settings.equiv_substitute_threshold) == "exact":
                with_exact += 1
                break
    return with_exact / len(sample)
```

- [ ] **Step 2: Add `rematch_all` to `src/astor/catalog/matcher.py`**

Append to `src/astor/catalog/matcher.py`:

```python
def rematch_all(session: Session, embedder: Embedder | None = None) -> int:
    """Wipe and rebuild the equivalences table on the current embeddings."""
    embedder = embedder or get_embedder()
    session.execute(sql_text("TRUNCATE TABLE equivalences"))
    session.flush()
    product_ids = [row[0] for row in session.execute(select(Product.id)).all()]
    total = 0
    for pid in product_ids:
        total += len(match_product(session, str(pid), embedder))
    log.info("rematch_all wrote %d equivalences over %d products", total, len(product_ids))
    return total
```

- [ ] **Step 3: Verify `sample_exact_rate` runs read-only on the live DB**

Run:

```bash
.venv/bin/python -c "
from astor.db.base import session_scope
from astor.catalog.embeddings import get_embedder
from astor.catalog import matcher
with session_scope() as s:
    before = s.execute(__import__('sqlalchemy').text('select count(*) from equivalences')).scalar()
    rate = matcher.sample_exact_rate(s, get_embedder(), sample_n=200)
    after = s.execute(__import__('sqlalchemy').text('select count(*) from equivalences')).scalar()
    print(f'exact_rate={rate:.3f} equivalences_unchanged={before==after}')
"
```
Expected: an `exact_rate=` between 0 and 1, and `equivalences_unchanged=True` (proves it wrote nothing).

- [ ] **Step 4: Commit**

```bash
git add src/astor/catalog/matcher.py
git commit -m "feat: rematch_all (truncate+rebuild) and read-only sample_exact_rate"
```

---

### Task 6: Orchestrator — snapshot → backfill → gate → rematch

**Files:**
- Create: `scripts/rebuild_map.py`
- Verify against: live dev DB

**Interfaces:**
- Consumes: `backfill.backfill_embeddings` (Task 3), `eval.accuracy.run` (existing), `gate.gate_decision`/`GateBars` (Task 4), `matcher.sample_exact_rate`/`rematch_all` (Task 5).
- Produces: end-to-end runbook. On gate pass → rematch; on gate fail → stop with the numbers.

- [ ] **Step 1: Write the orchestrator**

Create `scripts/rebuild_map.py`:

```python
"""Rebuild the equivalence map on real embeddings, gated.

Sequence (snapshot is the operator's responsibility BEFORE running -- see plan):
  1. backfill embeddings (only-stale by default)
  2. gate:
       (a) labeled harness on data/eval/gold.csv  -> precision, kind_accuracy
       (b) corpus sample sanity                    -> exact_rate
  3. both pass -> TRUNCATE + rematch all; either fails -> stop, print numbers.

Usage:
    python -m scripts.rebuild_map                # gate, auto-proceed if it passes
    python -m scripts.rebuild_map --force-rematch  # skip the gate (manual override)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from astor.catalog import backfill, matcher
from astor.catalog.embeddings import get_embedder
from astor.config import settings
from astor.db.base import session_scope
from astor.eval import gate
from astor.eval.accuracy import run as run_eval

GOLD = Path("data/eval/gold.csv")
PRODUCTS = Path("data/eval/products.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-rematch", action="store_true", help="skip the gate")
    ap.add_argument("--all", dest="all_rows", action="store_true", help="re-embed every row")
    args = ap.parse_args()

    embedder = get_embedder()
    if type(embedder).__name__ == "DevEmbedder":
        raise SystemExit(f"Refusing: DevEmbedder (provider={settings.embeddings_provider}).")

    with session_scope() as session:
        stats = backfill.backfill_embeddings(session, embedder, only_stale=not args.all_rows)
        print(f"[backfill] total={stats.total} embedded={stats.embedded} skipped={stats.skipped}")

    if not args.force_rematch:
        report = run_eval(PRODUCTS, GOLD, embedder,
                          exact_threshold=settings.equiv_exact_threshold,
                          substitute_threshold=settings.equiv_substitute_threshold)
        m = report.metrics
        with session_scope() as session:
            exact_rate = matcher.sample_exact_rate(session, embedder, sample_n=500)
        print(f"[gate] precision={m['precision']} kind_accuracy={m['kind_accuracy']} exact_rate={exact_rate:.3f}")
        result = gate.gate_decision(m["precision"], m["kind_accuracy"], exact_rate)
        if not result.passed:
            print("[gate] FAILED -> not rematching. Reasons:")
            for reason in result.reasons:
                print("   -", reason)
            raise SystemExit(1)
        print("[gate] PASSED -> proceeding to full rematch")

    with session_scope() as session:
        total = matcher.rematch_all(session, embedder)
    print(f"[rematch] equivalences_written={total}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirm the snapshot exists (must predate any destructive run)**

Run: `ls -la "$SCRATCH"/astor-pre-backfill-*.sql | tail -1`
Expected: the snapshot from Task 3 Step 3 is present and non-empty. If missing, take it now before continuing.

- [ ] **Step 3: Dry-run the gate without rematching (inspect the numbers)**

Temporarily observe the gate decision by running the eval harness alone:

Run:

```bash
.venv/bin/python -m scripts.run_eval --products data/eval/products.csv --gold data/eval/gold.csv
```
Expected: a report with real (non-DevEmbedder) `precision`, `recall`, `kind_accuracy`, and a substitute-threshold sweep. Record these numbers.

- [ ] **Step 4: Run the full gated rebuild**

Run: `.venv/bin/python -m scripts.rebuild_map`
Expected sequence in output:
- `[backfill] ... skipped=16016` (already embedded in Task 3) or `embedded=16016` if run fresh
- `[gate] precision=… kind_accuracy=… exact_rate=…`
- either `[gate] PASSED -> proceeding to full rematch` then `[rematch] equivalences_written=<N>`
- or `[gate] FAILED` with reasons and exit 1 (then STOP; bring the numbers back for threshold tuning — do not force).

- [ ] **Step 5: Verify the map was rebuilt on real vectors**

Run:

```bash
.venv/bin/python -c "
from sqlalchemy import create_engine, text
from astor.config import settings
e = create_engine(settings.database_url)
with e.connect() as c:
    n = c.execute(text('select count(*) from equivalences')).scalar()
    kinds = dict(c.execute(text('select kind, count(*) from equivalences group by kind')).all())
    print('equivalences:', n, 'by kind:', kinds)
"
```
Expected: a non-zero count with a sane `exact`/`substitute` split (compare against the old 310,390 — a large change is expected and correct, since the old map was noise).

- [ ] **Step 6: Commit**

```bash
git add scripts/rebuild_map.py
git commit -m "feat: gated map rebuild orchestrator (backfill -> gate -> rematch)"
```

---

## Self-Review

**Spec coverage:**
- §1 provenance columns → Task 1 ✅
- §2 snapshot → Task 3 Step 3 + Task 6 Step 2 ✅
- §3 backfill script (idempotent, `--only-stale`, `canonical_text`) → Tasks 2–3 ✅
- §4 calibration gate (labeled harness + unlabeled corpus sanity, auto-proceed) → Tasks 4–6 ✅
- §5 full rematch (TRUNCATE + rebuild) → Task 5 + Task 6 ✅
- Non-goals (embed-on-ingest, stale CLI) → not planned ✅
- Alembic-isolation constraint → Task 1 (idempotent DDL + 0003 for fresh DBs, no reconciliation) ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows an expected result.

**Type consistency:** `EMBEDDING_MODEL="voyage-3"`, `is_stale`, `text_hash`, `product_canonical_text`, `backfill_embeddings`/`BackfillStats`, `gate_decision`/`GateBars`/`GateResult`, `sample_exact_rate`, `rematch_all` — names/signatures are consistent across Tasks 2→6. The gate reads `report.metrics["precision"]`/`["kind_accuracy"]`, matching `EvalReport.metrics` keys in `astor/eval/accuracy.py`.
