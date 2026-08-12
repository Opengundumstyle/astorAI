# Chat Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A conversational storefront assistant — a Claude tool-use loop over the existing read endpoints plus a chat UI — so a customer can describe a need and get grounded protocol/product recommendations.

**Architecture:** `POST /api/chat` runs `chat.agent.run_chat`, a bounded Claude tool-use loop. Tools (`chat.tools`) wrap existing `repo` reads in-process and return compact JSON plus the products/protocols they surfaced; the loop collects those as referenced items. The Next.js `ChatPanel` posts the conversation each turn and renders the reply plus clickable item cards. Non-streaming v1.

**Tech Stack:** Python 3.11, FastAPI, `anthropic` 0.112, pydantic; Next.js 16 (App Router, TS). No new dependencies.

## Global Constraints

- **No new dependencies** (anthropic already installed; frontend uses existing patterns).
- **Tools return COMPACT JSON** — only ids/names/few fields, to bound tokens.
- **Grounding:** the assistant may only name a product/protocol that came from a tool; the system prompt enforces this. UI cards come from tool data, never the model's free text.
- **Injectable Anthropic client** (`run_chat(..., client=None)`) so the loop is offline-testable with a fake; the real client is built only when `client is None`.
- **No Anthropic key → `RuntimeError`** in `run_chat`, mapped to HTTP `503` by the router.
- **Bounded loop:** `max_iters=6`; on cap, return latest text + collected items.
- **Model:** `settings.chat_model`, default `"claude-sonnet-5"`.
- Routers use `APIRouter(prefix="/api")`, `get_session` dep, dict responses; tested offline by monkeypatching (`tests/api/test_catalog.py` pattern).
- Frontend: server-component page + `"use client"` panel; verify with `tsc --noEmit` + `eslint` (no JS test harness in repo).

---

### Task 1: `chat.tools` — tools over repo + dispatch

**Files:**
- Modify: `src/astor/config.py` (add `chat_model`)
- Create: `src/astor/chat/__init__.py` (empty)
- Create: `src/astor/chat/tools.py`
- Test: `tests/test_chat_tools.py`

**Interfaces:**
- Produces:
  - `ReferencedItem` dataclass: `type: str` (`"product"`|`"protocol"`), `id: str`, `name: str`.
  - `TOOL_SCHEMAS: list[dict]` — Anthropic tool definitions for the 5 tools.
  - `dispatch(session, name: str, args: dict) -> tuple[dict, list[ReferencedItem]]` — runs the named tool; a tool that raises returns `({"error": <msg>}, [])`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_tools.py
import types
import pytest
from astor.chat import tools
from astor.api import repo

def _sess(): return object()  # session unused; repo is monkeypatched

def test_search_products_is_compact_and_refs(monkeypatch):
    monkeypatch.setattr(repo, "list_products",
        lambda s, q, category, page, page_size: (
            [{"id": "p1", "name": "BCA Kit", "brand": "Astor", "category": "antibodies",
              "astor_sku": "ASR-1", "mpn": None, "region": None, "offer_count": 0, "best_landed": None}], 1))
    result, items = tools.dispatch(_sess(), "search_products", {"query": "BCA"})
    assert result["products"] == [{"id": "p1", "name": "BCA Kit", "brand": "Astor", "category": "antibodies"}]
    assert items == [tools.ReferencedItem("product", "p1", "BCA Kit")]

def test_search_protocols_refs(monkeypatch):
    monkeypatch.setattr(repo, "list_protocols",
        lambda s, q, page, page_size: ([{"id": "x1", "title": "Western Blot", "source": "protocols.io",
                                         "rank_score": 8.1, "product_count": 5}], 1))
    result, items = tools.dispatch(_sess(), "search_protocols", {"query": "western"})
    assert result["protocols"] == [{"id": "x1", "title": "Western Blot", "product_count": 5}]
    assert items == [tools.ReferencedItem("protocol", "x1", "Western Blot")]

def test_protocol_products_refs_protocol_and_products(monkeypatch):
    monkeypatch.setattr(repo, "protocol_materials",
        lambda s, pid, *, reviewed_only, limit: {
            "protocol_title": "WB", "source_uri": "u",
            "materials": [{"material_name": "BCA", "product_id": "p9", "product_name": "BCA Kit",
                           "brand": "Astor", "confidence": 0.86, "kind": "exact"}]} if pid == "x1" else None)
    result, items = tools.dispatch(_sess(), "protocol_products", {"protocol_id": "x1"})
    assert result["protocol_title"] == "WB"
    assert result["products"][0] == {"product_id": "p9", "product_name": "BCA Kit",
                                     "material_name": "BCA", "confidence": 0.86}
    assert tools.ReferencedItem("protocol", "x1", "WB") in items
    assert tools.ReferencedItem("product", "p9", "BCA Kit") in items

