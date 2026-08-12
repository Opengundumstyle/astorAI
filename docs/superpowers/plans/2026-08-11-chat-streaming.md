# Chat Streaming (SSE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the assistant's reply token-by-token over SSE, interleaved with the tool-use loop, so a chat turn feels responsive.

**Architecture:** A new generator `agent.run_chat_stream` mirrors `run_chat` but streams each turn via `client.messages.stream(...)`, yielding `status`/`delta`/`items`/`done`/`error` event dicts. `POST /api/chat/stream` serializes them as SSE. The `ChatPanel` reads the SSE body and grows the assistant bubble live. Additive — `/api/chat` is untouched.

**Tech Stack:** Python 3.11, FastAPI `StreamingResponse`, `anthropic` 0.112 streaming; Next.js 16 (fetch + ReadableStream). No new dependencies.

## Global Constraints

- **No new dependencies.**
- **Additive:** do NOT change `run_chat`, `/api/chat`, the tools, grounding, `_block_to_dict`, or the `max_iters` cap.
- **`thinking={"type":"disabled"}`** on every `messages.stream(...)` call (required — keeps tool-turn content to text/tool_use only, matching the non-stream loop's fix).
- **Injectable client** (`run_chat_stream(..., client=None)`) — real `Anthropic` built only when `client is None`; tests inject a fake whose `messages.stream(...)` is a context manager (iterable + `get_final_message()`).
- **Missing key:** router raises `HTTPException(503)` BEFORE opening the stream; the generator also yields `{"type":"error"}` defensively.
- **Event shapes (verbatim):** `{"type":"status","text":str}`, `{"type":"delta","text":str}`, `{"type":"items","items":[{"type","id","name"}]}`, `{"type":"done"}`, `{"type":"error","detail":str}`.
- **Referenced items deduped** by `(type,id)`, order preserved; emitted once before `done`.
- Frontend verified by `tsc --noEmit` + `eslint` on changed files (no JS harness). The pre-existing `Sidebar.tsx` eslint error is out of scope.

---

### Task 1: `agent.run_chat_stream` — the SSE event generator

**Files:**
- Modify: `src/astor/chat/agent.py`
- Test: `tests/test_chat_stream.py`

**Interfaces:**
- Consumes: `agent.SYSTEM`, `agent._block_to_dict`, `tools.TOOL_SCHEMAS`, `tools.dispatch`, `tools.ReferencedItem`, `settings.{anthropic_api_key,chat_model}`.
- Produces:
  - `_status_for(tool_names: list[str]) -> str`.
  - `run_chat_stream(session, messages, *, client=None, model=None, max_iters=6)` — a **generator** yielding the event dicts above.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_stream.py
import types
import pytest
from astor.chat import agent, tools
from astor.chat.tools import ReferencedItem


def _delta(t):
    return types.SimpleNamespace(type="content_block_delta",
                                 delta=types.SimpleNamespace(type="text_delta", text=t))
def _text_block(t): return types.SimpleNamespace(type="text", text=t)
def _tool_block(id, name, inp): return types.SimpleNamespace(type="tool_use", id=id, name=name, input=inp)
def _final(stop, content): return types.SimpleNamespace(stop_reason=stop, content=content)


class _FakeStream:
    def __init__(self, events, final): self._events = events; self._final = final
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._events)
    def get_final_message(self): return self._final


class _FakeMessages:
    def __init__(self, scripted): self._scripted = list(scripted); self.calls = []
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted): self.messages = _FakeMessages(scripted)


def _run(client, monkeypatch, dispatch=None):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    if dispatch is not None:
        monkeypatch.setattr(tools, "dispatch", dispatch)
    return list(agent.run_chat_stream(object(), [{"role": "user", "content": "hi"}], client=client))


def test_immediate_text_streams_delta_then_items_then_done(monkeypatch):
    client = _FakeClient([_FakeStream([_delta("Hel"), _delta("lo")],
                                      _final("end_turn", [_text_block("Hello")]))])
    events = _run(client, monkeypatch)
    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "items", "items": []},
        {"type": "done"},
    ]
    assert client.messages.calls[0]["thinking"] == {"type": "disabled"}


