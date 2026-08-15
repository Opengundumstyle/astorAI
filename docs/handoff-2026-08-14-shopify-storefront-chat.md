# Handoff — Shopify storefront chat + grounded reverse search (2026-08-14)

State of the work after this session. Three sub-projects shipped to `main`, each via
brainstorm → spec → plan → subagent-driven build with adversarial final review.

## What shipped

### Sub-project #1 — App Proxy trust boundary
The engine verifies Shopify App-Proxy-signed requests and exposes one proof endpoint.
- `src/astor/api/shopify_proxy.py`: `valid_app_proxy_signature` (hex HMAC-SHA256 over the
  rendered `key=value` query params — **sorted as strings**, no separator, keyed by the
  app secret) + `verify_app_proxy` dependency (401 bad/missing sig, 503 no secret,
  fail-closed).
- `GET /proxy/ping` → `{ok, shop}`, gated by that dependency.
- Config: `settings.shopify_app_proxy_secret` (falls back to `shopify_client_secret`).
- Spec: `docs/superpowers/specs/2026-08-12-shopify-app-proxy-design.md`;
  runbook: `docs/shopify-app-proxy-runbook.md`.

### Sub-project #2 — Storefront chat (widget through the proxy)
- `POST /proxy/chat` — reuses `agent.run_chat` unchanged (non-streaming), same
  `{reply, items}` shape as `/api/chat`, gated by `verify_app_proxy`.
- `GET /proxy/widget.js` — serves a self-contained vanilla-JS chat widget
  (`src/astor/api/static/widget.js`; shipped in builds via `pyproject` package-data).
- Widget: floating bubble, message list, typing indicator, example chips, reply + plain
  item chips (**capped at 6 + "+N more"**, long names ellipsized), ~30s abort, friendly
  error, IME-safe Enter, double-load guard. Derives its proxy base from its own script tag.
- Theme install = one loader snippet in `theme.liquid` (see runbook "Storefront chat").
- Spec: `docs/superpowers/specs/2026-08-13-storefront-chat-design.md`.

### Move 2-A — Grounded "protocols by material" reverse search
Fixes a real bug found in live testing: "Find protocols that use Trypsin-EDTA" used to
**guess** ("likely uses…"); now it answers from data.
- `repo.protocols_by_material` — lexical substring over the `protocols.materials` jsonb
  (normalize both sides: lowercase, collapse `-`/`/`/whitespace; `%`/`_` escaped), servable
  only, ranked by `rank_score`. Returns `{total, protocols:[{id,title,product_count,matched_material}]}`.
- `GET /api/protocols/by-material?q=&limit=` endpoint.
- `protocols_by_material` chat tool + a **groundedness prompt**: use the tool for
  "which protocols use X"; when empty, say so plainly, never infer from general knowledge.
- Verified live: the query now returns 9 grounded protocols instead of a hallucinated one.
- Spec: `docs/superpowers/specs/2026-08-13-protocols-by-material-design.md`.

### Move 4 — "Promote → source → point" advisor + demand capture
Fixes the "feels like a smarter Google" problem: the bot deflected on anything off-catalog.
- **Persona reshape** (`SYSTEM` in `chat/agent.py`): two lanes — catalog facts (products/
  protocols/SKUs) come from tools and are never invented; scientific knowledge is used freely
  to advise. Flow: promote Astor → on a miss, still answer the science + offer to flag for
  sourcing → only if asked, name major suppliers generically (no fabricated SKUs/links).
- **First WRITE capability**: `sourcing_requests` table + `flag_sourcing_request` chat tool.
  The model supplies `item`/`context`/`email`; the **server** supplies `shop`/`customer_id`
  from the verified App Proxy request — the model cannot set caller identity (enforced in the
  handler, tested adversarially). `request_context` is threaded from `verify_app_proxy` →
  `/proxy/chat` → `run_chat` → `dispatch` → the tool. Confirm-first; email opt-in.
- **`GET /api/sourcing-requests`** (admin read, newest-first, limit cap 200) for the team to
  review captured demand — the sourcing/stocking roadmap.
- Verified live: an off-catalog ask ("anti-GFP nanobodies") logs a request + bridges to
  related Astor products instead of dead-ending.
