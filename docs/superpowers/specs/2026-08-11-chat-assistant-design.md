# Astor chat assistant (tool-calling MVP) — Design

**Date:** 2026-08-11
**Status:** Approved (design), pending implementation plan
**Scope:** A conversational assistant on the storefront for UX testing — a Claude
tool-use loop over the existing read endpoints, plus a chat UI. The pragmatic MVP
of Plane 1 (conversation) + Plane 2 (catalog grounding); NOT the full elicitation
state machine.

## Problem

The pipeline is built and browsable (protocols ↔ products, 827 links), but there's
no way to *talk* to it like a customer would. The founder wants to feel the
conversational UX — describe an experiment or a need in free text and have the
assistant find the relevant protocol and the Astor products it needs. The engine
already exposes every read the assistant needs; what's missing is a brain that
understands intent, calls those reads, and replies conversationally.

## Decisions (from brainstorming)

- **Tool-calling LLM**, not a scripted flow or the full Plane-1 state machine.
- **Bot decides** protocol-driven vs product-driven per turn.
- **Recommend + link** products/protocols (clickable to existing pages); **no cart
  state** in v1.
- Build offline-tested; the user runs it against the live DB + Anthropic.

## Key facts (verified)

- `anthropic` 0.112.0 installed; `settings.anthropic_api_key` present (already used
  by `protocols/extraction.py`). Tool use via `client.messages.create(tools=[...])`.
- Existing repo reads to expose as tools (`src/astor/api/repo.py`): `list_products`
  (search by `q`), `list_protocols` (search by `q`), `protocol_materials`,
  `product_protocols`, `get_product_detail`. All in-process — no HTTP hop.
- FastAPI app factory `astor.api.main:create_app`; routers under
  `src/astor/api/routers/` (`APIRouter(prefix="/api")`), `get_session` dep,
  dict responses; routers tested offline by monkeypatching `repo` (see
  `tests/api/test_catalog.py`).
- Next.js 16 storefront; server-component pages, client components use `"use client"`;
  `lib/api.ts` typed client; sidebar `NAV` array; `ConfidenceBar`/`KindBadge` reusable.

## Design

### 1. Tools — `src/astor/chat/tools.py`

Five tools, each a thin wrapper over a repo function returning **compact** JSON
(only what the model needs — ids, names, a few fields — to keep token cost low):

| Tool | Backs | Returns |
|---|---|---|
| `search_products(query, limit=8)` | `repo.list_products(q=…)` | `[{id, name, brand, category}]` |
| `search_protocols(query, limit=8)` | `repo.list_protocols(q=…)` | `[{id, title, product_count}]` |
| `protocol_products(protocol_id)` | `repo.protocol_materials` | `{protocol_title, products:[{product_id, product_name, material_name, confidence}]}` |
| `product_protocols(product_id)` | `repo.product_protocols` | `{product_name, protocols:[{protocol_id, title}]}` |
| `product_detail(product_id)` | `repo.get_product_detail` | `{id, name, brand, category, specs}` |

- `TOOL_SCHEMAS`: the Anthropic tool JSON-schema list.
- `dispatch(session, name, args) -> (result_json, referenced_items)` — runs the tool
  and also returns the products/protocols it surfaced as typed `ReferencedItem`s
  (`{type:'product'|'protocol', id, name}`) so the loop can collect them for the UI.
- A tool that raises returns `{"error": "..."}` as its result (recoverable), never
  propagates.

### 2. Agent loop — `src/astor/chat/agent.py`

```python
@dataclass
class ReferencedItem: type: str; id: str; name: str
@dataclass
class ChatReply: reply: str; items: list[ReferencedItem]

SYSTEM = "...Astor scientific procurement assistant... ground every product/protocol
in a tool result, never invent a SKU or protocol; ask ONE clarifying question when
the request is vague; be concise..."

def run_chat(session, messages, *, client=None, model=None, max_iters=6) -> ChatReply:
    # messages: [{"role":"user"|"assistant","content":str}, ...] (conversation history)
    # 1. require key -> else RuntimeError (router maps to 503)
    # 2. client = client or Anthropic(api_key=settings.anthropic_api_key)
    # 3. loop up to max_iters:
    #      resp = client.messages.create(model, system=SYSTEM, tools=TOOL_SCHEMAS,
    #                                     max_tokens=1024, messages=convo)
    #      if stop_reason != "tool_use": return ChatReply(text(resp), collected)
    #      for each tool_use block: result, items = tools.dispatch(session, name, input)
    #         append tool_use (assistant) + tool_result (user) to convo; collect items
    #    # cap hit: return best-effort text + collected
```

