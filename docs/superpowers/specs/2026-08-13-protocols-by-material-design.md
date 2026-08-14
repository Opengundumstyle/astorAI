# Protocols-by-material reverse search (Move 2-A) — Design

**Date:** 2026-08-13
**Status:** Approved (design), pending implementation plan
**Scope:** Give the assistant a grounded way to answer "which protocols use material X."
Add a lexical reverse search over each protocol's extracted materials, exposed the same
three ways every read in this app is (repo → endpoint → agent tool), plus a prompt change
so the agent uses it and stops guessing. Deliberately NOT the material→SKU matching
overhaul (that is Move 2-B).

## Problem

Testing the storefront chat with "Find protocols that use Trypsin-EDTA" produced a wrong,
ungrounded answer: the bot named a protocol whose materials don't include trypsin and said
it "likely uses Trypsin-EDTA" — a guess. Two causes:

1. **No search path.** `search_protocols` → `repo.list_protocols` matches protocol *titles*
   only (`Protocol.title.ilike(%q%)`). Nothing searches the materials list, so a
   material-name query cannot find the protocols that actually list it.
2. **Guessing to fill the gap.** With no grounded path, the agent infers material usage
   from general knowledge, violating the "never invent" rule.

The data exists: 30 protocols list a trypsin variant in `protocols.materials`. We just
can't query it.

## Key facts (verified)

- `protocols.materials` is `jsonb`: an array of `{"name", "amount", "vendor", "catalog_no"}`
  objects. Example names: `"0.25% Trypsin-EDTA(1x) in HBSS, with Phenol Red, pH 7.2-8.0 -
  100 ML"`, `"Trypsin-EDTA solution (Gibco, #25200056)"`, `"Sequencing grade recombinant
  trypsin"`.
- `Protocol` has `servable` (bool), `rank_score` (review ranking), `title`. `list_protocols`
  already filters `servable.is_(True)` and computes `product_count` as a correlated
  count of `ProtocolMaterialLink` rows.
- Reads follow repo → router → agent-tool. Agent tools live in `chat/tools.py`
  (`TOOL_SCHEMAS`, `dispatch`, `ReferencedItem`); the `SYSTEM` prompt is in `chat/agent.py`.
- Tests: offline, `TestClient` with `app.dependency_overrides[get_session]`, or repo tests
  against a seeded session.

## Decisions (from brainstorming)

- **Lexical substring match (A1)**, not semantic. High precision for canonical reagent
  names; needs no new infrastructure; finds all 30 today. Synonym/semantic matching is a
  Move 2-B concern.
- **Order by review rank (B1)**: `rank_score` desc, `product_count` desc as tie-breaker —
  mirrors `list_protocols` so the reverse search feels like the same product.
- Expose via **repo + endpoint + agent tool**, and fix the **groundedness prompt**.
- Payload carries **`matched_material`** — the actual material string that matched — so the
  bot can show *why* a protocol is a hit.

## Design

### 1. Repo — `protocols_by_material(session, material, *, limit=10)` (`src/astor/api/repo.py`)

Case-insensitive lexical search over the `materials` jsonb. A protocol matches when **any**
element's `name` contains the normalized term.

- **Normalization (both sides):** lowercase, and collapse hyphens/slashes/whitespace runs to
  a single space, so `"trypsin-edta"`, `"trypsin edta"`, `"Trypsin/EDTA"` are equivalent.
  Applied to the query term and, in SQL, to each material name before the `LIKE`.
- **Query shape:** filter `Protocol.servable.is_(True)`; require existence of a material
  whose normalized `name` contains the normalized term (a jsonb-array-elements lateral /
  `EXISTS` over `jsonb_array_elements(materials)`, with `regexp_replace(lower(elem->>'name'),
  '[-/[:space:]]+',' ','g')` `LIKE '%<normterm>%'`). Order `rank_score` desc nulls last,
  then `product_count` desc. `limit` caps returned rows.