def test_tool_round_then_text_emits_status_and_items(monkeypatch):
    client = _FakeClient([
        _FakeStream([], _final("tool_use", [_tool_block("t1", "search_protocols", {"query": "wb"})])),
        _FakeStream([_delta("Found it.")], _final("end_turn", [_text_block("Found it.")])),
    ])
    dispatch = lambda s, name, args: ({"protocols": [{"id": "x1", "title": "WB", "product_count": 5}]},
                                      [ReferencedItem("protocol", "x1", "WB")])
    events = _run(client, monkeypatch, dispatch)
    assert events[0] == {"type": "status", "text": "Searching protocols…"}
    assert {"type": "delta", "text": "Found it."} in events
    assert {"type": "items", "items": [{"type": "protocol", "id": "x1", "name": "WB"}]} in events
    assert events[-1] == {"type": "done"}
    # second stream() call carried a tool_result back
    second = client.messages.calls[1]["messages"]
    assert any(isinstance(m.get("content"), list) and m["content"]
               and m["content"][0].get("type") == "tool_result" for m in second)


def test_items_deduped(monkeypatch):
    client = _FakeClient([
        _FakeStream([], _final("tool_use", [_tool_block("t1", "search_products", {"query": "a"})])),
        _FakeStream([_delta("ok")], _final("end_turn", [_text_block("ok")])),
    ])
    dispatch = lambda s, name, args: ({}, [ReferencedItem("product", "p1", "A"),
                                           ReferencedItem("product", "p1", "A")])
    events = _run(client, monkeypatch, dispatch)
    items_ev = next(e for e in events if e["type"] == "items")
    assert items_ev["items"] == [{"type": "product", "id": "p1", "name": "A"}]


def test_missing_key_yields_error(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", None)
    events = list(agent.run_chat_stream(object(), [{"role": "user", "content": "hi"}]))
    assert events and events[0]["type"] == "error"


def test_status_helper():
    assert agent._status_for(["search_protocols"]) == "Searching protocols…"
    assert agent._status_for(["search_products"]) == "Searching the catalog…"
    assert agent._status_for(["protocol_products"]) == "Pulling the details…"
    assert agent._status_for(["mystery"]) == "Looking that up…"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_chat_stream.py -q`
Expected: FAIL (`run_chat_stream` / `_status_for` missing)

- [ ] **Step 3: Implement**

Append to `src/astor/chat/agent.py` (it already has `SYSTEM`, `_block_to_dict`, `import json`, `from astor.chat import tools`, `from astor.chat.tools import ReferencedItem`, `from astor.config import settings`):

```python
def _status_for(tool_names: list[str]) -> str:
    if "search_protocols" in tool_names:
        return "Searching protocols…"
    if "search_products" in tool_names or "product_detail" in tool_names:
        return "Searching the catalog…"
    if "protocol_products" in tool_names or "product_protocols" in tool_names:
        return "Pulling the details…"
    return "Looking that up…"


def run_chat_stream(session, messages, *, client=None, model=None, max_iters: int = 6):
    """Streaming variant of run_chat: a generator of SSE event dicts. Streams the
    final answer's text deltas; tool rounds emit a status event. Never raises to the
    caller — an error becomes an {"type":"error"} event so the SSE stream closes cleanly."""
    if not settings.anthropic_api_key:
        yield {"type": "error", "detail": "ANTHROPIC_API_KEY is not set — the assistant needs it."}
        return
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)
    model = model or settings.chat_model

    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    collected: list[ReferencedItem] = []
    seen: set[tuple[str, str]] = set()

    def _items_event():
        return {"type": "items", "items": [{"type": i.type, "id": i.id, "name": i.name} for i in collected]}

    try:
        for _ in range(max_iters):
            with client.messages.stream(
                model=model, max_tokens=1024, system=SYSTEM,
                tools=tools.TOOL_SCHEMAS, thinking={"type": "disabled"}, messages=convo,
            ) as stream:
                for event in stream:
                    if (getattr(event, "type", None) == "content_block_delta"
                            and getattr(getattr(event, "delta", None), "type", None) == "text_delta"):
                        yield {"type": "delta", "text": event.delta.text}
                final = stream.get_final_message()

            if final.stop_reason != "tool_use":
                yield _items_event()
                yield {"type": "done"}
                return

            tool_names = [b.name for b in final.content if getattr(b, "type", None) == "tool_use"]
            yield {"type": "status", "text": _status_for(tool_names)}
            convo.append({"role": "assistant", "content": [_block_to_dict(b) for b in final.content]})
            results = []
            for block in final.content:
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

        yield _items_event()
        yield {"type": "done"}
    except Exception as exc:  # noqa: BLE001 — surface as a stream event, never break the connection
        yield {"type": "error", "detail": f"{type(exc).__name__}: {exc}"}
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_chat_stream.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/chat/agent.py tests/test_chat_stream.py
git commit -m "feat: run_chat_stream SSE event generator over the tool-use loop"
```

---

### Task 2: `POST /api/chat/stream` — StreamingResponse

**Files:**
- Modify: `src/astor/api/routers/chat.py`
- Test: `tests/api/test_chat_stream.py`

**Interfaces:**
- Consumes: `agent.run_chat_stream`, existing `ChatRequest`, `settings.anthropic_api_key`, `get_session`.
- Produces: `POST /api/chat/stream` → `text/event-stream` of `data: {json}\n\n`; `503` when the key is missing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_chat_stream.py
from fastapi.testclient import TestClient
from astor.chat import agent
from astor.config import settings
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, gen=None, key="k"):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    if gen is not None:
        monkeypatch.setattr(agent, "run_chat_stream", gen)
    return TestClient(app)


def test_stream_returns_sse_events(monkeypatch):
    def fake(session, messages, **kw):
        yield {"type": "delta", "text": "hi"}
        yield {"type": "items", "items": []}
        yield {"type": "done"}
    resp = _client(monkeypatch, fake).post("/api/chat/stream",
                                           json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert 'data: {"type": "delta", "text": "hi"}' in body
    assert 'data: {"type": "done"}' in body


def test_stream_503_when_no_key(monkeypatch):
    resp = _client(monkeypatch, None, key=None).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_chat_stream.py -q`
