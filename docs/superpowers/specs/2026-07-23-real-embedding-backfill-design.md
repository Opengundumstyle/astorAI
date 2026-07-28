# Real-embedding backfill + provenance — Design

**Date:** 2026-07-23
**Status:** Approved (design), pending implementation plan
**Scope:** A — fix the categorized map now + add provenance columns. Explicitly
defers auto-embed-on-ingest and stale-detection CLI.

## Problem

The live dev DB is fully populated — 16,016 `products` (all with embeddings),
310,390 `equivalences`, 15,988 `supplier_offers` — but every embedding was
produced by `DevEmbedder`, the hash-based pseudo-embedder the code labels *"NOT
semantically meaningful"*, because `EMBEDDINGS_PROVIDER` was `dev`. All 310,390
equivalences were derived from those garbage vectors via pgvector ANN. This is
the "wrong categorized map": nothing is broken, the map was built on noise.

`VOYAGE_API_KEY` and `ANTHROPIC_API_KEY` are now set and smoke-tested live.
Voyage `voyage-3` returns dim 1024, matching `EMBEDDING_DIM=1024` and the
`Vector(1024)` columns confirmed in the live DB (`products.embedding`,
`ix_product_embedding_hnsw`). No column migration needed for dimension.

## Goal

Regenerate the map on real Voyage semantics with sane, sample-validated
thresholds, and add provenance columns so staleness is a query, not tribal
knowledge. A clean rollback path throughout.

## Non-goals (deferred)

- Incremental embed-on-ingest (new products auto-embedding at insert time).
- A stale-detection / re-embed CLI (`--where-model-not voyage-3`).
- A corpus-level labeled ground-truth set (curation work; see Limitations).

## Key facts (verified)

- Embedder sits behind the `Embedder` Protocol (`src/astor/catalog/embeddings.py`);
  provider is config, not code — the engine/adapter thin waist.
- Matcher (`src/astor/catalog/matcher.py`) `ensure_embedding` only embeds when
  `embedding IS NULL`, so re-running ingest will NOT re-embed existing rows.
  A dedicated backfill that force-overwrites is required.
- `canonical_text()` (`src/astor/catalog/normalization.py`) is the single source
  of the text that gets embedded — the backfill and matcher must both use it.
- Eval harness exists: `scripts/run_eval.py` → `astor.eval.accuracy.run`,
  computing precision/recall/F1/kind_accuracy against a labeled gold CSV.
- Gold set is a toy fixture: `data/eval/gold.csv` = 8 pairs / 7 products.
  `docs/curation/categories.csv` = 4-row taxonomy, NOT equivalence labels.

## Design

### 1. Schema migration (`alembic 0003`)

Add to `products`, both nullable:
- `embedding_model TEXT` — e.g. `voyage-3`
- `embedding_text_hash TEXT` — sha256 of the exact `canonical_text()` embedded

Backfilling values for existing rows happens in step 3. Enables
`WHERE embedding_model != 'voyage-3'` staleness queries later.

### 2. Snapshot (safety)

`pg_dump` the `astor` DB → `scratchpad/astor-pre-backfill-<ts>.sql` before any
write. Rollback = `psql < dump`.

### 3. Backfill script (`scripts/backfill_embeddings.py`)

Idempotent, batched:
- Load all products; compute `canonical_text()` per product (same function the
  matcher uses — guarantees the embedded text matches what matching assumes).
- Embed in Voyage batches of 128; write `embedding`, `embedding_model='voyage-3'`,
  `embedding_text_hash`.
- `--only-stale` flag: skip rows whose stored `embedding_text_hash` and
  `embedding_model` already match the current text + model. First run embeds all
  16,016; re-runs are cheap and safe.

### 4. Calibration gate (honest form of "auto-proceed if metrics pass")

Runs automatically after re-embed. Two complementary checks:

- **(a) Labeled — harness.** `run_eval.py` on `data/eval/gold.csv` with Voyage.
  Pass bar: `precision >= 0.90` AND `kind_accuracy >= 0.75`.
  Validates the scoring/threshold logic on 8 pairs — catches grossly-wrong
  thresholds; does NOT certify the 16k map.
- **(b) Unlabeled — corpus sanity.** Sample ~500 products from the re-embedded
  corpus, run the matcher, check the score distribution is sane: exact-match
  rate not absurd (red flag: >40% of sampled products getting an "exact" twin ⇒
  thresholds too loose) and visible separation between exact/substitute/none
  bands.

If BOTH pass → auto-proceed to step 5. If EITHER fails → hard-stop, print the
numbers, wait for human decision (threshold tuning).

Pass bars (starting values, tunable): precision ≥ 0.90, kind_accuracy ≥ 0.75,
sampled exact-rate < 0.40.

### 5. Full rematch

`TRUNCATE equivalences`, then run `matcher.match_product` over all 16,016
products, rebuilding the table on real vectors. Report new equivalence count vs.
the prior 310,390.

## Data flow

```
pg_dump snapshot
  → migration 0003 (add provenance cols)
  → backfill_embeddings.py  (16,016 products: Voyage embed + model + text_hash)
  → calibration gate  (a) harness on gold.csv  (b) corpus-sample sanity
      ├─ both pass → TRUNCATE equivalences → rematch all → report
      └─ either fails → hard-stop, show numbers
```

## Error handling

- Voyage batch failure: fail the batch loudly, do not write partial provenance;
  `--only-stale` re-run resumes the un-embedded rows.
- Snapshot must succeed before any destructive step; abort if `pg_dump` fails.
- Gate failure is a stop, not a crash — the re-embedded vectors are already
  persisted (an improvement on their own); only the rematch is withheld.

## Testing

- Unit: `backfill` provenance write + `--only-stale` skip logic (text_hash match
  ⇒ skipped) on a small fixture.
- Unit: gate pass/fail decision from synthetic metric inputs.
- Integration: run the eval harness on `data/eval/gold.csv` with the real
  embedder; assert it produces a report (numbers recorded, not asserted-exact).

## Limitations (on record)

Scope A yields a map on real semantics with sane thresholds — a large
improvement over hash noise — but NOT a precision-certified map, because no
labeled corpus-level ground truth exists (only the 8-pair fixture). Building one
is curation work (`docs/curation/`). The gate catches gross failures, not
fine-grained corpus precision.

## Production note (why no re-embed on cloud migration)

An embedding is a pure function of (model + version, canonical input text), not
of the DB host. Moving local Docker Postgres → cloud pgvector (e.g. Supabase)
migrates the vectors as data (`pg_dump`/restore); no recompute. Re-embed is
required only on model swap, dimension change, `canonical_text()` change, or
provider swap. The provenance columns added here make that future re-embed a
targeted query rather than a guess.
