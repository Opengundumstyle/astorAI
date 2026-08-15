# Move 4 — "Promote → source → point" assistant — Design

**Date:** 2026-08-14
**Status:** Approved (design), pending implementation plan
**Scope:** Reshape the storefront chat from a catalog-retrieval search box into a bounded
lab advisor, and add the first **write** capability: capture unmet demand as a sourcing
request the team can review. Deliberately NOT a dashboard UI, notifications, auto-capture
analytics, product deep-linking (Move 3), or rate-limiting (shared hardening follow-up).

## Problem

The assistant "feels like a smarter Google": it retrieves what's ingested and, when asked
for anything off-catalog, deflects and ends the conversation. Two causes: (1) the grounding
prompt ("never invent a product/SKU/protocol; only mention tool results") plus Move 2-A's
"never infer from general knowledge" — correct for *catalog facts* — get over-applied to
*all* knowledge, so it won't advise; (2) every tool is a catalog lookup, so its whole
repertoire is retrieve-or-deflect. A miss is a dead end instead of a demand signal.

## Theme

Every request runs three tiers: **promote** what Astor carries → on a miss, **capture the
demand** for sourcing → secondarily, **point** the customer to where it's generally
available. A miss becomes a lead and a stocking-roadmap signal instead of a lost turn.

## Decisions (from brainstorming)

- **Confirm-first capture (A).** The bot offers to flag a miss and only logs on the
  customer's yes — keeps the queue high-signal and consensual.