Expected: FAIL (route missing → 404)

- [ ] **Step 3: Implement**

In `src/astor/api/routers/chat.py`, add imports and the route (keep the existing `/chat` route). Add at the top with the other imports:

```python
import json

from fastapi.responses import StreamingResponse

from astor.config import settings
```

Add the route after the existing `chat` handler:

```python
@router.post("/chat/stream")
def chat_stream(body: ChatRequest, session: Session = Depends(get_session)):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set — the assistant needs it.")

    def sse():
        for event in agent.run_chat_stream(session, [m.model_dump() for m in body.messages]):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        sse(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/api/test_chat_stream.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/chat.py tests/api/test_chat_stream.py
git commit -m "feat: POST /api/chat/stream (SSE StreamingResponse, 503 on missing key)"
```

---

### Task 3: Frontend — `sendChatStream` + live `ChatPanel`

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`
- Modify: `web/components/ChatPanel.tsx`

**Interfaces:**
- Consumes: `/api/chat/stream` (Task 2).
- Produces: `api.sendChatStream(messages, handlers)`; a `ChatPanel` that renders the reply as it streams.

- [ ] **Step 1: Add the stream handler type**

In `web/lib/types.ts` (append):

```ts
export interface ChatStreamHandlers {
  onStatus?: (text: string) => void;
  onDelta: (text: string) => void;
  onItems?: (items: ChatItem[]) => void;
  onError?: (detail: string) => void;
}
```

- [ ] **Step 2: Add `sendChatStream` to the API client**

In `web/lib/api.ts`: add `ChatStreamHandlers` to the type import, and inside the `api` object (after `sendChat`):

```ts
  sendChatStream: async (messages: ChatMessage[], h: ChatStreamHandlers): Promise<void> => {
    const res = await fetch(`${BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    if (!res.ok || !res.body) {
      const b = await res.json().catch(() => ({}));
      h.onError?.(b.detail ?? `Chat failed: ${res.status}`);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let ev: { type: string; text?: string; items?: ChatItem[]; detail?: string };
        try {
          ev = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (ev.type === "delta" && ev.text != null) h.onDelta(ev.text);
        else if (ev.type === "status" && ev.text != null) h.onStatus?.(ev.text);
        else if (ev.type === "items" && ev.items != null) h.onItems?.(ev.items);
        else if (ev.type === "error") h.onError?.(ev.detail ?? "Something went wrong.");
      }
    }
  },
```

Also add `ChatItem` to the type import in `api.ts` if not already imported (it is used by the parser).

- [ ] **Step 3: Make ChatPanel stream**

Replace the whole `web/components/ChatPanel.tsx` with:

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
  status?: string;
  streaming?: boolean;
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

  function patchLast(fn: (t: Turn) => Turn) {
    setTurns((ts) => {
      const copy = [...ts];
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const history: ChatMessage[] = [
      ...turns.filter((t) => !t.error).map((t) => ({ role: t.role, content: t.content })),
      { role: "user", content: q },
    ];
    setTurns((t) => [
      ...t,
      { role: "user", content: q },
      { role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setBusy(true);
    try {
      await api.sendChatStream(history, {
        onStatus: (s) => patchLast((x) => ({ ...x, status: s })),
        onDelta: (d) => patchLast((x) => ({ ...x, content: x.content + d, status: undefined })),
        onItems: (items) => patchLast((x) => ({ ...x, items })),
        onError: (detail) =>
          patchLast((x) => ({ ...x, content: x.content || detail, error: true, status: undefined })),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong.";
      patchLast((x) => ({ ...x, content: x.content || msg, error: true, status: undefined }));
    } finally {
      patchLast((x) => ({ ...x, streaming: false, status: undefined }));
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
            {t.role === "assistant" && !t.content && (t.status || t.streaming) && (
              <div className="px-1 pb-1 text-xs" style={{ color: "var(--muted)" }}>
                {t.status ?? "thinking…"}
              </div>
            )}
            {t.content && (
              <div
                className="rounded-lg px-3 py-2 text-sm"
                style={{
                  background: t.role === "user" ? "rgba(94,234,212,0.12)" : "var(--panel)",
                  border: "1px solid var(--border)",
                  color: t.error ? "#fca5a5" : "inherit",
                  whiteSpace: "pre-wrap",
                }}
              >
                {t.content}
              </div>
            )}
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
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex gap-2">
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

- [ ] **Step 4: Verify typecheck + lint**

Run (from `web/`):
```bash
npx tsc --noEmit
npx eslint components/ChatPanel.tsx lib/api.ts lib/types.ts
```
Expected: no type errors; eslint clean for these files.

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/components/ChatPanel.tsx
git commit -m "feat(web): stream chat replies live (sendChatStream + growing bubble)"
```

---

### Task 4: Full-suite regression

**Files:**
- Test: full `pytest`

- [ ] **Step 1: Run the chat suites**

Run: `pytest tests/test_chat_stream.py tests/api/test_chat_stream.py tests/test_chat_agent.py tests/api/test_chat.py -q`
Expected: PASS (streaming + the untouched non-stream tests).

- [ ] **Step 2: Run the whole Python suite**

Run: `pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: chat streaming suite green"
```

---

## Self-Review

**Spec coverage:**
- `run_chat_stream` generator, status/delta/items/done/error, streaming loop, dedupe, cap, thinking-disabled → Task 1. ✓
- `_status_for` mapping → Task 1. ✓
- `POST /api/chat/stream` StreamingResponse, SSE framing, 503 on missing key, no-cache/X-Accel headers → Task 2. ✓
- `sendChatStream` SSE reader + `ChatPanel` live render (status line, growing bubble, items on arrival, error bubble) → Task 3. ✓
- Additive: `run_chat` / `/api/chat` untouched → respected (Task 2 keeps the existing route; Task 1 appends). ✓
- Offline tests: fake streaming client (Task 1), monkeypatched generator + TestClient (Task 2) → ✓.

**Placeholder scan:** none — every step has complete code.

**Type consistency:** event dict shapes (`status/delta/items/done/error`) identical across Task 1 (emit), Task 2 (serialize), Task 3 (parse); `run_chat_stream(session, messages, *, client, model, max_iters)` matches its router caller; `_status_for` outputs match the Task 1 test assertions ("Searching protocols…", "Searching the catalog…", "Pulling the details…", "Looking that up…"); frontend `ChatStreamHandlers{onStatus,onDelta,onItems,onError}` matches `sendChatStream`'s usage and the ChatPanel handlers.

**Verified against code:** anthropic 0.112 `messages.stream(...)` context manager + `get_final_message()`; existing `agent.SYSTEM`/`_block_to_dict`/`json`/`tools` imports in `agent.py`; existing `ChatRequest`/`HTTPException`/`Depends(get_session)` in `chat.py`; `lib/api.ts` `get<T>`/BASE pattern and the existing `sendChat`; current `ChatPanel.tsx` structure (this task replaces it wholesale).

**One caveat for the implementer:** the REAL streaming loop (`run_chat_stream` with `client=None`) is exercised only against live Anthropic when the app runs — the offline tests use a fake `messages.stream`. If anthropic 0.112's event attribute names differ at runtime (e.g. the text-delta path), adjust the `event.type`/`event.delta.type`/`event.delta.text` access to match, keeping the fake-stream tests' shape as the contract. The non-stream `/api/chat` remains as a working fallback if streaming needs a runtime tweak.
