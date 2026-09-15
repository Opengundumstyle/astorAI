# Troubleshooting know-how: design spec

**Date:** 2026-09-14
**Status:** Approved design, awaiting implementation plan
**Branch:** bench/assistant-benchmark
**Companion memo:** `docs/biomni-comparison.md` (why we are doing this and what we are not copying from Biomni)

## 1. Goal

Give the storefront assistant a curated, expert-reviewed source for wet-lab troubleshooting answers, so that when a customer says "my Western blot has no bands" the reply is grounded in Astor's own guidance, names what to check, and ends in what to buy.

Success looks like:

- Every troubleshooting fix the assistant suggests traces to a row in a curation table with a review state and a source.
- Every row's fix resolves to a purchasable role, or explicitly to "the lab already owns this".
- A gold set measures whether the right rows and the right products surface, and runs in the existing bench.

## 2. Non-goals

- No code execution, data lake, or literature agent. See the companion memo.
- No new database table or migration in this version. CSV loaded at startup.
- No changes to Shopify, the cart, or the engine/adapter boundary.
- No per-shop overrides. One table for all shops.
- No attempt to cover categories Mary has not run. Those wait for the coverage-expansion pass.

## 3. Decisions already made

| Question | Decision |
|---|---|
| Content source | Model drafts, Mary reviews and corrects. |
| Entry shape | Structured rows joined to `roles.csv` and `checklist.csv`. |
| First-version scope | `western_blot`, `rt_qpcr`, `elisa`, `cell_culture_transfection` (the four with `has_run_it=yes`). |
| Consumption | New chat tool `troubleshoot(category, symptom)`. |
| Storage | `docs/curation/troubleshooting.csv`, loaded at startup with the other curation tables. |
| Eval | A gold set ships with the feature. |

## 4. The table

### 4.1 File

`docs/curation/troubleshooting.csv`, UTF-8 with BOM to match the other four tables, so it opens cleanly in Excel for Mary.

### 4.2 Columns

| Column | Type | Meaning |
|---|---|---|
| `entry_id` | string, `T` + 4 digits | Stable key. Never reused after deletion. |
| `category_id` | string | Must exist in `categories.csv`. |
| `symptom` | text, bilingual | What the customer observes. Written the way a customer would say it, Chinese and English in one cell separated by ` / `, same convention as `category_name`. |
| `likely_cause` | text, bilingual | The single most likely cause for this row. One cause per row; a symptom with three causes is three rows. |
| `check_or_fix` | text, bilingual | What to check or change. Concrete: "confirm secondary host differs from primary", not "check your antibodies". |
| `fix_role` | string or empty | A `role` from `roles.csv` that the fix requires. Empty when the fix is procedural and needs no item. |
| `buy_needed` | `yes` / `no` / `sometimes` | Whether the fix normally means buying something. Derived guidance for the assistant, not a hard rule. |
| `checklist_role_ref` | string or empty | `category_id:role` of the checklist row this cause corresponds to, when the cause is a missing or wrong required item. |
| `confidence` | `drafted` / `reviewed` | `drafted` until Mary has confirmed the row. |
| `source` | `checklist` / `mary` / `model` | Where the row's content came from. `checklist` means derived mechanically from a checklist row's `why_required`. |
| `reviewed_by` | string or empty | Reviewer name. |
| `reviewed_on` | ISO date or empty | Review date. |
| `notes` | text | Free. |

### 4.3 Validation rules, enforced by the loader at startup

- `entry_id` unique.
- `category_id` present in `categories.csv`.
- `fix_role`, when set, present in `roles.csv`.
- `checklist_role_ref`, when set, matches a `category_id,role` pair in `checklist.csv`.
- `confidence` and `source` limited to the listed values.
- `reviewed_by` and `reviewed_on` both set when `confidence=reviewed`, both empty otherwise.

A validation failure raises at startup with the offending `entry_id` and column. The assistant must not start with a malformed table.

### 4.4 Expected size

10 to 15 rows per category, about 50 rows in version one.

## 5. Drafting and review workflow

