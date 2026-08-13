# Storefront chat via App Proxy (sub-project #2) — Design

**Date:** 2026-08-13
**Status:** Approved (design), pending implementation plan
**Scope:** Put the Astor assistant on the Shopify storefront. Route the chat through the
App Proxy trust boundary built in sub-project #1, and embed a chat widget in the theme so
a customer on the dev store (`astor-dev.myshopify.com`, later `astorscientific.us`) can
talk to it. Builds directly on #1 (`/proxy` prefix, `verify_app_proxy` signature check),
which is proven end-to-end (`/proxy/ping` returns a signed `{ok, shop}`).

## Problem

The assistant only runs in a local demo harness (`localhost:3000/chat`). To reach real
customers it must live on the storefront — but the storefront browser can't call the
engine directly (exposes the URL, CORS, secrets). App Proxy solves that: a browser request
to `store/apps/astor/...` is signed by Shopify, forwarded to the engine, and verified. #1
proved the boundary with a ping; #2 carries the actual chat across it.

## Decisions (from brainstorming)

- **Manual theme snippet (option A), not a Theme App Extension.** Fastest path to a working
  storefront bot; no Shopify CLI, no extension build. A real launch path for the store.
- **Non-streaming to start.** App Proxy can buffer streaming responses and has a response
  timeout; the reliable path is a single request/response with a "typing…" indicator. The
  existing streaming endpoint stays but is not proxied yet.