def test_unknown_id_returns_error_not_raise(monkeypatch):
    monkeypatch.setattr(repo, "protocol_materials", lambda s, pid, *, reviewed_only, limit: None)
    result, items = tools.dispatch(_sess(), "protocol_products", {"protocol_id": "nope"})
    assert "error" in result and items == []

def test_tool_exception_is_caught(monkeypatch):
    def boom(*a, **k): raise ValueError("db down")
    monkeypatch.setattr(repo, "list_products", boom)
    result, items = tools.dispatch(_sess(), "search_products", {"query": "x"})
    assert "error" in result and items == []

def test_schemas_cover_all_five_tools():
    names = {t["name"] for t in tools.TOOL_SCHEMAS}
    assert names == {"search_products", "search_protocols", "protocol_products",
                     "product_protocols", "product_detail"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_chat_tools.py -q`
Expected: FAIL (`ModuleNotFoundError: astor.chat`)

- [ ] **Step 3: Implement**

Add to `src/astor/config.py` after `anthropic_api_key`:

```python
    chat_model: str = "claude-sonnet-5"  # storefront assistant (tool-use loop)
```

Create `src/astor/chat/__init__.py` (empty). Create `src/astor/chat/tools.py`:

```python
"""Tools the storefront assistant can call — thin, compact wrappers over repo reads.

Each returns (result_dict, referenced_items). `referenced_items` are the products
and protocols the tool surfaced, so the chat UI can render real clickable cards
instead of trusting the model's prose. A tool that raises returns an {"error": ...}
result so the model can recover rather than 500 the turn.
"""
from __future__ import annotations

from dataclasses import dataclass

from astor.api import repo


@dataclass(frozen=True)
class ReferencedItem:
    type: str   # "product" | "protocol"
    id: str
    name: str


def _search_products(session, args) -> tuple[dict, list[ReferencedItem]]:
    limit = int(args.get("limit") or 8)
    rows, _ = repo.list_products(session, args["query"], None, 1, limit)
    products = [{"id": r["id"], "name": r["name"], "brand": r.get("brand"),
                 "category": r.get("category")} for r in rows]
    items = [ReferencedItem("product", r["id"], r["name"]) for r in rows]
    return {"products": products}, items


def _search_protocols(session, args) -> tuple[dict, list[ReferencedItem]]:
    limit = int(args.get("limit") or 8)
    rows, _ = repo.list_protocols(session, args["query"], 1, limit)
    protocols = [{"id": r["id"], "title": r["title"], "product_count": r["product_count"]}
                 for r in rows]
    items = [ReferencedItem("protocol", r["id"], r["title"]) for r in rows]
    return {"protocols": protocols}, items


def _protocol_products(session, args) -> tuple[dict, list[ReferencedItem]]:
    r = repo.protocol_materials(session, args["protocol_id"], reviewed_only=False, limit=50)
    if r is None:
        return {"error": "protocol not found"}, []
    products = [{"product_id": m["product_id"], "product_name": m["product_name"],
                 "material_name": m["material_name"], "confidence": m["confidence"]}
                for m in r["materials"]]
    items = [ReferencedItem("protocol", args["protocol_id"], r["protocol_title"])]
    items += [ReferencedItem("product", m["product_id"], m["product_name"]) for m in r["materials"]]
    return {"protocol_title": r["protocol_title"], "products": products}, items


def _product_protocols(session, args) -> tuple[dict, list[ReferencedItem]]:
    r = repo.product_protocols(session, args["product_id"], reviewed_only=False, limit=50)
    if r is None:
        return {"error": "product not found"}, []
    protocols = [{"protocol_id": p["protocol_id"], "title": p["title"]} for p in r["protocols"]]
    items = [ReferencedItem("product", args["product_id"], r["product_name"])]
    items += [ReferencedItem("protocol", p["protocol_id"], p["title"]) for p in r["protocols"]]
    return {"product_name": r["product_name"], "protocols": protocols}, items


def _product_detail(session, args) -> tuple[dict, list[ReferencedItem]]:
    d = repo.get_product_detail(session, args["product_id"])
    if d is None:
        return {"error": "product not found"}, []
    compact = {"id": d["id"], "name": d["name"], "brand": d.get("brand"),
               "category": d.get("category"), "specs": d.get("specs", {})}
    return compact, [ReferencedItem("product", d["id"], d["name"])]


_HANDLERS = {
    "search_products": _search_products,
    "search_protocols": _search_protocols,
    "protocol_products": _protocol_products,
    "product_protocols": _product_protocols,
    "product_detail": _product_detail,
}


def dispatch(session, name: str, args: dict) -> tuple[dict, list[ReferencedItem]]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}, []
    try:
        return handler(session, args)
    except Exception as exc:  # noqa: BLE001 — surface as recoverable tool error
        return {"error": f"{type(exc).__name__}: {exc}"}, []


