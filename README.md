# AstorScientific — M1

Catalog ingestion + equivalence matcher for the AstorScientific platform: an
AI-native, PO-native procurement marketplace for life-science products built on
a **distributor / merchant-of-record** model (Astor buys upstream and resells).

This repo is **Milestone 1** of the system-design roadmap: the schema spine, the
catalog ingestion module, and the China↔US equivalence matcher — the project's
highest-risk subsystem — built first so its accuracy can be measured early.

## What's here

```
src/astor/
  config.py              # all config from env (twelve-factor)
  db/
    base.py              # engine + session_scope (stateless seam)
    models.py            # the data model spine (see invariants below)
  catalog/
    schemas.py           # pipeline DTOs
    extraction.py        # pluggable: StructuredExtractor (CSV/XLSX) + LLMExtractor (PDF/HTML)
    normalization.py     # raw -> canonical (pure functions)
    ingestion.py         # discrete, idempotent steps (queue-ready)
    embeddings.py        # pluggable embedder (DevEmbedder offline; Voyage/OpenAI real)
    matcher.py           # equivalence engine: ANN candidate-gen + rule scoring
  pricing/
    landed_cost.py       # transparent landed-cost breakdown (stored on order lines)
migrations/              # Alembic; 0001 = full initial schema (incl. pgvector)
scripts/ingest_catalog.py
tests/test_logic.py      # pure-logic tests (no DB)
data/sample_supplier_cn.csv
```

## Schema invariants (the no-refactor list)

1. `Product` (canonical facts) is never merged with `SupplierOffer` (who sells it, at what cost).
2. `OrderLine` -> `Product` (Astor sets price); `UpstreamPoLine` -> `SupplierOffer` (what Astor buys).
3. `Equivalence` is first-class data (confidence + kind), not logic buried in code.
4. `landed_cost` is a stored JSONB breakdown, never a scalar.
5. `tenant_id` on tenant-scoped rows from day one.
6. Natural unique keys make ingestion idempotent (safe re-runs).

## Scaling seams already in place

- Entity resolution is sub-quadratic: ANN candidate generation via pgvector HNSW, not pairwise.
- Ingestion is split into discrete idempotent steps — a queue/worker fleet can drive them unchanged.
- Stateless sessions per unit of work — app tier scales horizontally by adding instances.
- Idempotency keys on orders; transactional upserts throughout.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set DATABASE_URL; keys optional for offline runs

# Postgres with the pgvector + pgcrypto extensions (the migration enables them):
createdb astor
alembic upgrade head

# Smoke test: ingest the sample catalog and run matching
python -m scripts.ingest_catalog --file data/sample_supplier_cn.csv --supplier "Sample CN" --region CN
```

## What is real vs. stubbed

**Real:** schema + migration, the data model, idempotent CSV/XLSX ingestion,
normalization, the matching pipeline (ANN candidate generation + rule scoring +
persisted equivalences with confidence), landed-cost breakdown.

**Stubbed / needs wiring before trusting output:**
- `DevEmbedder` is deterministic and offline — *not* semantically meaningful.
  Set `EMBEDDINGS_PROVIDER=voyage` (+ key) for real match quality.
- `LLMExtractor` needs `ANTHROPIC_API_KEY` (only for PDF/HTML catalogs).
- Duty rates in `landed_cost` are a placeholder table — replace with real
  HS-code-driven classification before quoting.

## Run tests

```bash
pip install -e ".[dev]"
pytest          # pure-logic tests, no database required
```