- **Reply text + plain item chips.** Referenced products/protocols render as non-clickable
  name chips. No storefront product deep-linking (needs an ID→Shopify-handle map we don't
  have; the dev store's catalog is fake test data anyway).
- **Loader snippet + engine-served widget.** The theme gets a tiny loader; the engine
  serves the widget JS through the proxy, so iterating on the widget never re-touches the
  theme.

## Key facts (verified)

- Engine chat logic already exists: `astor.chat.agent.run_chat(session, messages)` returns
  `ChatReply(reply: str, items: list[ReferencedItem])`; today's `POST /api/chat` wraps it
  and returns `{"reply", "items":[{type,id,name}]}`. #2 reuses `run_chat` unchanged.
- App Proxy signs **every** request under the subpath, including POST (body forwarded,
  signature appended as query params). `verify_app_proxy(request)` reads
  `request.query_params` and so verifies GET and POST identically. Confirmed working in #1.
- Requests from the widget are **same-origin** (`astor-dev.myshopify.com/apps/astor/...`),
  so no CORS handling is needed. The engine URL and secret never reach the browser.
- The proxy router (`src/astor/api/routers/shopify_proxy.py`) currently holds only `/ping`.

## Design

### 1. Engine — two proxy-gated routes (in `src/astor/api/routers/shopify_proxy.py`)

Both depend on `verify_app_proxy` (same 401/503 behavior as `/ping`).

- **`POST /proxy/chat`** — request body `{"messages": [{"role", "content"}]}` (reuse a
  Pydantic model identical to the chat router's `ChatRequest`). Calls
  `agent.run_chat(session, [...])` with `session` from the existing `get_session`
  dependency, and returns `{"reply": reply.reply, "items": [{"type","id","name"} ...]}` —
  byte-for-byte the same shape as `POST /api/chat`. On `RuntimeError` (e.g. missing
  `ANTHROPIC_API_KEY`) → `HTTPException(503, ...)`, matching `/api/chat`.

  Dependency order: `verify_app_proxy` first (reject unsigned before doing any work), then
  `get_session`. Both are FastAPI `Depends`.

- **`GET /proxy/widget.js`** — returns the widget source as
  `Response(content=<js>, media_type="application/javascript")`. The JS lives in a repo
  file `src/astor/api/static/widget.js`, read at request time (small; simplicity over
  caching). Gated by `verify_app_proxy` like everything under `/proxy`. A short
  `Cache-Control: public, max-age=60` header is fine but not required.

### 2. Theme — loader snippet (documented, pasted by the operator)

One block, pasted once into the theme (`theme.liquid`, just before `</body>`):

```html
<div id="astor-chat"></div>
<script src="/apps/astor/widget.js" defer></script>
```

`/apps/astor/widget.js` resolves against the storefront origin → Shopify proxies + signs →
engine verifies + serves. Everything else is driven by the served JS; **updating the widget
never requires editing the theme again.**

### 3. The widget — `src/astor/api/static/widget.js` (vanilla JS, no framework, no build)

Self-contained (~120–180 lines). Responsibilities:

- **Derive its own proxy base** from its `<script>` tag so it isn't hardcoded to
  `/apps/astor`: find `document.querySelector('script[src*="widget.js"]')`, take its `src`,
  strip the trailing `/widget.js` → base (e.g. `/apps/astor`). POST target = `base + "/chat"`.
  (Uses the script tag, not `document.currentScript`, which is null under `defer`.)
- **UI**: a floating bubble button that toggles a panel containing a scrollable message
  list, a text input + send button, a "typing…" indicator while awaiting a reply, and a few
  example-prompt chips on first open (mirroring `ChatPanel`'s examples). All styles inlined
  in the JS (injected `<style>` or inline styles) so there is no separate CSS asset; scoped
  under an `#astor-chat` namespace to avoid clashing with theme CSS.
- **State**: keeps the running `messages` array in memory; each turn POSTs the full history
  (same contract as the demo). No persistence.
- **Network**: `fetch(base + "/chat", {method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({messages})})`, with an `AbortController` timeout (~30s) to stay under
  the App Proxy limit. On the JSON response, append the assistant `reply` and render `items`
  as plain (non-clickable) chips showing `name` (and a small type tag).
- **Errors**: any non-200, network failure, or timeout → append a friendly assistant line
  ("I'm having trouble reaching the assistant right now — please try again."). Never hang,
  never surface raw errors or status codes to the shopper.

### 4. Data flow

`customer opens store` → theme loads `/apps/astor/widget.js` (Shopify-signed → verified →
served) → customer types → widget `POST /apps/astor/chat` (Shopify-signed → verified) →
`run_chat` queries the DB + Anthropic → `{reply, items}` → widget renders reply + chips.
Same-origin throughout; no CORS; secret/engine-URL never in the browser.

### 5. Error handling

- Bad/missing signature → `401` (from `verify_app_proxy`); no secret configured → `503`.
  The widget treats any non-200 as the friendly retry message.
- Missing `ANTHROPIC_API_KEY` → `503` from `/proxy/chat` (same as `/api/chat`).
- A turn exceeding the App Proxy timeout → the client `AbortController` fires first and the
  widget shows the retry message rather than hanging.

### 6. Testing (offline, no Shopify)

`tests/api/test_shopify_proxy_chat.py`, reusing the `_sign` helper pattern from #1:

- `POST /proxy/chat` with a valid signature and a **monkeypatched `agent.run_chat`**
  (returns a canned `ChatReply`) → `200` with `{"reply", "items"}` in the expected shape.
- `POST /proxy/chat` with a tampered/missing signature → `401`; with no secret → `503`.
- `POST /proxy/chat` when `run_chat` raises `RuntimeError` → `503`.
- `GET /proxy/widget.js` with a valid signature → `200`, `Content-Type` starts with
  `application/javascript`, body contains a known marker (e.g. `astor-chat`); bad signature
  → `401`.

The widget JS is verified by inspection plus the live dev-store round-trip (type a message
on `astor-dev.myshopify.com`, get a reply) — the same acceptance bar we used for the ping.

### 7. Runbook — extend `docs/shopify-app-proxy-runbook.md`

Add a "Storefront chat" section: paste the loader snippet into `theme.liquid`, ensure the
engine + tunnel are running (unchanged from #1), open the storefront, and confirm the chat
bubble appears and answers. Note that the tunnel URL baked into the released App Proxy
config must still be current.

## Non-goals

- Streaming through the proxy (the existing SSE endpoint stays unproxied for now).
- Product deep-linking / ID→Shopify-handle mapping (a later sub-project, tied to the real
  `astorscientific.us` catalog).
- Theme App Extension packaging (option B) — a later productionization of the embed.
- Rate-limiting / abuse controls and `logged_in_customer_id` personalization.
- Any change to the existing `/api/chat`, `/api/chat/stream`, or the demo harness.