- `model` defaults to `settings.chat_model` (new config field, default
  `"claude-sonnet-5"`).
- `client` is the **injectable seam**: tests pass a fake exposing
  `.messages.create(...)` returning canned `tool_use` then final text.
- Referenced items deduped by `(type, id)`, order preserved.

### 3. Endpoint — `src/astor/api/routers/chat.py`

```python
@router.post("/chat")
def chat(body: ChatRequest, session = Depends(get_session)) -> dict:
    try:
        r = agent.run_chat(session, body.messages)
    except RuntimeError as e:              # missing key
        raise HTTPException(503, str(e))
    return {"reply": r.reply,
            "items": [{"type": i.type, "id": i.id, "name": i.name} for i in r.items]}
```
`ChatRequest` = pydantic `{messages: list[ChatMessage]}`, `ChatMessage = {role, content}`.
Registered in `main.py`.

### 4. Frontend

- `lib/types.ts`: `ChatMessage {role:'user'|'assistant', content:string}`,
  `ChatItem {type:'product'|'protocol', id, name}`, `ChatResponse {reply, items}`.
- `lib/api.ts`: `sendChat(messages) -> ChatResponse` (POST /api/chat).
- `components/ChatPanel.tsx` (`"use client"`): holds `messages` state, an input +
  send, a "thinking…" indicator while awaiting; renders each turn as a bubble;
  under an assistant reply, renders `items` as small cards linking to
  `/products/{id}` or `/protocols/{id}` (reusing the card style). Enter-to-send.
- `app/(shell)/chat/page.tsx`: a light shell that renders `<ChatPanel/>` with an
  intro + example prompts ("I need to run a Western blot for phospho-ERK",
  "What do I need for a BCA assay?").
- `components/Sidebar.tsx`: add `{ href:"/chat", label:"Assistant", icon:"◆" }`.

### 5. Data flow

user types → `sendChat(history)` → `POST /api/chat` → `run_chat` loop (Claude +
tools, tools hit repo in-process) → `{reply, items}` → ChatPanel appends the
assistant bubble + item cards. Client owns the history and posts it each turn (no
server persistence).

### 6. Error handling

- No Anthropic key → `run_chat` raises → `503` with a clear message; ChatPanel shows
  it as an assistant-styled error bubble.
- Tool raises → returned to the model as `{"error":…}` tool_result; the model
  recovers or explains. Never 500s the turn.
- `max_iters` cap (6) bounds cost/latency; on cap, return the model's latest text
  plus whatever items were collected.

### 7. Testing (all offline)

- **tools.py:** each tool with `repo` monkeypatched — asserts compact shape and that
  `dispatch` returns the right `ReferencedItem`s; a raising repo → `{"error":…}`.
- **agent.py:** fake client returning (a) a `tool_use` for `search_protocols` then a
  final text — assert the tool ran, `tool_result` threaded, reply + deduped items
  returned; (b) immediate text (no tools); (c) `max_iters` cap; (d) missing key →
  RuntimeError.
- **chat.py router:** `TestClient` + monkeypatched `agent.run_chat` — happy path
  shape, and `RuntimeError` → 503 (mirrors `tests/api/test_catalog.py`).
- **Frontend:** `tsc --noEmit` + `eslint` clean (no JS test harness in the repo).

## Non-goals (v1)

- No cart state (recommend + link only).
- No streaming (fast-follow; v1 is a single response per turn with a loading state).
- No auth / rate-limit on `/api/chat` (local UX test).
- No server-side conversation persistence.
- Not the Plane-1 completeness-gate elicitation state machine (this MVP exercises the
  UX; the architected version is later).
