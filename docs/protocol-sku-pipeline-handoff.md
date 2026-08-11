# Protocol → SKU pipeline — Handoff

> **Status:** 2026-08-11. The offline protocol-ingest slice (§9) is built, run, and
> loaded end-to-end: protocols.io harvested → loaded → materials extracted → matched
> to the product catalog. Complements `docs/protocol-sourcing-handoff.md` (licensing),
> `docs/ARCHITECTURE.md` §8–10, and the specs/plans under `docs/superpowers/`.

## What this session delivered

A working vertical slice of the Plane-2 catalog-grounding pipeline (see the diagram
artifact and `ARCHITECTURE.md` §9), all on `main`:

1. **Harvester** (`src/astor/protocols/`, `scripts/harvest_protocols.py`) — double-locked
   protocols.io adapter, per-category, review-ranked, resumable. Live API quirks pinned
   in code (v3 params, URL-based pagination via `total_pages`, short-term search).
2. **Persistence + licence provenance** (`persistence.py`, migration `0004`) —
   `serving_basis` stamps the commercial-licence authorization per row so UNKNOWN-licence
   protocols.io content is servable and auditable (INV PI-2). Default fails closed.
3. **Material extraction** (`extraction.py`, `--extract-materials`) — LLM role-classify +
   procurement filter. Dropped 12,412 raw lines → 6,425 purchasable.
4. **Materials → SKU matching** (`material_matcher.py`, migration `0005`,
   `scripts/match_materials.py`) — exact catalog (brand+mpn) then semantic pgvector ANN,
   confident-only, best-per-material. Writes the bidirectional `protocol_material_links`.

## Live DB state (this machine only — not in git)

- **862 protocols** loaded, servable, `serving_basis='commercial-licence:pio-approval-2026-08'`,
  across 9 categories (western_blot, rt_qpcr, elisa, cell_culture_transfection,
  protein_purification, immunoprecipitation, enzyme_inhibitor_assay,
  nucleic_acid_extraction, cloning_protein_expression).
- **16,016 products**, all with real Voyage `voyage-3` embeddings.
- **827 `protocol_material_links`** — 314 distinct protocols, 319 products, 25 exact /
  802 substitute, all `reviewed=false` (pending §9.11 human review).

The corpus JSON (`data/raw/protocols_io/`, 862 payloads) and the DB are local; the code,
thresholds, and specs are pushed. A fresh DB re-does load → extract → match.

## The precision/coverage dial

`material_substitute_threshold` (config, `.env`-overridable) is the one knob:

| Threshold | Protocols linked | Links | Note |
|---|---|---|---|
| 0.70 | 472 / 567 (83%) | 2,473 | more reach, a wrong-match tail at 0.70–0.73 |
| **0.75 (current)** | 314 / 567 (55%) | 827 | trimmed tail, high precision |

Lowering re-adds links idempotently (upsert). Raising leaves stale lower-confidence rows
(upsert never deletes) — truncate + re-match if you raise it.

## Run order (needs Docker + Postgres + Voyage/Anthropic keys)

```
docker compose up -d db            # start the pgvector DB
# migrations diverged on the dev DB — protocol tables were created via
# Base.metadata.create_all(engine), NOT alembic (see below)
python -m scripts.load_protocols --serving-basis "<ref>" --extract-materials
python -m scripts.match_materials --dry-run   # counts, no writes
python -m scripts.match_materials             # commit links
```

## Gotchas hit and fixed (so they don't resurface)

- **Diverged alembic history:** the dev DB records a phantom `0002_pack_size_text` and
  lacks the protocol tables, so `alembic upgrade head` fails. Protocol tables were created
  with `Base.metadata.create_all(engine)` (idempotent, only missing tables) — the sanctioned
  dev-DB pattern per the `0003` migration note. Do NOT try to reconcile alembic here.
- **NUL bytes** in protocols.io Draft.js prose broke Postgres inserts — stripped at the
  mapping boundary (`_no_nul`).
- **scipy/numpy ABI** mismatch broke the Voyage import chain — fixed by aligning
  scipy 1.17 / numpy 1.26 in the local env.
- **Voyage rate limit** (3 RPM) until a payment method was added — free tokens still cover
  this volume.

## Next (in value order)

1. **Human review of the 827 links** — flip `reviewed=true` on the good ones; only reviewed
   links go customer-facing. This is the §9.11 gate.
2. **Completeness checklist (§9.7 — the moat)** — per-category required-role checklists +
   gap detection. Not built; highest value.
3. **BoM / ProtocolTemplate layer (§8)** — elicitation gate, quantity rules, requirement
   typing above the raw links.
4. **Category coverage / cap** — protocols.io approved ≤1,000 records (treat as flexible,
   may rise); room to deepen categories or add IHC/IF, NGS library prep, standard PCR.