- **Email optional (C).** Always log the request; *offer* email for follow-up ("want us to
  email you when we source it?") but never gate on it.
- **Review surface = table + read endpoint (A).** Durable capture + `GET /api/sourcing-requests`
  for the team; dashboard tile / notifications are later.
- **"Where to buy" may name major suppliers generically** (e.g. "the big suppliers like
  Sigma-Aldrich or Thermo Fisher usually carry this") — but NOT a fabricated competitor SKU
  or a purchase link, and always **after** promoting Astor + offering sourcing.
- **Identity is server-supplied.** `shop` and `customer_id` come from the verified App Proxy
  request, never from the model.

## Design

### 1. Persona reshape — `SYSTEM` in `src/astor/chat/agent.py`

Rewrite the role and grounding into two explicit lanes:

- **Catalog facts** (products, protocols, SKUs, availability): must come from tools; never
  invented. (Keeps the existing anti-hallucination rules, incl. `protocols_by_material`
  grounding.)
- **Scientific knowledge** (technique, buffers, troubleshooting, experimental design): used
  freely, like a knowledgeable lab colleague.

Behavioral flow, stated in the prompt:
1. Search the catalog first. If Astor carries it, **promote** it (lead with the product/
   protocol).
2. If it's a genuine miss, still **answer the science / give the advice**, then say we don't
   stock it and **offer to flag it for sourcing** ("want our team to look into sourcing it?").
3. On the customer's yes, call `flag_sourcing_request`; **offer optional email** for
   follow-up. Confirm before logging — never log unasked.
4. Only if the customer asks where to get it, name **major suppliers generically** (no
   fabricated SKUs, no links), as the closer — not the headline.

Keep the brevity rules, but soften the "ask one question and stop / end the conversation"
reflex so it stays a dialogue.

### 2. Data model — `sourcing_requests` (Alembic migration + `src/astor/db/models.py`)

Columns: `id` (uuid pk), `requested_item` (Text, the reagent/product asked for),
`context` (Text, the customer's need in their words), `shop` (String, nullable),
`customer_id` (String, nullable — the proxy `logged_in_customer_id`), `email` (String,
nullable), `status` (String, default `'new'`), `created_at` (timestamptz default now).
No unique constraint — each request is its own demand data point (dedupe/aggregate at
review time).

### 3. Repo — `src/astor/api/repo.py`

- `create_sourcing_request(session, *, requested_item, context, shop=None, customer_id=None, email=None) -> dict`
  — inserts a row, returns `{"id", "requested_item", "status"}`.
- `list_sourcing_requests(session, *, limit=50) -> list[dict]` — newest first, each
  `{"id", "requested_item", "context", "shop", "customer_id", "email", "status", "created_at"}`.

### 4. Write tool — `flag_sourcing_request` (`src/astor/chat/tools.py`)

The first write tool. Input schema `{item: str (required), context: str, email?: str}` —
the **model** supplies these. `shop` and `customer_id` come from a request-scoped context
the **server** threads in, NOT from the model.

- Threading: `run_chat` / `run_chat_stream` / `dispatch` gain an optional
  `request_context: dict | None` (`{"shop", "customer_id"}`). `dispatch` passes it to the
  handler. `/proxy/chat` fills it from `verify_app_proxy` (shop) + the proxy query's
  `logged_in_customer_id`; the demo `/api/chat` passes `None` (→ shop/customer_id null).
  Read tools ignore it.
- Handler `_flag_sourcing_request(session, args, request_context)`: calls
  `repo.create_sourcing_request(session, requested_item=args["item"], context=args.get("context",""),
  shop=(request_context or {}).get("shop"), customer_id=(request_context or {}).get("customer_id"),
  email=args.get("email"))`. Returns a short ack dict (e.g. `{"logged": True, "item": ...}`)
  and no `ReferencedItem`s. **Server identity always overrides** any `shop`/`customer_id`
  the model might put in `args` (those keys are ignored).
- Register in `_HANDLERS` and `TOOL_SCHEMAS`. Threading form (pinned): every handler gains a
  trailing `request_context: dict | None = None` parameter, and `dispatch(session, name,
  args, request_context=None)` passes it to the chosen handler. Read handlers keep the
  param and ignore it; only `_flag_sourcing_request` reads it. This is a uniform, low-churn
  signature change across the existing handlers.

### 5. Read endpoint — `GET /api/sourcing-requests` (`src/astor/api/routers/dashboard.py`)

`limit` query (default 50, cap 200) → `{"items": [...], "count": n}` via
`repo.list_sourcing_requests`. Admin/team read. **No public write endpoint** — capture
happens only through the agent, which on the storefront is gated by the App Proxy
signature; the write is model-mediated and confirm-first.

### 6. Data flow

Off-catalog ask → bot answers the science + notes we don't carry it + offers to flag →
customer confirms (optionally gives email) → agent calls `flag_sourcing_request(item,
context, email?)` → server attaches `shop`/`customer_id` from the verified proxy request →
row in `sourcing_requests` → team reviews via `GET /api/sourcing-requests`.

### 7. Error handling

- Tool insert failure → caught by `dispatch`'s existing try/except → returns `{"error": ...}`;
  the bot tells the customer it couldn't log it and offers to retry. Never crashes the turn.
- Missing `item` → the tool schema marks it required; a malformed call returns a tool error,
  not a row.
- `request_context` absent (demo path) → shop/customer_id stored null; still a valid record.

### 8. Guardrails

- Never fabricate a SKU/product/protocol; never assert catalog availability without a tool.
- General knowledge is for advice, not catalog claims.
- "Where to buy": major suppliers named generically only — no fabricated competitor SKUs,
  no purchase links, always secondary to Astor promotion + the sourcing offer.
- `shop`/`customer_id` are server-supplied only; the model cannot set caller identity.
- Confirm before logging; email is opt-in.

### 9. Testing (offline)

- **Model/migration**: table creates; columns + defaults (`status='new'`, `created_at`).
- **Repo** (DB-gated, `RUN_DB_TESTS=1`, unique-marker isolation like the Move 2-A tests):
  `create_sourcing_request` inserts and returns the id/status; `list_sourcing_requests`
  returns newest-first with all fields.
- **Tool** (offline, repo monkeypatched): `flag_sourcing_request` creates a request from
  `{item, context, email}`; **server `request_context` shop/customer_id override any model-
  supplied values**; missing email → null; returns the ack, no `ReferencedItem`s.
- **Agent loop** (fake client): a scripted turn calls `flag_sourcing_request` → the repo
  create is invoked with the threaded `request_context`; confirm-first is honored (the tool
  only fires after the model decides to).
- **Endpoint**: `GET /api/sourcing-requests` returns the list payload (repo monkeypatched,
  `get_session` overridden); `limit` capped.
- Persona prose isn't unit-tested (as with the existing prompt) — verified by the tool-
  wiring tests + the live dev-store check.

## Non-goals

- Dashboard tile / team notifications / auto-capture analytics.
- A public write endpoint; product deep-linking (Move 3); rate-limiting (shared hardening
  follow-up — the write is confirm-first + proxy-gated, not a trivial spam vector, but it is
  a public-reachable write and wants a limit before heavy public traffic).
- Streaming changes; any change to the read tools' behavior.
