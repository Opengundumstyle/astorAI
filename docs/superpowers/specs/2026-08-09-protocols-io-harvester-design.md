# protocols.io harvester (build now, run when licensed) — Design

**Date:** 2026-08-09
**Status:** Approved (design), pending implementation plan
**Scope:** Build and offline-test the full discover → shortlist → fetch → persist →
gate → rank harvester for protocols.io. **Zero live calls until the commercial
licence lands** — the deliverable is a run-ready pipeline, not a run.

## Problem

The v1 protocol corpus plan (`docs/protocol-sourcing-handoff.md`) identified
protocols.io as the best-structured source but contractually blocked for bulk
ingestion (ToS §4.A vi/vii/xi: no systematic download, no database population)
pending a commercial data licence. The licence inquiry is an open action. Today
only `fetch_one` exists (single-probe, double-gated); there is no discovery,
shortlisting, bulk persistence, or orchestration. If/when a licence lands, day
one should be a run day, not a coding day.

"Top review" needs defining: protocols.io exposes **no star ratings and no
licence field** via its API. The available quality signals are `peer_reviewed`
(v3 list endpoint only — always null in the v4 detail payload) and `stats`
engagement counters (views, votes, bookmarks, forks, comments). Our existing
`ReviewSignal`/`rank_by_review` already model exactly these, with peer_reviewed
as the dominant key.

## Goal

A harvester that, given seed categories, produces a ranked, persisted corpus of
the top ~100 protocols per category — fully exercised offline against fixtures
today, and executable live later by flipping two explicit locks.

**Scale (v1 defaults, all config-overridable):**
- Categories: driven by `docs/curation/categories.csv` (western_blot, rt_qpcr,
  elisa, cell_culture_transfection), each with a small set of query synonyms
  (e.g. western_blot → "western blot", "immunoblot").
- Candidate pool: up to ~10 list pages × 50 per query (~500 candidates/category
  before dedupe).
- Shortlist: **top 100 per category** by `rank_by_review` on list-level signals.
- Detail fetches: shortlist only → ≈400 v4 fetches + ~40–80 list pages ≈
  **450–500 requests per full run**. This number goes in the licence email.

## Non-goals (deferred)

- Any live call against protocols.io (until licence + locks).
- The §9 transforms (role classify, procurement filter, spec, completeness).
- Europe PMC changes (`EuropePmcSource.search` + `run_from_search` already work).
- LLM extraction of materials from `materials_text` free text.

## Key facts (verified in code)

- `ProtocolsIoSource.to_raw(payload, list_item=...)` already threads the v3 list
  item through to recover `peer_reviewed` (`sources.py:149`).
- The API has **no licence concept**; every record maps to `License.UNKNOWN` and
  the gate fails it closed to link-out (`sources.py:221`). A harvester run with
  no licence agreement therefore yields a **link-out-only** corpus by design.
- `run_from_search` currently uses `hasattr(source, "search")` as its safety
  invariant — protocols.io is unsweepable *because* it lacks the method
  (`ingestion.py:85`). Adding `search()` invalidates that; the invariant must
  become explicit.
- Endpoint split is deliberate: list/search is v3, detail is v4
  (`sources.py:139`).

## Design

### 1. `ProtocolsIoSource.search()` (sources.py)

- `GET {LIST_BASE}/protocols?filter=public&key=<query>&order_field=relevance`
  with pagination (`page_size` ≤ 50, page cursor/number per v3 docs), hard
  `limit` cap, optional `peer_reviewed=1` filter parameter.
- Returns **raw list items** (dicts), not `RawProtocol` — list items feed both
  shortlist scoring and the later `to_raw(payload, list_item=...)` call.
- Gated exactly like `fetch_one`: `allow_network=False` default + token
  required. Additionally checks the licensed-run lock (§4).
- New explicit class attribute `sweepable: bool` on both sources
  (`EuropePmcSource.sweepable = True`, `ProtocolsIoSource.sweepable = False`).
  `run_from_search` checks `sweepable` instead of `hasattr(search)`.

### 2. `harvest.py` orchestrator (new module, `src/astor/protocols/`)