1. **Mechanical seed.** For each checklist row in scope whose `why_required` describes a failure ("no signal", "masks signal", "false positive"), emit one troubleshooting row with `source=checklist`, `checklist_role_ref` set, `fix_role` equal to the checklist role, `confidence=drafted`.
2. **Model draft.** For each category, Claude drafts the remaining rows from general knowledge to reach the target count, `source=model`, `confidence=drafted`. Every drafted row must name a `fix_role` that exists or leave it empty with a procedural fix.
3. **Review.** Mary edits the CSV or an xlsx export in `docs/curation/`, corrects text, deletes wrong rows, adds rows with `source=mary`, and sets `confidence=reviewed` with her name and date.
4. **Load.** The loader accepts both states. Drafted rows are served but tagged, and the assistant is told to present them as "commonly reported" rather than "Astor's guidance". This is the review-state analogue of Biomni's commercial-mode filter.

The mechanical seed and the model draft are produced by a script in `scripts/` that writes the CSV. It runs once to seed and is not part of the runtime.

## 6. Curation loader

New package `src/astor/curation/`.

### 6.1 `loader.py`

- `load_categories(path) -> dict[str, Category]`
- `load_roles(path) -> dict[str, Role]`
- `load_checklist(path) -> list[ChecklistRow]`
- `load_troubleshooting(path, *, categories, roles, checklist) -> list[TroubleshootingEntry]`
- `load_all(root: Path) -> CurationTables` which loads the four files above and runs the cross-table validation from section 4.3.

Frozen dataclasses. No pandas. Row parsing strips the BOM.

`categories.csv` already has a loader in `src/astor/protocols/categories.py` for the harvest. The new loader does not replace it in this version. A follow-up may unify them.

### 6.2 Where it is called

`load_all` runs once at API startup and the result is stored on the app state, the same place the embedder and session factory live. Tool handlers receive it through `request_context`.

## 7. The tool

### 7.1 Schema, added to `TOOL_SCHEMAS` in `src/astor/chat/tools.py`

```
name: troubleshoot
description: Look up Astor's curated troubleshooting guidance for a failed or
  unexpected experimental result. Call this for "my X didn't work", "no signal",
  "high background", "no amplification", "cells died" style questions. Returns
  matching symptom/cause/fix entries and, for each fix, the catalog products that
  fill the needed role. Present drafted entries as commonly reported causes, not
  as Astor's confirmed guidance.
input_schema:
  category: string, one of the category_ids in scope (optional; omit if unsure)
  symptom: string, the customer's description in their own words (required)
  limit: integer, default 5
```

### 7.2 Behaviour, in `src/astor/curation/troubleshoot.py`

1. **Category.** If `category` is given and valid, filter to it. If omitted, run a keyword classifier over `category_name` and the category's symptom vocabulary; if no category scores, search all four.
2. **Symptom match.** Score each candidate row by token overlap between the customer's `symptom` and the row's `symptom` plus `likely_cause`, on both the Chinese and English halves. If the best score is below a threshold, fall back to embedding similarity using the existing embedder, and tag the result `match: "semantic"` the way `search_products` does. Row embeddings over `symptom + likely_cause` are computed once at load time and held in memory with the tables; at about 50 rows this is one batched call at startup. If the embedder is unavailable at startup, the tables still load and the tool runs keyword-only. The keyword threshold is an implementation constant, set so the fixture tests pass and tuned against the gold set.
3. **Resolve fixes.** For each returned row with a `fix_role`, look up the role's `plain_description` and run the existing lexical product search on it, limit 3. If `lab_usually_owns_it=yes` for the role, skip the product search and say so in the result.
4. **Return.**

```
{
  "entries": [
    {
      "entry_id": "T0007",
      "category_id": "western_blot",
      "symptom": "...",
      "likely_cause": "...",
      "check_or_fix": "...",
      "confidence": "reviewed" | "drafted",
      "fix_role": "secondary_antibody" | null,
      "lab_usually_owns_it": "no",
      "products": [ gated product DTOs, up to 3 ]
    }
  ],
  "match": "keyword" | "semantic"
}
```

Products go through `roles.gate_product(r, roles.BUYER)` like every other tool. `ReferencedItem`s are emitted for the products so the UI renders cards.

No LLM call inside the tool. Deterministic given the tables and the catalog.

### 7.3 Prompt change, `src/astor/chat/agent.py`

One paragraph under GROUNDING SPECIFICS:

> For a failed or unexpected result ("no bands", "no Ct", "high background", "cells detached"), call `troubleshoot` with the customer's own words before answering. Lead with the most likely cause, say what to check, and end with what to buy if a fix needs an item Astor carries. If an entry is marked `drafted`, say it is a commonly reported cause rather than Astor's confirmed guidance. If the tool returns nothing, answer from general knowledge and say the catalog has no specific guidance for that case.

