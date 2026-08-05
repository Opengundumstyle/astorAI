# Project State & Roadblocks — Handoff

> **Status:** state-of-the-repo brief, 2026-08-05. Written to hand off where the
> system actually stands, what blocks it, and which blockers are ours to clear
> versus which depend on other people. Complements `ARCHITECTURE.md` (the design),
> `protocol-sourcing-handoff.md` (the licensing analysis), and
> `superpowers/specs/2026-07-29-scoring-model-fix-design.md` (the pending fix).
>
> **This brief corrects two claims that are stale elsewhere in `docs/`.** See §5.

## 1. Where the system stands

Astor is a distributor / merchant-of-record procurement marketplace for
life-science products. This repo is M1–M2: the schema spine, catalog ingestion,
and the China↔US equivalence matcher — built first on purpose, because it is the
highest-risk subsystem and its accuracy needed measuring early.

**Built and working:**

| Area | State |
|---|---|
| Schema spine + Alembic migrations | pgvector, tenant scoping, idempotent natural keys |
| Catalog ingestion | CSV/XLSX structured extractor + LLM extractor (PDF/HTML); Shopify house-catalog source |
| Equivalence matcher | pgvector HNSW ANN candidate-gen + rule scoring, persisted as first-class `Equivalence` rows |
| Landed cost | stored JSONB breakdown, never a scalar |
| Eval | accuracy harness, calibration gate, `rematch_all`, gated `rebuild_map` orchestrator |
| Ops web app | FastAPI + Next.js — dashboard, browse, product detail, role toggle |
| Embeddings | real Voyage `voyage-3` backfill with model + text-hash provenance |
| Protocol lane | `ProtocolsIoSource` (network-gated), `EuropePmcSource` (ungated), licence gate, review ranking |
| Tests | 118 passed, 1 skipped — all pure-logic, no DB required |

**Branch state at time of writing:** `scoring-model-fix` is 5 commits ahead of
`main` and unmerged (design spec + species amendment + brand normalization +
gold-set sampler + labeling instructions). Local `main` was 11 commits ahead of
`origin/main` before this doc.

## 2. Roadblocks

| # | Roadblock | Why it bites |
|---|---|---|
| 1 | **The live equivalence map is untrustworthy** | 93.5% of 314,184 equivalences are labeled `exact`; `sample_exact_rate` ≈ 1.0 against a gate bar of ≤ 0.40. The gate is *expected to fail* today — documented in `rebuild_map.py`. Confirmed wrong *binary* matches, not just wrong labels: Human vs Mouse Kremen-2 at confidence 1.0 (P061). |
| 2 | **The fix is designed, not implemented** | Phases 1–2 of the scoring-model-fix spec are approved with no implementation plan written. Only the sampler and a brand normalizer shipped. |
| 3 | **`mpn` is 0% populated (0 of 16,016)** | The new `exact := brand + mpn` definition is unimplementable until an LLM pass recovers MPNs across the catalog. 16k extraction calls at unmeasured cost, recovery rate, and throughput, plus migration `0004`. **If recovery is low, the design needs revisiting before implementation, not after.** |
| 4 | **Curation → engine has no code path** | See §3. The moat exists as spreadsheets and as nothing else. |
| 5 | **Protocol lane delivers articles, not materials** | See §4. The free ingest lane is built and legal, but stops short of the transform that IS the product. |
| 6 | **Eval measures precision, not recall** | The gate runs against 8 labeled pairs. Even the new 100-pair gold set contains only pairs the matcher already proposed — a passing score never tells us what we fail to find. A recall set is unbuilt and out of scope for threshold calibration. |

Roadblocks 1–3 are one dependency chain and are entirely ours to unblock: no
consensus needed, no external party. Roadblocks 4–5 have external lead times
(a domain expert's hours, a licensing negotiation, possibly counsel) and should
run in parallel rather than queue behind the chain.

## 3. Roadblock 4 in detail — the curation seam

**What the moat is.** ARCHITECTURE §9 stage 7 ("augment completeness") injects
what no protocol source contains: the controls a rigorous run needs but customers
omit, and the error-prone spec constraints on otherwise-obvious items. It reads
`category_completeness_checklists`. That table is the moat — not the model.
`docs/curation/` is its human-editable source, and the scope discipline there is
right: the domain expert authors only the *delta* (~3–6 rows per category, ~1 hr),
never a full material list.

**The content is substantially further along than `docs/` implies.** The four
CSVs cover three launch categories:

- `categories.csv` — western_blot, rt_qpcr, elisa picked with reasoning, plus a
  backlog fourth (cell_culture_transfection)
- `checklist.csv` — 15 delta rows across the three
- `elicitation.csv` — 11 questions, each tagged with what it changes
- `roles.csv` — 13 roles with `lab_usually_owns_it` procurement flags

The biology reads as sound (e.g. the rt_qpcr rows correctly capture NTC, minus-RT,
and reference-gene stability under treatment condition).

**The actual blockers are three, and none is "the checklists aren't written":**

1. **Nothing consumes the files.** Grepping `src/`, `scripts/`, `tests/`,
   `contracts/` for checklist/roles/elicitation returns only two unrelated string
   fields in the engine contract YAML. The `docs/curation/README.md` workflow
   step 4 — "Transform (Zhile): script CSV → engine curated assets" — has no
   script behind it.
2. **The CSVs are untracked in git.** They have not moved since 2026-07-11 and are
   one `git clean` from gone. Deliberately left untracked by this commit: see
   Open actions.
3. **Provenance is unclear.** `docs/curation/README.md` states only the
   western_blot rows are pre-filled as the worked example, but all three
   categories were authored inside the same six-minute window — which reads as one
   bootstrap sitting, not as per-category expert sign-off. If these are drafts,
   then ARCHITECTURE §14 #2 (checklists — one of only two true hard-consensus
   items) is still open and the moat is currently engineering's biology guesses.
   **Confirm before building the transform on top of it.**