TOOL_SCHEMAS = [
    {"name": "search_products",
     "description": "Search the Astor catalog by free text (name/brand). Returns matching products.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "search_protocols",
     "description": "Search harvested lab protocols by title. Returns protocols and how many catalog products each maps to.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "protocol_products",
     "description": "Given a protocol id, list the Astor products it needs (its shopping list).",
     "input_schema": {"type": "object",
                      "properties": {"protocol_id": {"type": "string"}},
                      "required": ["protocol_id"]}},
    {"name": "product_protocols",
     "description": "Given a product id, list the protocols that use it.",
     "input_schema": {"type": "object",
                      "properties": {"product_id": {"type": "string"}},
                      "required": ["product_id"]}},
    {"name": "product_detail",
     "description": "Given a product id, get its name, brand, category, and specs.",
     "input_schema": {"type": "object",
                      "properties": {"product_id": {"type": "string"}},
                      "required": ["product_id"]}},
]
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_chat_tools.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/config.py src/astor/chat/__init__.py src/astor/chat/tools.py tests/test_chat_tools.py
git commit -m "feat: chat tools over repo reads (compact JSON + referenced items)"
```

---

### Task 2: `chat.agent` — the tool-use loop

**Files:**
- Create: `src/astor/chat/agent.py`
- Test: `tests/test_chat_agent.py`

**Interfaces:**
- Consumes: `tools.TOOL_SCHEMAS`, `tools.dispatch`, `tools.ReferencedItem`, `settings.chat_model`, `settings.anthropic_api_key`.
- Produces:
  - `ChatReply` dataclass: `reply: str`, `items: list[ReferencedItem]`.
  - `run_chat(session, messages: list[dict], *, client=None, model=None, max_iters=6) -> ChatReply`
    where `messages` is `[{"role": "user"|"assistant", "content": str}, ...]`.

**Anthropic response shape the loop reads (real SDK and the test fake both provide it):**
`resp.stop_reason` (str) and `resp.content` (list of blocks). A text block has
`.type == "text"` and `.text`; a tool_use block has `.type == "tool_use"`, `.id`,
`.name`, `.input`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_agent.py
import types
import pytest
from astor.chat import agent, tools
from astor.chat.tools import ReferencedItem


def _text_block(t): return types.SimpleNamespace(type="text", text=t)
def _tool_block(id, name, inp): return types.SimpleNamespace(type="tool_use", id=id, name=name, input=inp)
def _resp(stop, content): return types.SimpleNamespace(stop_reason=stop, content=content)


class _FakeMessages:
    def __init__(self, scripted): self._scripted = list(scripted); self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted): self.messages = _FakeMessages(scripted)


def test_returns_text_when_no_tools(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    client = _FakeClient([_resp("end_turn", [_text_block("Hi, how can I help?")])])
    out = agent.run_chat(object(), [{"role": "user", "content": "hello"}], client=client)
    assert out.reply == "Hi, how can I help?"
    assert out.items == []
    assert client.messages.calls[0]["model"] == agent.settings.chat_model


def test_runs_tool_then_returns_text_and_items(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch",
        lambda s, name, args: ({"protocols": [{"id": "x1", "title": "WB", "product_count": 5}]},
                               [ReferencedItem("protocol", "x1", "WB")]))
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "search_protocols", {"query": "western"})]),
        _resp("end_turn", [_text_block("I found the Western Blot protocol.")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "western blot?"}], client=client)
    assert out.reply == "I found the Western Blot protocol."
    assert out.items == [ReferencedItem("protocol", "x1", "WB")]
    # second create call carried a tool_result back to the model
    second = client.messages.calls[1]["messages"]
    assert any(
        isinstance(m.get("content"), list) and m["content"] and m["content"][0].get("type") == "tool_result"
        for m in second
    )


def test_items_are_deduped(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch",
        lambda s, name, args: ({}, [ReferencedItem("product", "p1", "A"),
                                    ReferencedItem("product", "p1", "A")]))
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "search_products", {"query": "a"})]),
        _resp("end_turn", [_text_block("done")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "a"}], client=client)
    assert out.items == [ReferencedItem("product", "p1", "A")]


def test_iteration_cap_returns_gracefully(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch", lambda s, name, args: ({}, []))
    # always asks for a tool -> never a text stop
    always_tool = _resp("tool_use", [_tool_block("t", "search_products", {"query": "x"})])

    class _Loop:
        def __init__(self): self.messages = self
        def create(self, **k): return always_tool
    out = agent.run_chat(object(), [{"role": "user", "content": "x"}], client=_Loop(), max_iters=3)
    assert isinstance(out.reply, str)  # best-effort, no crash


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        agent.run_chat(object(), [{"role": "user", "content": "hi"}])
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_chat_agent.py -q`
Expected: FAIL (`ModuleNotFoundError` / `run_chat` missing)