- **product_count:** the same correlated `ProtocolMaterialLink` count `list_protocols` uses.
- **matched_material:** for each returned protocol, the first material `name` (original,
  un-normalized) that matched the term.
- **Returns:** `{"total": <int>, "protocols": [{"id": str, "title": str,
  "product_count": int, "matched_material": str}, ...]}`. `total` is the full count of
  matching servable protocols (not just the `limit` page), so the caller can say "30 use it".
- **Empty/blank term** (after normalization) → `{"total": 0, "protocols": []}`; never raises.

### 2. Endpoint — `GET /api/protocols/by-material` (`src/astor/api/routers/protocols.py`)

Query params `q` (required, the material term) and `limit` (optional, default 10, capped at
50). Thin wrapper: `return repo.protocols_by_material(session, q, limit=limit)`. Missing/empty
`q` → returns the empty payload (200), consistent with the repo contract. Same JSON shape as
the repo function.

### 3. Agent tool — `protocols_by_material` (`src/astor/chat/tools.py`)

- Handler `_protocols_by_material(session, args)`: calls
  `repo.protocols_by_material(session, args["material"], limit=int(args.get("limit") or 10))`.
  Returns the payload dict and `ReferencedItem("protocol", p["id"], p["title"])` for each
  returned protocol (rendered as chips).
- Register in `dispatch` and `TOOL_SCHEMAS`:
  `{"name": "protocols_by_material", "description": "Find protocols that USE a given lab
  material/reagent by name (reverse lookup over each protocol's material list). Use this for
  'which protocols use X' / 'what protocols need X' questions.", input: {material: string
  (required), limit: integer (optional)}}`.

### 4. Prompt — `SYSTEM` in `chat/agent.py`

Add to GROUNDING (keep all existing brevity rules unchanged):

- For a "which/what protocols use|need|require material/reagent X" question, call
  `protocols_by_material` with the reagent's core name.
- If it returns `total: 0` (or an empty list), say plainly that no protocols in the catalog
  list that material — do NOT infer or guess which protocols use it from general knowledge.
- When results exist, lead with the count ("30 protocols use Trypsin-EDTA") and name the
  single best match; the chips carry the rest. Optionally offer to find the product to buy.

### 5. Data flow

"Find protocols that use Trypsin-EDTA" → agent calls `protocols_by_material("Trypsin-EDTA")`
→ repo scans the `materials` jsonb → returns the real 30 ranked by review → agent replies
with the count + top match, chips = those protocols. Grounded, honest, correct.

### 6. Error handling

- Empty/blank/malformed term → `{"total": 0, "protocols": []}`; the agent surfaces the
  honest "no protocols list that material," never a guess.
- The endpoint never 500s on a missing `q` — it returns the empty payload.

### 7. Testing (offline, no network)

- **Repo** (`tests/.../test_protocols_by_material.py`, seeded session): seed protocols with
  `materials` jsonb — some containing a trypsin variant, some not, one non-servable, one
  whose match uses a hyphen/space variant; assert only the servable trypsin ones return, in
  `rank_score` order, `matched_material` populated with the real string, `total` correct, and
  the loose hyphen/space/slash normalization matches. Blank term → empty.
- **Endpoint**: `TestClient` with `get_session` overridden to the seeded session (or repo
  monkeypatched) — `GET /api/protocols/by-material?q=trypsin` returns the payload; missing
  `q` → empty payload, 200.
- **Tool**: `dispatch(session, "protocols_by_material", {...})` (repo monkeypatched) returns
  the payload and one protocol `ReferencedItem` per result.
- **Agent wiring**: with a fake Anthropic client that calls the tool then answers, assert the
  loop dispatches `protocols_by_material` and the reply carries the protocol items (mirrors
  existing `tests/api/test_chat.py` style).

## Non-goals

- Synonym / semantic material matching (Move 2-B).
- Any change to material→SKU linking, the 827 `protocol_material_links`, or the matcher.
- Product deep-linking or a browse-page UI (the endpoint suffices for now).
- Changes to `search_protocols` (title search stays; the new tool is additive).