**Genuinely still gated on the domain expert:** the 100-pair gold-set labeling
(2–3 hrs, no substitute), domain validation of the checklist rows if they are
drafts, and the canonical-protocol pick per category (`protocol-sourcing-handoff.md`
§7).

## 4. Roadblock 5 in detail — the protocol lane

**The legal structure** (full analysis in `protocol-sourcing-handoff.md`): two
instruments bind independently. A copyright licence governs the work — and
steps/materials/quantities are *facts*, uncopyrightable regardless. A site ToS is
a *contract* governing access, and a contract can forbid what a licence permits.
protocols.io content is CC-BY, but ToS §4.A(vi) forbids downloading data "to make
or populate a database of any kind" and §4.A(xi) forbids automated downloading to
index. **The breach happens at the pull**, before anything is built — which is why
the "separate product-association database" idea does not cure it; it fixes the
copyright axis, and copyright was never the blocker.

That is respected in code: `ProtocolsIoSource.fetch_one` takes
`allow_network=False` by default and raises with a pointer to §10 / §14 #1. The
fast path stays fenced until it is licensed.

**The free lane is built.** `EuropePmcSource` (`src/astor/protocols/sources.py`)
is deliberately *not* network-gated — the OA subset is explicitly provided for text
mining. It queries `(OPEN_ACCESS:Y) AND (LICENSE:"cc by" OR LICENSE:"cc0")` as
defence-in-depth while re-deriving licence per record so `license_gate` remains the
real enforcement, and sorts by citations server-side.

**But the blocker moved rather than cleared.** From the adapter's own docstring:
Europe PMC returns **articles, not step-structured protocols** — `to_raw` fills
attribution, licence and citation count, and `steps`/`materials` come back **empty
by design**. So the free lane yields correctly-licensed, correctly-attributed,
correctly-ranked papers *with no materials in them*.

Turning an OA methods section into steps and materials is an LLM extraction pass.
`LLMMaterialExtractor`, `procurement_filter`, and the `MaterialRole` taxonomy exist
in `protocols/extraction.py` — but `ingestion.py` wires only `map → gate → rank`.
ARCHITECTURE §9 stages 5–11 (derive spec → catalog grounding, quantity rule,
completeness augmentation, attribution binding, requirement typing, validate,
publish) are unbuilt, and the pipeline has never run end-to-end on real Europe PMC
data.

Minor but worth fixing when touched: `LLMMaterialExtractor` pins
`model="claude-opus-4-8"` — a generation behind and expensive for structured
extraction. It should move to a current, cheaper model, the same call the
scoring-model-fix spec made when it picked Haiku for discriminating-key extraction.

## 5. Corrections to stale claims elsewhere in `docs/`

1. **`protocol-sourcing-handoff.md` §6 lists `EuropePmcSource` as "not yet built."**
   It was built on 2026-07-20 (commit `0111ec9`), after that brief was written.
   The remaining gap is the methods→materials extraction pass and §9 stages 5–11,
   not the adapter.
2. **`docs/curation/README.md` implies only western_blot is filled.** All three
   launch categories are filled across `checklist.csv` and `elicitation.csv`.
   The open question is provenance (§3.3), not completeness.

## 6. Where 4 and 5 join

Stage 7 is the seam. A BoM = **ingest output + the curated delta** — one half from
the protocol lane, the other from the curation CSVs. Neither half reaches it
today: the ingest lane stops at ranked articles with no materials, and the
checklist half has no code path out of the spreadsheets.

That also means the validation plan in `docs/curation/README.md` — the
hand-authored delta doubling as the answer key that proves the ingest pipeline
works — cannot run in either direction yet.

## 7. Recommended next moves

1. **Measure MPN recovery before implementing Phase 1** (roadblock 3). Run the
   extraction prompt against a few hundred products and report the recovery rate.
   The entire `exact := brand + mpn` definition rests on data that does not exist
   yet; a low rate invalidates the design cheaply, now, instead of after a 16k-row
   run.
2. **Prove the product thesis on one paper** (roadblocks 4+5, §6). Take a single
   Europe PMC CC-BY western-blot paper, run `LLMMaterialExtractor`, and compose the
   result with the existing western_blot checklist rows into a BoM. About a day's
   work, and it tests the transform that IS the product rather than more
   infrastructure.
3. **Get the gold set labeled** (roadblock 1). It is the only artifact that turns
   domain judgement into a threshold number, and it has a human lead time —
   start it now, in parallel, not after the scoring change lands.
4. **Settle the curation CSVs' provenance and commit them** (§3.2, §3.3).

## 8. Open actions

- [ ] **Zhile:** confirm whether the rt_qpcr / elisa checklist rows are expert-validated
      or engineering drafts; commit the four CSVs to git once settled.
- [ ] **Zhile:** measure MPN recovery rate on a sample before writing the Phase 1 plan.
- [ ] **Zhile:** one-paper end-to-end spike (Europe PMC → materials → BoM with checklist).
- [ ] **Domain expert:** label `data/eval/gold_set_labeling.csv` (100 pairs, ~2–3 hrs).
      Do **not** send `gold_set_key.csv` — shown the machine's labels a labeler anchors
      on them and we measure agreement instead of truth.
- [ ] **Domain expert:** canonical protocol pick per launch category.
- [ ] **Zhile:** merge or close out `scoring-model-fix`.
- [ ] **Carried from `protocol-sourcing-handoff.md` §7:** protocols.io commercial-licence
      inquiry; IP counsel on facts-vs-contract before scaled ingestion.