- [ ] **Step 3: Implement**

`src/astor/chat/agent.py`:

```python
"""The storefront assistant: a bounded Claude tool-use loop.

`client` is injectable so the loop is testable with a fake Anthropic client; the
real one is built only when client is None. Non-streaming: one ChatReply per turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from astor.chat import tools
from astor.chat.tools import ReferencedItem
from astor.config import settings

log = logging.getLogger(__name__)

SYSTEM = (
    "You are Astor Scientific's procurement assistant. Customers describe an "
    "experiment or a product need; you help them find the right lab protocol and the "
    "Astor products required for it.\n\n"
    "RULES:\n"
    "- Use the tools to find real protocols and products. NEVER invent a product, SKU, "
    "or protocol — only mention ones a tool returned this turn.\n"
    "- If the request is vague, ask ONE focused clarifying question before searching.\n"
    "- Decide freely whether to go protocol-first (find a protocol, then its products) "
    "or product-first (search products, optionally show protocols that use them).\n"
    "- Be concise and practical. Summarize what you found; the UI shows clickable cards "
    "for the items you referenced, so you don't need to paste ids or long lists."
)


@dataclass
class ChatReply:
    reply: str
    items: list[ReferencedItem]


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def run_chat(session, messages, *, client=None, model=None, max_iters: int = 6) -> ChatReply:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the assistant needs it.")
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)
    model = model or settings.chat_model

    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    collected: list[ReferencedItem] = []
    seen: set[tuple[str, str]] = set()
    last_text = ""

    for _ in range(max_iters):
        resp = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM,
            tools=tools.TOOL_SCHEMAS, messages=convo,
        )
        last_text = _text_of(resp) or last_text
        if resp.stop_reason != "tool_use":
            return ChatReply(_text_of(resp), collected)

        convo.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result, items = tools.dispatch(session, block.name, block.input)
            for it in items:
                if (it.type, it.id) not in seen:
                    seen.add((it.type, it.id))
                    collected.append(it)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(result)})
        convo.append({"role": "user", "content": results})

    log.warning("chat loop hit max_iters=%d", max_iters)
    return ChatReply(last_text or "Sorry — I couldn't finish that. Try rephrasing?", collected)
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_chat_agent.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/chat/agent.py tests/test_chat_agent.py
git commit -m "feat: chat agent tool-use loop (injectable client, deduped refs, capped)"
```

---

### Task 3: `POST /api/chat` router

**Files:**
- Create: `src/astor/api/routers/chat.py`
- Modify: `src/astor/api/main.py` (import + include router)
- Test: `tests/api/test_chat.py`

**Interfaces:**
- Consumes: `agent.run_chat`, `agent.ChatReply`, `tools.ReferencedItem`, `get_session`.
- Produces: `POST /api/chat` with body `{"messages": [{"role","content"}]}` →
  `{"reply": str, "items": [{"type","id","name"}]}`. Missing key → `503`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_chat.py
from fastapi.testclient import TestClient
from astor.chat import agent
from astor.chat.tools import ReferencedItem
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, fn):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(agent, "run_chat", fn)
    return TestClient(app)


