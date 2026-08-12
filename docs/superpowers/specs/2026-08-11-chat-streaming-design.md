# Chat streaming (SSE) — Design

**Date:** 2026-08-11
**Status:** Approved (design), pending implementation plan
**Scope:** Stream the assistant's reply token-by-token over SSE, interleaved with
the existing tool-use loop. Additive: the non-streaming `/api/chat` stays. Builds
on `docs/superpowers/specs/2026-08-11-chat-assistant-design.md`.

## Problem

Each chat turn currently blocks for a few seconds behind a static "thinking…"
placeholder while the tool-use loop runs, then dumps the whole reply at once. That
reads as laggy. Streaming the final answer as it's generated — with a status line
during tool lookups — makes the assistant feel responsive.

## Decisions (from design review)

- **Transport: SSE.** New `POST /api/chat/stream` → `text/event-stream`. The existing
  `POST /api/chat` (non-stream) stays for tests and as a fallback.
- **Interleave with tools:** stream deltas live; a turn ending in `tool_use` emits a
  `status` event and runs tools; the turn ending in text streams token-by-token.
- **Grounding/tools/cap unchanged** — same `tools.dispatch`, dedupe, `max_iters`.

## Key facts (verified)

- anthropic 0.112: `client.messages.stream(...)` is a context manager; the stream is
  iterable (events have `.type`; a text delta is `content_block_delta` with
  `.delta.type == "text_delta"` and `.delta.text`); `stream.get_final_message()`
  returns the final `Message` (`.stop_reason`, `.content`) after iteration.
- Reuse from the built assistant: `agent.SYSTEM`, `agent._block_to_dict`,
  `tools.TOOL_SCHEMAS`, `tools.dispatch`, `ReferencedItem`, `settings.chat_model`,
  `thinking={"type":"disabled"}` (required — keeps tool-turn content text/tool_use only).
- FastAPI `StreamingResponse`; routers under `src/astor/api/routers/`; frontend
  `lib/api.ts` + `ChatPanel.tsx` (client component).

## Design

### 1. `agent.run_chat_stream(session, messages, *, client=None, model=None, max_iters=6)`

A **generator** yielding event dicts (the router serializes them to SSE):

- `{"type":"status","text": <str>}` — before a tool round; text derived from the tool
  names called (e.g. "Searching protocols…", "Looking up products…", default "Searching…").
- `{"type":"delta","text": <str>}` — each text delta from the streaming turn.
- `{"type":"items","items":[{"type","id","name"}, ...]}` — the deduped referenced
  items, emitted once, right before done.
- `{"type":"done"}` — terminal success.
- `{"type":"error","detail": <str>}` — if the loop raises mid-stream (e.g. an SDK error).

Loop (mirrors `run_chat`, streaming each turn):
```
require key -> else yield {"type":"error", ...}; return
convo = [{role,content} for messages]; collected=[]; seen=set()
for _ in range(max_iters):
    with client.messages.stream(model, max_tokens=1024, system=SYSTEM,
                                tools=TOOL_SCHEMAS, thinking={"type":"disabled"},
                                messages=convo) as stream:
        for event in stream:
            if event.type == "content_block_delta" and getattr(event.delta,"type",None)=="text_delta":
                yield {"type":"delta","text": event.delta.text}
        final = stream.get_final_message()
    if final.stop_reason != "tool_use":
        yield {"type":"items","items": [asdict-ish(i) for i in collected]}
        yield {"type":"done"}; return
    # tool round
    tool_names = [b.name for b in final.content if b.type=="tool_use"]
    yield {"type":"status","text": _status_for(tool_names)}
    convo.append({"role":"assistant","content":[_block_to_dict(b) for b in final.content]})
    results=[]
    for b in final.content:
        if b.type!="tool_use": continue
        result, items = tools.dispatch(session, b.name, b.input)
        for it in items: dedupe into collected/seen
        results.append({"type":"tool_result","tool_use_id":b.id,"content":json.dumps(result)})
    convo.append({"role":"user","content":results})
# cap
yield {"type":"items","items":[...]}; yield {"type":"done"}
```
- `_status_for(names)`: protocols→"Searching protocols…", products→"Searching the catalog…",
  else "Looking that up…".
- The whole `for` body is wrapped so an exception yields `{"type":"error","detail":str(exc)}`
  then returns — the stream never crashes the connection.

### 2. `POST /api/chat/stream` — `src/astor/api/routers/chat.py`

- Reuse the existing `ChatRequest`.
- **Key check up front:** if `settings.anthropic_api_key` is falsy → `HTTPException(503)`
  (a normal error before the stream opens, so the client gets a clean 503, not a broken stream).
- Else return `StreamingResponse(_sse(...), media_type="text/event-stream")` where `_sse`
  wraps each event from `agent.run_chat_stream` as `f"data: {json.dumps(ev)}\n\n"`.
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (disable proxy buffering).

### 3. Frontend

- `lib/api.ts`: `sendChatStream(messages, handlers)` — `fetch` the stream, read
  `response.body` with a reader + `TextDecoder`, split the buffer on `\n\n`, parse each
  `data: ` line as JSON, and invoke handlers: `onStatus(text)`, `onDelta(text)`,
  `onItems(items)`, `onDone()`, `onError(detail)`. On `!res.ok` (e.g. 503) call `onError`
  with `body.detail`.
- `types.ts`: `ChatStreamEvent` union (`status|delta|items|done|error`).
- `ChatPanel.tsx`: on send, push the user turn + an empty assistant turn, then call
  `sendChatStream`: `onStatus` sets a `status` field on the streaming turn (shown in
  muted text above/below the growing bubble); `onDelta` appends to the streaming turn's
  `content`; `onItems` sets its `items`; `onDone` clears busy; `onError` marks the turn
  as an error bubble. The bubble grows live; the "thinking…" placeholder is replaced by
  the streaming reply (a small status line covers the pre-text tool phase).

### 4. Error handling

- Missing key → 503 before the stream (router).
- Mid-stream SDK error → `{"type":"error"}` event → `onError` renders an error bubble;
  the HTTP stream still closes cleanly.
- `max_iters` cap → still emits `items` + `done` (graceful, same as non-stream).

### 5. Testing (offline)

- **agent.run_chat_stream:** a fake client whose `messages.stream(...)` returns a context
  manager that (a) is iterable over canned events (`SimpleNamespace(type=..., delta=...)`)
  and (b) exposes `get_final_message()` → canned `Message`. Tests:
  - immediate text turn → yields `delta`(s) then `items` then `done`, no status.
  - one tool round then text → yields `status`, threads a `tool_result` into the second
    `stream()` call's `messages`, collects/dedupes items, streams the final text, `items`, `done`.
  - missing key → first event is `{"type":"error"}` (or the router 503 path is covered separately).
- **router:** `TestClient` — with key set and `agent.run_chat_stream` monkeypatched to yield
  canned events, assert `200`, `content-type: text/event-stream`, and the body contains the
  serialized `data:` lines; with key unset, assert `503`.
- **Frontend:** `tsc --noEmit` + `eslint` on changed files (no JS harness).

## Non-goals

- No change to `/api/chat` (non-stream), the tools, grounding, or the cap.
- No server-side conversation persistence.
- No ret/resumable streams or partial-failure replay.