- Spec: `docs/superpowers/specs/2026-08-14-promote-source-point-design.md`.
- **DB note:** the `sourcing_requests` table is created via `Base.metadata.create_all` in the
  diverged local dev DB (migration `0006_sourcing_requests.py` is the clean-deploy artifact —
  do NOT `alembic upgrade head` locally, the phantom `0002_pack_size_text` breaks the chain).

## Test status
- Offline suite: **223 passed, 9 skipped** (`python -m pytest -q`).
- DB-gated repo tests (Postgres jsonb): pass under **`RUN_DB_TESTS=1`** (local Postgres).
  These are skipped in the normal run; all HTTP/tool/agent layers monkeypatch the repo, so
  the suite is green with no database.

## Live environment (local dev, not hosted)
- Engine runs locally: `uvicorn astor.api.main:app --port 8000` (reads `.env`).
  **Restart gotcha:** kill *all* listeners on 8000 (`lsof -ti tcp:8000 | xargs kill`) —
  stale uvicorn processes can hold the port so a restart silently fails to take over.
- Public exposure is a **free `cloudflared` quick tunnel** → **ephemeral URL** that changes
  on every restart. Each change requires updating the Shopify app's App Proxy **Proxy URL**
  and **releasing a new version**. This whack-a-mole is the next thing to kill (see below).
- Shopify: a **Partner dev app "Astor Assistant"** (App Proxy: prefix `apps`, subpath
  `astor`, Proxy URL → the tunnel `/proxy`) installed on a free **dev store `astor-dev`**.
  Secrets (app API secret, store password) live in `.env` / the dev store admin — **not**
  in git.
  - **Two Partner orgs exist**: an older one with a `astor-ingest` app (unrelated,
    pre-dates this work — leave alone), and the org created this session that holds
    **Astor Assistant** + the `astor-dev` store. Use the org that contains Astor Assistant.

## Open follow-ups (next candidates)
- **Stable tunnel URL** (in progress): move off ephemeral `trycloudflare` to a fixed URL
  (recommended: ngrok free **static domain**) so the Shopify Proxy URL is set once and never
  re-edited. Real end state is hosting the engine (sub-project #3), which removes the tunnel
  entirely.
- **Move 2-B — matching-quality overhaul**: only 3 of ~30 trypsin material mentions are
  SKU-linked; normalize noisy SKU/material text before embedding + add a lexical fallback +
  re-backfill the 827 `protocol_material_links`. Improves the buy-the-product side and the
  SKU→protocol index. Larger, changes the link corpus, needs a re-match + quality gate.
- **Move 3 — product deep-linking**: item chips are non-clickable because product rows carry
  no Shopify handle/URL (they come from supplier catalogs). Quick win: chips link to
  `astorscientific.us/search?q=<name>`. Real version: match our products → Shopify products
  (SKU/MPN/title via the Admin API), store the handle, link to `/products/<handle>`. Only
  demoable on the real store (dev store has fake products).
- **Public-endpoint hardening** (before public launch): `/proxy/chat` is reachable by anyone
  who can load the storefront and is unmetered (each call costs Anthropic tokens). Add a
  rate limit, a signed-`timestamp` freshness check, and a request-size cap. **Must also cover
  the unsigned demo `/api/chat`**, which — now that the chat can write — can create
  `sourcing_requests` rows (null identity) without a signature. Deliberately a non-goal so
  far; revisit before astorscientific.us go-live (and before `/api/chat` is publicly hosted).
- **Sourcing-request surfacing** (Move 4 follow-ups): a dashboard tile + team notifications
  on new requests (email/Slack) — deferred non-goals of Move 4; the read endpoint exists.
- **Sub-projects #3 (hosting the engine+DB) and #4 (commerce/webhooks)**: not started.

## Where things are
- Specs + plans: `docs/superpowers/specs/`, `docs/superpowers/plans/`.
- Runbook (dev-store setup + theme snippet): `docs/shopify-app-proxy-runbook.md`.
- Prior handoffs: `docs/protocol-sku-pipeline-handoff.md`, `docs/storefront-chat-handoff.md`.