The cached system prompt changes once. No per-turn injection.

## 8. Eval

### 8.1 Gold set

`data/eval/troubleshooting_gold.csv`:

| Column | Meaning |
|---|---|
| `case_id` | `TG` + 4 digits |
| `category_id` | Expected category |
| `question` | Customer-phrased failure, mixed Chinese and English across the set |
| `expected_entry_ids` | `;`-separated entry ids that a correct lookup must include |
| `expected_fix_roles` | `;`-separated roles a correct answer must surface |
| `must_match` | Regex against surfaced product chip names, same as `assistant_scenarios.csv` |
| `notes` | Free |

10 to 15 cases per category. At least a quarter of cases phrased in Chinese only.

### 8.2 Runner, `src/astor/eval/troubleshooting.py`

Two layers, both reusing existing machinery:

- **Retrieval layer.** Call `troubleshoot` directly with each case's `question`. Score hit rate: fraction of `expected_entry_ids` present in the top `limit`. Report per category and overall. No model call, runs in the unit test suite against fixtures and, with a database, against the real tables.
- **Assistant layer.** Wrap each case as a `Scenario` from `eval/assistant.py` and run the existing gate on whether the surfaced product chips match `must_match`. This is the same gate the current bench uses and needs a live model.

Gate bars follow `GateBars` defaults. A retrieval hit rate below 0.8 overall, or below 0.6 in any category, fails the bench.

## 9. Files

New:

- `docs/curation/troubleshooting.csv`
- `data/eval/troubleshooting_gold.csv`
- `scripts/seed_troubleshooting.py`
- `src/astor/curation/__init__.py`
- `src/astor/curation/loader.py`
- `src/astor/curation/troubleshoot.py`
- `src/astor/eval/troubleshooting.py`
- `tests/test_curation_loader.py`
- `tests/test_troubleshoot_tool.py`
- `tests/test_troubleshooting_eval.py`

Modified:

- `src/astor/chat/tools.py`: handler and schema for `troubleshoot`.
- `src/astor/chat/agent.py`: one prompt paragraph.
- API startup: call `load_all` and attach the tables to app state; pass through `request_context`.
- `docs/curation/01-每张表怎么填.md`: a section describing the fifth table, in the same style as the other four.

## 10. Testing

- **Loader:** loads the real four CSVs plus the new one without error; each validation rule in 4.3 has a failing fixture that raises with the right `entry_id`.
- **Tool:** fixture tables with six rows across two categories. Tests for: category given, category omitted and classified, category omitted and unclassifiable, keyword hit, semantic fallback with a fake embedder, `fix_role` resolved to products, `lab_usually_owns_it=yes` skips product search, empty result shape.
- **Prompt:** existing `test_chat_agent.py` fake-client pattern, one scenario where the model calls `troubleshoot` and the reply references a product chip.
- **Eval:** retrieval layer runs on fixtures in CI; assistant layer marked live and runs in the bench.
- **Startup:** API boots with the tables; a deliberately broken CSV in a temp dir fails startup with a clear message.

## 11. Risks and how they are handled

- **Chinese symptom matching.** Token overlap is weak on Chinese without segmentation. Mitigation: bilingual cells give an English half to match on, the embedding fallback covers the rest, and the gold set includes Chinese-only cases so this is measured.
- **Drafted rows reaching customers.** Mitigation: the tool tags them, the prompt hedges them, and the eval reports the share of drafted rows surfaced so we can see when review is lagging.
- **Role vocabulary gaps.** A drafted fix may need a role not yet in `roles.csv`. Mitigation: the seed script reports every missing role, and the loader refuses to start on an unknown one, so gaps are fixed in the table rather than papered over.
- **Product search on role descriptions is approximate.** "二抗" as a search query may miss. Mitigation: version one accepts this and measures it through `must_match`; a follow-up can add a `search_hint` column to `roles.csv`.

## 12. Out of scope, noted for later

- Unifying the two `categories.csv` loaders.
- A `search_hint` column on roles.
- Moving the table to Postgres with pgvector once rows or shops multiply.
- The remaining five categories in `categories.csv`.
- Feeding checklist and elicitation tables to the assistant through the same loader. This spec builds the loader; wiring those two tables into tools is a separate piece of work.
