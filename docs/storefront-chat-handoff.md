# Storefront + Chat Assistant — Handoff

> **Status:** 2026-08-12. The protocol→SKU pipeline is now surfaced in the
> storefront (browse + bidirectional link pages) and driven by a conversational
> assistant with live streaming. Complements `docs/protocol-sku-pipeline-handoff.md`
> (the data pipeline) and `docs/ARCHITECTURE.md` §4/§7 (Plane 1 conversation,
> Plane 2 catalog grounding). All on `main`.

## What this arc delivered (on top of the pipeline)

The 827 material→SKU links are now usable through the engine API and the Next.js
storefront, and talkable via a tool-calling assistant:

1. **Read endpoints** (`src/astor/api/routers/`):
   - `GET /api/products/{id}/protocols` — protocols that use a product (reverse).
   - `GET /api/protocols/{id}/materials` — a protocol's product cart (forward).
   - `GET /api/protocols` — browse/search servable protocols, most catalog-connected first.
   - Data access in `repo.py` (`product_protocols`, `protocol_materials`, `list_protocols`);
     routers tested offline by monkeypatching `repo` / `agent`.
2. **Storefront pages** (`web/app/(shell)/`):
   - Product page gains a "Protocols that use this product" section.
   - `/protocols` browse list; `/protocols/[id]` a protocol's product cart.
   - The whole loop is navigable: product ↔ protocols ↔ product.
3. **Chat assistant** (`src/astor/chat/`, `web/components/ChatPanel.tsx`, `/chat`):
   - Tool-calling Claude loop: 5 read tools over `repo` (`search_products`,
     `search_protocols`, `protocol_products`, `product_protocols`, `product_detail`),
     grounded — cards come only from tool results, never invented.
   - `POST /api/chat` (non-stream) and `POST /api/chat/stream` (SSE). The frontend uses
     streaming: status line during tool lookups → reply streams word-by-word → cards.
   - Model: `settings.chat_model` (default `claude-sonnet-5`), `thinking={"type":"disabled"}`
     (REQUIRED — see gotcha below).

## How to run it (local; DB must be up)

```
docker compose up -d db                              # pgvector DB (see pipeline handoff)
uvicorn astor.api.main:app --port 8000               # engine API  (needs ANTHROPIC_API_KEY)
cd web && npm run dev                                 # storefront → http://localhost:3000
```
Entry points: `/chat` (assistant), `/protocols` (browse). The API restart is required to
pick up new routers; the Next dev server hot-reloads the frontend.

## Gotchas hit and fixed (don't reintroduce)

- **Sonnet-5 adaptive thinking breaks tool replay.** Omitting the `thinking` param makes
  claude-sonnet-5 emit thinking blocks; our block serializer strips them, and replaying a
  stripped/unsigned thinking block 400s the API → the tool loop dies (surfaces as 500).
  Fix: pass `thinking={"type":"disabled"}` on EVERY `messages.create`/`messages.stream`
  call in `chat/agent.py`. The offline tests can't catch this (fake client) — the final
  adversarial review did.
- **Info overload.** The system prompt was tightened to 2–4 sentences, one best-match
  recommendation, cards carry detail, offer the next step (`chat/agent.py:SYSTEM`).
- **SSE framing:** `data: {json}\n\n`; `json.dumps` escapes newlines so a delta can't
  contain a literal blank-line boundary. Missing key → 503 before the stream opens.

## Grounding / safety

- Referenced-item **cards are built only from tool results** (`agent` collects them from
  `tools.dispatch`), so the UI can never link to a product/protocol that doesn't exist.
  The prose is guarded only by the system prompt (an LLM can still name something loosely,
  but no fake card).
- The links surfaced are still `reviewed=false` — the storefront read endpoints accept a
  `reviewed_only` flag (default false for the demo). For production, set it true so only
  human-vetted links show (§9.11 review gate).

## Live state (this machine only)

- DB: 862 protocols, 16,016 products (embedded), 827 `protocol_material_links`
  (material_substitute_threshold=0.75; see pipeline handoff for the dial).
- Servers were left running during dev: API on :8000, web on :3000. Restart as above.
- anthropic SDK: venv has 0.117.x; the running anaconda env has 0.112 — both stream
  correctly (verified live). Voyage payment method added; Anthropic key set.

## Tests

Full Python suite green (186 passed, 1 skipped). Chat/stream covered offline via a fake
Anthropic client (`tests/test_chat_agent.py`, `tests/test_chat_stream.py`) + monkeypatched
repo/agent for routers (`tests/api/test_chat*.py`, `test_product_protocols.py`,
`test_protocol_materials.py`, `test_protocols_list.py`). Frontend: tsc + eslint only
(no JS harness). The real streaming loop runs only against live Anthropic — verified by
a live SSE curl.

## Next (deferred, non-blocking)

- **Review workflow:** flip `reviewed=true` on good links (Mary), then set `reviewed_only=true`.
- **Chat UX polish:** blinking cursor while streaming; max-iters fallback text; preserve
  error detail when partial content already streamed.
- **Completeness checklist (§9.7 — the moat)** and the **BoM/ProtocolTemplate layer (§8)**
  remain the highest-value net-new pipeline work.
- **Fresh-DB caveat:** the live DB state is local; a new DB re-does load → extract → match.
