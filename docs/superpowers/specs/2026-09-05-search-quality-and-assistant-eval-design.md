# Search quality + assistant pass-rate eval — Design

> **Status:** implemented 2026-09-05. Scope: sub-project A (search quality) and
> the test system that gates it. Sub-projects B (real SKU + product URL), C
> (chip → link) and D (protocol carts) are deferred to later rounds against this
> harness.

## Measured result

| Retrieval rule | Assistant pass rate |
|---|---|
| Before (whole-phrase `ILIKE`) | 5/6 EMEM, 5/6 six-well — gate FAILS |
| After (this design) | 42/42 across 7 scenarios — gate PASSES |

Reproduce the failing baseline with `--legacy-search`, which keeps the shipped
rule available precisely so the gate can be watched failing.

```
python -m scripts.run_assistant_eval --runs 6                  # fixture, gates CI
python -m scripts.run_assistant_eval --runs 6 --legacy-search  # watch it fail
python -m scripts.run_assistant_eval --runs 3 --db             # real 16k catalog
python -m scripts.run_assistant_eval --runs 3 --live           # deployed assistant
RUN_DB_TESTS=1 pytest                                          # 314 passed
```

Latency on the full 16,019-product catalog: 100–360 ms per search (five `COUNT`
scans for df plus the superset fetch). Acceptable inside a chat turn; an
in-process df cache is the obvious optimisation if it ever matters.

## The defects this closes

Verified against production on 2026-09-04/05 via a signed App Proxy request, and
against the live Shopify catalog (16,030 variants) pulled the same day.

| Symptom (from the review doc) | Mechanism |
|---|---|
| "产品搜索没有结果" — EMEM/DMEM reported as not carried | `repo.list_products` matched the query as ONE literal substring (`name ILIKE '%q%'`). `EMEM medium`, `MEM medium` and `Eagle's Minimum Essential Medium` (ASCII `'` vs the catalog's `’`) all return 0 rows. Measured miss rate: **4 of 8 runs** of the same question. |
| Assistant claimed no 6-well plates, showed 96-well | `%6 well cell culture plate%` is a literal substring of `NEST 9\|6 Well Cell Culture Plate`. The real 6-well plates (11 of them) never matched, because their word order differs. |
| Arbitrary results | `ORDER BY created_at DESC LIMIT 8` — no relevance ranking. 595 matches for `MEM` yielded 8 arbitrary rows. |

`repo.list_protocols` has the identical defect on `Protocol.title` and is included.

## Design

### 1. `src/astor/catalog/search.py` — pure, no ORM, no DB

All matching and ranking semantics live here, for the same reason `scoring.py`
is pure: the harness must measure the logic that actually runs, not a
reimplementation that can silently drift.

Rules, in order:

1. **Fold** — NFKC normalize, map apostrophe variants (`’ ‘ ˇ ` ´`) to `'`,
   casefold. This is what makes an ASCII `Eagle's` match the catalog's `Eagle’s`.
2. **Tokenize** on runs of non-alphanumerics (apostrophes kept inside words).
3. **Digit-bearing tokens are mandatory.** A row must word-prefix match every
   token containing a digit. In labware the number IS the discriminating
   attribute (6 vs 96 well, 500 mL vs 1 L). Word-prefix (`\b6`) does not match
   inside `96`, which is precisely why the 6/96-well collision cannot recur.
4. **Remaining tokens score by IDF-weighted coverage**, with a floor of 0.5.
   Rare terms (`dmem`) must outweigh common ones (`medium`), or unrelated
   `LB Medium` outranks `DMEM, Low Glucose`.
5. **Rank** — weighted coverage, plus a bonus for whole-word (not merely
   prefix) matches in the name, plus a bonus when the full folded query appears
   in the name, minus a small length penalty so the specific name outranks the
   verbose one.

Document frequency for step 4 is supplied by the caller. `repo` computes it with
one cheap `COUNT` per token using plain `ILIKE`, which **over-estimates** df
(substring, not word-prefix). That is deliberate and safe: IDF is a smooth
weight, and it keeps every semantic rule out of SQL.

### 2. `repo.list_products` / `repo.list_protocols`

Both become: SQL prefilter (plain `ILIKE`, superset, no semantics) → rank in
Python via `search` → paginate. Signatures, return shapes and the buyer gate are
unchanged, so `tools.py`, the routers and the ops UI need no edits.

A blank or punctuation-only query keeps the current behaviour (no filter).

### 3. Test system

Two layers, because neither alone would have caught the reported bug.

**Layer 1 — deterministic, offline, in the default suite.**
`data/eval/catalog_sample.csv` is a frozen fixture cut from the live catalog,
including every adversarial family (6-well vs 96-well, curly-apostrophe EMEM,
the DMEM family, ELISA 96-well kits, Trypsin-EDTA). `tests/test_search.py` is
table-driven over it: query → expected top hit, and **must-not-contain**
assertions. No API keys, no database, milliseconds.

**Layer 2 — LLM-in-the-loop pass rate, opt-in.**
No unit test could have caught the EMEM failure: it exists only when a live
model chooses the phrasing. `scripts/run_assistant_eval.py --runs N` drives the
real `agent.run_chat` against a fake session backed by the fixture and the real
`search` module — only the model varies — and reports a per-scenario pass rate
against a bar, in the style of `eval/gate.py`. It prints the query strings the
model emitted, which is the diagnostic that identified the root cause.
Scenarios live in `data/eval/assistant_scenarios.csv`, seeded from the review
doc's own complaints. Metrics are pure (`src/astor/eval/assistant.py`) and unit
tested without an LLM.

`--live` runs the same scenarios through the signed Shopify App Proxy against
production, to confirm a deploy actually fixed the behaviour.

## Out of scope (deliberate)

- Semantic/vector fallback. Production runs `EMBEDDINGS_PROVIDER=dev` (render.yaml
  sets no `VOYAGE_API_KEY`), so a vector fallback would return non-semantic
  garbage until that is configured. Lexical fixes every failure measured.
- `pg_trgm`/GIN indexing. At 16k rows a sequential scan is cheap; revisit when
  the catalog or QPS grows.
- Sub-projects B, C, D.

## Limitations

- df is approximate (substring over-estimate). Acceptable for a smooth weight;
  exact df would need either word-prefix regex in SQL or a cached index.
- The fixture is a snapshot. It tests the search rules, not catalog freshness.
- Layer 2's bar is a pass *rate*: a scenario can regress from 8/8 to 7/8 without
  failing unless the bar is set at 1.0. Must-find scenarios are set at 1.0.
- The fixture contains real product names and vendor values. They are already
  public on the storefront; the repo is private.