Stages, each a pure function where possible, mirroring `ingestion.py` style:

1. **discover(categories) → candidates**: run each category's query synonyms
   through `search()`, concatenate pages, dedupe by protocol `id` (keep latest
   `version_id`), dedupe across synonyms within a category; a protocol matching
   multiple categories is kept in each (category tag carried alongside).
2. **shortlist(candidates, n=100) → ids**: build `ReviewSignal` from list-item
   stats + `peer_reviewed`, rank with existing `rank_by_review` logic, take
   top-N per category.
3. **fetch(ids) → payloads**: v4 detail fetch per shortlisted id, throttled
   (reuse the free-tier throttle pattern from `rebuild_map`), with
   **skip-if-already-persisted at same version** so re-runs and resumed runs
   only fetch what is missing (interruption-safe, idempotent).
4. **persist**: every raw list page and detail payload written to
   `data/raw/protocols_io/` as JSON — detail payloads keyed
   `<protocol_id>-v<version_id>.json`, list pages under `searches/<category>/`.
   A `manifest.json` per run records: timestamp, queries, pages fetched,
   shortlist, serving_basis (§3). Re-ranking and mapping changes never
   re-fetch.
5. **map + gate + rank**: existing `run_from_payloads` path
   (`to_raw(payload, list_item)` → `license_gate` → `rank_by_review`).

An **offline entry point** drives stages 1–2 and 5 from saved fixture/persisted
JSON with no network, and is what tests exercise end-to-end.

### 3. Serving basis — explicit, auditable licence override

Since the API carries no licence data, servability under a commercial agreement
derives from the **contract**, not per-record labels:

- Harvester takes `serving_basis: str | None`. Default `None` → **link-out
  mode**: everything remains `UNKNOWN`, gate routes all records to `link_out`.
  Still useful (Mary's reference shortlists, demo, ranking experiments).
- With `serving_basis="commercial-licence:<agreement-ref>"` (from env/config
  once an agreement exists): each record is stamped with that provenance
  (persisted in manifest + on the record), and the gate is invoked with an
  explicitly widened `allow` set for this source. `DEFAULT_SERVE_LICENSES` and
  the fail-closed default gate are **never modified**.

### 4. Live-run lock — double-locked, loud

Network access for `search()` and bulk `fetch` requires **both**:
1. `allow_network=True` at the call site (existing pattern), and
2. `PROTOCOLS_IO_LICENSED=1` in env/config.

Missing either raises with a message citing `docs/protocol-sourcing-handoff.md`
§3 (ToS analysis). `fetch_one` keeps its current single-probe behaviour
(allow_network only) so the sanctioned mapping-probe workflow is unchanged.

### 5. Error handling

- HTTP 429/5xx: exponential backoff with cap; abort the run (persisting
  progress) after repeated failures rather than hammering.
- Malformed list items / payloads: log + skip, never abort the whole run;
  counts reported in the manifest.
- Partial runs resume via skip-if-persisted (stage 3).

### 6. Testing (all offline; fixtures in `tests/fixtures/protocols_io/`)

Fixtures: ≥2 search pages + a terminal page (repeat-cursor/empty), ≥2 detail
payloads (one from the sanctioned single-probe fetch, one hand-built covering
edge shapes: forks-as-object, Draft.js materials_text, missing DOI).

Tests: pagination termination; synonym + cross-page dedupe keeping latest
version; shortlist top-N ordering (peer_reviewed dominates engagement);
skip-if-persisted; link-out-mode gate (all → link_out); licensed-mode
provenance stamping + widened gate; both locks failing closed; `sweepable`
invariant on `run_from_search`.

## Open questions / follow-ups (non-blocking)

- The licence email should quote the concrete footprint: ~500 requests/run,
  ~400 stored records, quarterly-ish refresh — and ask whether a bulk
  snapshot/export is offered as an alternative to API sweeping.
- Exact v3 pagination parameter names (`page_id` vs `page` vs cursor) to be
  pinned against docs during implementation; fixtures encode the assumption so
  a wrong guess fails a test, not a run.