def test_chat_returns_reply_and_items(monkeypatch):
    def fake(session, messages, **kw):
        return agent.ChatReply("Here you go.", [ReferencedItem("protocol", "x1", "WB")])
    resp = _client(monkeypatch, fake).post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Here you go."
    assert body["items"] == [{"type": "protocol", "id": "x1", "name": "WB"}]


def test_chat_503_when_no_key(monkeypatch):
    def fake(session, messages, **kw):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the assistant needs it.")
    resp = _client(monkeypatch, fake).post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_chat.py -q`
Expected: FAIL (route missing → 404)

- [ ] **Step 3: Implement**

`src/astor/api/routers/chat.py`:

```python
"""Storefront assistant endpoint — a tool-using chat turn."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from astor.api.deps import get_session
from astor.chat import agent

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.post("/chat")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> dict:
    try:
        reply = agent.run_chat(session, [m.model_dump() for m in body.messages])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "reply": reply.reply,
        "items": [{"type": i.type, "id": i.id, "name": i.name} for i in reply.items],
    }
```

In `src/astor/api/main.py`: add `chat` to the routers import and include it:

```python
from astor.api.routers import catalog, chat, dashboard, pricing, protocols
...
    app.include_router(chat.router)
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/api/test_chat.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/chat.py src/astor/api/main.py tests/api/test_chat.py
git commit -m "feat: POST /api/chat endpoint (503 on missing key)"
```

---

### Task 4: Frontend — chat panel, page, api, nav

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`
- Create: `web/components/ChatPanel.tsx`
- Create: `web/app/(shell)/chat/page.tsx`
- Modify: `web/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `/api/chat` (Task 3).
- Produces: `api.sendChat(messages)`; a `/chat` route; a sidebar "Assistant" entry.

- [ ] **Step 1: Add types**

In `web/lib/types.ts` (append):

```ts
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatItem {
  type: "product" | "protocol";
  id: string;
  name: string;
}

export interface ChatResponse {
  reply: string;
  items: ChatItem[];
}
```

- [ ] **Step 2: Add the API method**

In `web/lib/api.ts`: add `ChatMessage, ChatResponse` to the type import, and inside the `api` object:

```ts
  sendChat: async (messages: ChatMessage[]): Promise<ChatResponse> => {
    const res = await fetch(`${BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.detail ?? `Chat failed: ${res.status}`);
    }
    return res.json() as Promise<ChatResponse>;
  },
```

- [ ] **Step 3: Create the ChatPanel client component**

`web/components/ChatPanel.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ChatItem, ChatMessage } from "@/lib/types";

interface Turn {
  role: "user" | "assistant";
  content: string;
  items?: ChatItem[];
  error?: boolean;
}

const EXAMPLES = [
  "I need to run a Western blot for phospho-ERK — what do I need?",
  "What products does a BCA protein assay require?",
  "Find protocols that use Trypsin-EDTA",
];

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const history: ChatMessage[] = [
      ...turns.filter((t) => !t.error).map((t) => ({ role: t.role, content: t.content })),
      { role: "user", content: q },
    ];
    setTurns((t) => [...t, { role: "user", content: q }]);
    setInput("");
    setBusy(true);
    try {
      const res = await api.sendChat(history);
      setTurns((t) => [...t, { role: "assistant", content: res.reply, items: res.items }]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong.";
      setTurns((t) => [...t, { role: "assistant", content: msg, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {turns.length === 0 && (
        <div className="flex flex-col gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => send(ex)}
              className="card p-3 text-left text-sm"
              style={{ color: "var(--muted)" }}
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-3">
        {turns.map((t, i) => (
          <div key={i} className={t.role === "user" ? "self-end" : "self-start"} style={{ maxWidth: "85%" }}>
            <div
              className="rounded-lg px-3 py-2 text-sm"
              style={{
                background: t.role === "user" ? "rgba(94,234,212,0.12)" : "var(--panel)",
                border: "1px solid var(--border)",
                color: t.error ? "#fca5a5" : "inherit",
              }}
            >
              {t.content}
            </div>
            {t.items && t.items.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {t.items.map((it) => (
                  <Link
                    key={`${it.type}-${it.id}`}
                    href={it.type === "product" ? `/products/${it.id}` : `/protocols/${it.id}`}
                    className="rounded px-2 py-1 text-xs"
                    style={{ background: "var(--bg-elev)", border: "1px solid var(--border)", color: "var(--teal)" }}
                  >
                    {it.type === "product" ? "◈" : "⚗"} {it.name}
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="self-start rounded-lg px-3 py-2 text-sm" style={{ color: "var(--muted)" }}>
            thinking…
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(input); }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Describe your experiment or a product you need…"
          className="w-full rounded-lg px-3 py-2 text-sm"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "inherit" }}
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg px-4 py-2 text-sm font-semibold"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", opacity: busy ? 0.5 : 1 }}
        >
          Send
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Create the page**

`web/app/(shell)/chat/page.tsx`:

```tsx
import { ChatPanel } from "@/components/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6">
        <h1 className="text-xl font-bold">Assistant</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          Describe an experiment or a product need — grounded in your protocols and catalog.
        </p>
      </section>
      <ChatPanel />
    </div>
  );
}
```

- [ ] **Step 5: Add the sidebar nav entry**

In `web/components/Sidebar.tsx`, add to the `NAV` array:

```tsx
  { href: "/chat", label: "Assistant", icon: "◆" },
```

- [ ] **Step 6: Verify typecheck + lint**

Run (from `web/`):
```bash
npx tsc --noEmit
npx eslint components/ChatPanel.tsx "app/(shell)/chat/page.tsx" lib/api.ts lib/types.ts
```
Expected: no type errors; eslint clean for the new files. (The pre-existing
`Sidebar.tsx` `set-state-in-effect` error is unrelated to this change — do not
"fix" it here.)

- [ ] **Step 7: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/components/ChatPanel.tsx "web/app/(shell)/chat/page.tsx" web/components/Sidebar.tsx
git commit -m "feat(web): chat assistant panel + /chat page + nav"
```

---

### Task 5: Full-suite regression

**Files:**
- Test: full `pytest`

- [ ] **Step 1: Run the chat + api suites**

Run: `pytest tests/test_chat_tools.py tests/test_chat_agent.py tests/api/test_chat.py -q`
Expected: PASS.

- [ ] **Step 2: Run the whole Python suite**

Run: `pytest -q`
Expected: PASS, no regressions (importing `astor.chat` and the new router must not break existing collection).

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: chat assistant suite green"
```

---

## Self-Review

**Spec coverage:**
- 5 tools over repo + compact JSON + referenced items → Task 1. ✓
- `chat_model` config → Task 1. ✓
- Agent tool-use loop, injectable client, dedupe, cap, no-key RuntimeError → Task 2. ✓
- System prompt / grounding rules → Task 2 (`SYSTEM`). ✓
- `POST /api/chat`, 503 on missing key, registered in main → Task 3. ✓
- Frontend types + `sendChat` + `ChatPanel` (bubbles, items→cards, thinking state, examples) + `/chat` page + sidebar → Task 4. ✓
- Offline tests: tools (monkeypatched repo), agent (fake client), router (monkeypatched run_chat) → Tasks 1–3. ✓
- Non-goals (no cart, no streaming, no auth, no persistence) → respected; none implemented. ✓

**Placeholder scan:** none — every step has complete code.

**Type consistency:** `ReferencedItem(type,id,name)` and `ChatReply(reply,items)` used identically across Tasks 1–3; `run_chat(session, messages, *, client, model, max_iters)` signature matches its callers; `dispatch(session, name, args) -> (dict, list[ReferencedItem])` consistent; tool names identical in `_HANDLERS`, `TOOL_SCHEMAS`, and the schema-coverage test; frontend `ChatResponse{reply, items}` matches the router's response.

**Verified against code:** repo signatures — `list_products(session,q,category,page,page_size)->(list,int)`, `list_protocols(session,q,page,page_size)->(list,int)`, `protocol_materials(...,*,reviewed_only,limit)`, `product_protocols(...,*,reviewed_only,limit)`, `get_product_detail(session,id)`; `settings.anthropic_api_key` exists; anthropic 0.112 `messages.create(tools=...)`; router/test pattern from `tests/api/test_catalog.py`; sidebar `NAV` array + `lib/api.ts` `get<T>` client.

**One caveat for the implementer:** the real Anthropic tool-use loop (`run_chat` with `client=None`) is exercised only against the live API when the user runs the app — the offline tests inject a fake client by design. If the SDK's block/response attribute names differ at runtime, adjust `_text_of` / the `.type`/`.id`/`.name`/`.input` access to match anthropic 0.112, keeping the fake-client tests' shape as the contract.
