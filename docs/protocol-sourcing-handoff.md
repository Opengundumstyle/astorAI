# Protocol Sourcing & Licensing — Handoff

> **Status:** decision brief, 2026-07-18. Captures the source evaluation, the
> licensing analysis, Mary's "separate database" proposal, and the recommended v1
> path. Complements `ARCHITECTURE.md` §9–10 (ingest pipeline + data/curation) and
> the code scaffold in `src/astor/protocols/`.

## 1. The question we were answering

For the v1 protocol source (ARCHITECTURE.md §4 override: protocols.io single-source,
review-ranked, accuracy layer deferred), **can we legally ingest protocol content
into our own index and serve/derive from it in a commercial product?** The answer
turns out to differ sharply by source.

## 2. The core distinction: copyright licence vs. site contract

Two separate legal instruments bind us independently:

- **Copyright licence** (e.g. CC-BY, granted by the *author*): governs what we may do
  with the *work* — copy, redistribute, make derivatives, use commercially — *with
  attribution*. Facts (steps, materials, quantities) are **not copyrightable** at all,
  so they can be extracted regardless.
- **Site Terms of Service** (a *contract* with the platform): governs our *access to
  and use of the site/API*. **A contract can forbid what a copyright licence permits.**

The trap: content can be permissively licensed (CC-BY) yet still be **contractually
off-limits to ingest**, because the ToS restricts the *act of pulling it from the site*.
The "facts are free" argument defeats **copyright** claims but **not** breach-of-contract.

## 3. Source-by-source verdict

| Source | Commercial ingest-and-serve | Structure/API | Coverage & quality | Best-fit role |
|---|---|---|---|---|
| **Europe PMC (OA CC0/CC-BY subset)** | ✅ permissive **and** permitted | REST + bulk FTP | Very broad (papers) | **The free ingest lane** |
| **protocols.io** | ❌ needs paid commercial licence (ToS wall) | Best — structured materials API | Broad, protocol-shaped | Fast path *if* licensed |
| **Bio-protocol** | ⚠️ only the CC-BY slice; CC-BY-NC blocked by design | No API | High quality, protocol-shaped | CC-BY subset **via Europe PMC** |
| **Addgene protocols** | ❌ all-rights-reserved, noncommercial; needs written consent | No protocols API | Narrow (dozens) | Reference / citations / link-out |

**protocols.io specifics (the blocker):** ToS §4.A explicitly prohibits our use case —
4.A(vi) "copy, download, or store any content/**data** from the Site … to make or
populate a **database** of any kind"; 4.A(vii) storing in a "private electronic
retrieval system"; 4.A(xi) "systematic or automated downloading … to index content";
4.A(i) commercial use "may … be subject to separate terms … and a fee." The content is
CC-BY, but the **contract** forbids the ingestion. → commercial data licence or link-out.

**Europe PMC (the clean lane):** the OA subset (~5.7M articles) is CC0/CC-BY/CC-BY-NC/
CC-BY-NC-SA and is **explicitly provided for text mining** via FTP/API — no restrictive
contract. Filter to **CC0/CC-BY** for commercial serving. It **already contains the
CC-BY Bio-protocol articles**, so that source is subsumed here.

## 4. Mary's proposal — does it work?

**Proposal (paraphrased from thread):** call protocols.io's database, generate a
*product-association table* stored as a *separate* database, so the protocol itself
doesn't contain our products → "not for-profit" → circumvents the commercial-use problem.

**Verdict: it does not circumvent the ToS.** Reasons:

1. **The prohibited act is the ingestion itself.** 4.A(vi) forbids pulling their data
   "to make or populate a database of any kind whatsoever" — Mary's plan is *literally*
   that. 4.A(xi) forbids automated downloading to index content. The breach happens at
   the *pull*, before any table is built.
2. **"Not for-profit" is inaccurate.** The table's purpose is to sell products — that is
   commercial use (4.A.i). Whether our products sit *inside* the protocol text is
   irrelevant; the commercial character is the purpose of the derived system, and that's
   what a court/counsel weighs, not the table layout.
3. **"Separate/derivative database" doesn't cure it.** 4.B(vi) forbids creating
   derivative works from their content except as permitted; a mapping keyed to their
   protocols is a derivative built on their data.
4. **What the instinct gets right:** storing only *facts→SKU mappings* (not their prose)
   is correct data hygiene — it solves a **copyright** concern. But the blocker was never
   copyright; it's the **contract** (how we access/use their data). It fixes the wrong axis.

**On "以个人名义 / personal capacity":** using an individual or academic account to pull
data we then use commercially is a **clearer** ToS violation (commercial use via
non-commercial access, possible misrepresentation) and reads as deliberate circumvention
— worse on both legal and relationship risk. Advise against.

**The kernel that IS right, done legitimately:** store facts-only, mapped to SKUs — but
obtain the facts from a source not bound by protocols.io's contract (Europe PMC CC-BY),
or under a protocols.io commercial licence. Same result, no breach.

**The team is already on the right path** (from the thread): emailing protocols.io to ask
about the **commercial use fee** and "talk to their team" (Brian) is exactly the correct
move — that's the licence lane, not circumvention.

## 5. Recommended v1 path

1. **Don't mass-ingest anything yet, and don't rely on any circumvention theory.**
2. **Hand-pick canonical protocols per category** (Mary's domain call — which protocol is
   the standard for each of the first 3–5 categories). Prefer **CC-BY** sources we can
   actually serve; use Addgene/Bio-protocol/protocols.io as *reference shortlists*.
3. **Extract facts with the LLM extractor** (reuse the `catalog/extraction.py` pattern),
   restructure into our schema, attribute, **never reproduce source prose.**
4. **Free ingest lane = Europe PMC OA subset filtered to CC0/CC-BY** (includes CC-BY
   Bio-protocol). This is where the "facts are free" argument actually holds.
5. **In parallel (non-blocking): pursue the protocols.io commercial licence** — email +
   "commercial use fee" inquiry, cc Mary (maryxueyu@gmail.com). Its value is the
   *structured* data (skips prose extraction); price is negotiated, not published.
6. **IP counsel** (§14 #9) to confirm the copyright-vs-contract reading before scale.

## 6. Code scaffold status (`src/astor/protocols/`)

Built, tested (8 tests green), mirrors `catalog/`:
- `schemas.py` — neutral `RawProtocol` + `ReviewSignal` (review-ranking input) +
  `License` enum with `.redistributable` gate (fails closed on UNKNOWN).
- `sources.py` — `ProtocolsIoSource`: pure offline `to_raw()` mapping; **network fetch
  is gated** (`allow_network=False` by default) so nothing can bulk-pull until the ToS is
  cleared.
- `filtering.py` — `license_gate` (legal split) + `rank_by_review` (selection policy).
- `ingestion.py` — `map → gate → rank`, offline-drivable via `run_from_payloads()`.

**Not yet built:** `EuropePmcSource` adapter (+ `CC_BY_NC_SA` licence value); a curated
"seed list" runner for the hand-pick path; the §9 transform stages (role classify,
procurement filter, spec, completeness). These come once the loop is proven on real data.

## 7. Open actions

- [ ] **Zhile:** email protocols.io re: commercial data licence + commercial-use fee; cc Mary.
- [ ] **Mary:** pick the standard protocol per first category (source-agnostic; feeds the checklist).
- [ ] **Zhile:** wire `EuropePmcSource` + seed-list runner for the CC-BY hand-pick path.
- [ ] **Counsel:** confirm facts-vs-contract analysis before any scaled ingestion.
