# Storefront Chat via App Proxy (sub-project #2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Astor assistant on the Shopify storefront — a proxy-verified chat endpoint plus a theme-embeddable widget the engine serves itself.

**Architecture:** Two new routes on the existing `/proxy` router (both gated by the `verify_app_proxy` dependency from sub-project #1): `POST /proxy/chat` reuses `agent.run_chat` unchanged (non-streaming), and `GET /proxy/widget.js` serves a self-contained vanilla-JS widget. A one-line loader snippet in the theme pulls the widget from the engine, so the widget iterates without theme edits.

**Tech Stack:** Python 3.11, FastAPI/Starlette, stdlib `hmac`/`hashlib`/`pathlib`; vanilla browser JS (no framework, no build).

## Global Constraints

- **No new dependencies.**
- **Everything under `/proxy` stays signature-gated** by `verify_app_proxy` (from #1): bad/missing signature → `401`; no secret configured → `503`. No dev bypass, no unproxied storefront route.
- **`POST /proxy/chat` reuses `agent.run_chat` unchanged** and returns the exact shape `POST /api/chat` returns: `{"reply": <str>, "items": [{"type","id","name"}, ...]}`. On `RuntimeError` → `HTTPException(503, str(exc))`.
- **Non-streaming only.** Do not route SSE through the proxy. Leave `/api/chat`, `/api/chat/stream`, and the demo harness untouched.
- **Widget: reply text + plain, non-clickable item chips.** No product deep-linking / ID→handle mapping.
- **Widget is self-contained** vanilla JS: no framework, no external fetch beyond `<base>/chat`, styles injected inline, all DOM namespaced under `#astor-chat`.
- **Widget derives its proxy base from its own `<script>` tag** (`script[src*="widget.js"]`), not `document.currentScript` (null under `defer`), and POSTs to `<base>/chat`.
- **Widget never hangs or leaks internals:** `AbortController` ~30s timeout; any non-200/timeout/network error → a friendly retry line, never a raw status code.
- Test signatures use the #1 algorithm: hex HMAC-SHA256 over the rendered `key=value` query params (excluding `signature`), **sorted as strings, concatenated with no separator**, keyed by the secret.
- The `/proxy` router is already registered in `main.py` — no registration change needed.

---

### Task 1: `POST /proxy/chat`

**Files:**
- Modify: `src/astor/api/routers/shopify_proxy.py`
- Test: `tests/api/test_shopify_proxy_chat.py`

**Interfaces:**
- Consumes: `verify_app_proxy` (from `astor.api.shopify_proxy`), `get_session` (from `astor.api.deps`), `agent.run_chat(session, messages) -> ChatReply(reply:str, items:list[ReferencedItem(type,id,name)])`.
- Produces: `POST /proxy/chat`, body `{"messages":[{"role","content"}]}` → `{"reply":str, "items":[{"type","id","name"}]}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_shopify_proxy_chat.py
import hashlib
import hmac

from fastapi.testclient import TestClient

from astor.chat import agent
from astor.chat.tools import ReferencedItem
from astor.config import settings
from astor.api.deps import get_session
from astor.api.main import create_app

SECRET = "s3cr3t"


def _sign(params: dict[str, str], secret: str) -> str:
    message = "".join(sorted(f"{k}={v}" for k, v in params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _signed(params: dict[str, str]) -> dict[str, str]:
    return {**params, "signature": _sign(params, SECRET)}


def _client(monkeypatch, run_chat_fn):
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", SECRET)
    monkeypatch.setattr(settings, "shopify_client_secret", SECRET)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(agent, "run_chat", run_chat_fn)
    return TestClient(app)


def test_proxy_chat_returns_reply_and_items(monkeypatch):
    def fake(session, messages, **kw):
        return agent.ChatReply("Here you go.", [ReferencedItem("protocol", "x1", "WB")])
    c = _client(monkeypatch, fake)
    resp = c.post("/proxy/chat", params=_signed({"shop": "astor-dev.myshopify.com"}),
                  json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json() == {"reply": "Here you go.",
                           "items": [{"type": "protocol", "id": "x1", "name": "WB"}]}


def test_proxy_chat_401_on_bad_signature(monkeypatch):
    c = _client(monkeypatch, lambda *a, **k: agent.ChatReply("nope", []))
    resp = c.post("/proxy/chat",
                  params={"shop": "astor-dev.myshopify.com", "signature": "deadbeef"},
                  json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401


def test_proxy_chat_401_without_signature(monkeypatch):
    c = _client(monkeypatch, lambda *a, **k: agent.ChatReply("nope", []))
    resp = c.post("/proxy/chat", params={"shop": "astor-dev.myshopify.com"},
                  json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401


def test_proxy_chat_503_when_run_chat_raises(monkeypatch):
    def boom(session, messages, **kw):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the assistant needs it.")
    c = _client(monkeypatch, boom)
    resp = c.post("/proxy/chat", params=_signed({"shop": "astor-dev.myshopify.com"}),
                  json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -q`
Expected: FAIL (route missing → 404, so the 200 test fails).

- [ ] **Step 3: Implement**

Edit `src/astor/api/routers/shopify_proxy.py`. Replace the import block and add the model + route (keep the existing `/ping` route as-is). The full top of the file becomes:

```python
"""Shopify App Proxy endpoints — reachable only via a signed Shopify proxy request.

The Proxy URL configured in the Shopify app points at `https://<host>/proxy`, and
Shopify appends the storefront subpath, so `store/apps/astor/ping` -> `/proxy/ping`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from astor.api.deps import get_session
from astor.api.shopify_proxy import verify_app_proxy
from astor.chat import agent

router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
```

Then, after the existing `ping` route, add:

```python
@router.post("/chat")
def chat(
    body: ChatRequest,
    ctx: dict = Depends(verify_app_proxy),
    session: Session = Depends(get_session),
) -> dict:
    """Storefront chat turn, verified as a signed App Proxy request. Reuses the same
    agent + response shape as /api/chat; non-streaming."""
    try:
        reply = agent.run_chat(session, [m.model_dump() for m in body.messages])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "reply": reply.reply,
        "items": [{"type": i.type, "id": i.id, "name": i.name} for i in reply.items],
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/shopify_proxy.py tests/api/test_shopify_proxy_chat.py
git commit -m "feat: POST /proxy/chat — storefront chat behind App Proxy"
```

---

### Task 2: The widget — `src/astor/api/static/widget.js`

**Files:**
- Create: `src/astor/api/static/widget.js`
- Test: `tests/api/test_shopify_proxy_chat.py` (append a contract test)

**Interfaces:**
- Produces: a self-contained widget script that, at load, derives its base from
  `script[src*="widget.js"]` and POSTs `{messages}` to `<base>/chat`, rendering
  `{reply, items}`. Consumed by Task 3's `GET /proxy/widget.js` (served verbatim) and the
  theme loader snippet.

- [ ] **Step 1: Write the failing contract test**

Append to `tests/api/test_shopify_proxy_chat.py`:

```python
from pathlib import Path

WIDGET_JS = Path(__file__).resolve().parents[2] / "src" / "astor" / "api" / "static" / "widget.js"


def test_widget_file_exists():
    assert WIDGET_JS.is_file()


def test_widget_has_required_behaviors():
    src = WIDGET_JS.read_text()
    # base derived from its own <script> tag (currentScript is null under defer)
    assert 'script[src*="widget.js"]' in src
    # posts to <base>/chat
    assert "/chat" in src
    # client-side timeout to stay under the App Proxy limit
    assert "AbortController" in src
    # namespaced mount + styles
    assert "astor-chat" in src
    # friendly error copy, no raw status codes shown to shoppers
    assert "having trouble reaching the assistant" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -k widget -q`
Expected: FAIL (`test_widget_file_exists` — file absent).

- [ ] **Step 3: Create the widget**

Create `src/astor/api/static/widget.js` with exactly this content:

```javascript
(function () {
  "use strict";

  // Derive the App Proxy base from this script's own src.
  // (defer scripts run after parsing, so document.currentScript is null — query the tag.)
  var scriptEl = document.querySelector('script[src*="widget.js"]');
  var src = scriptEl ? scriptEl.getAttribute("src") : "/apps/astor/widget.js";
  var base = src.replace(/\/widget\.js.*$/, "");
  var CHAT_URL = base + "/chat";

  var EXAMPLES = [
    "I need to run a Western blot — what do I need?",
    "What products does a BCA protein assay require?",
    "Find protocols that use Trypsin-EDTA",
  ];
  var ERROR_MSG = "I'm having trouble reaching the assistant right now — please try again.";

  var messages = []; // running history: {role, content}
  var busy = false;

  // ---- styles (scoped under #astor-chat) ----
  var style = document.createElement("style");
  style.textContent = [
    "#astor-chat{position:fixed;bottom:20px;right:20px;z-index:2147483000;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}",
    "#astor-chat *{box-sizing:border-box}",
    "#astor-chat .astor-bubble{width:56px;height:56px;border-radius:50%;background:#111;color:#fff;border:none;cursor:pointer;font-size:24px;box-shadow:0 4px 14px rgba(0,0,0,.25)}",
    "#astor-chat .astor-panel{display:none;flex-direction:column;width:360px;max-width:90vw;height:520px;max-height:75vh;background:#fff;border:1px solid #e5e5e5;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.18);overflow:hidden}",
    "#astor-chat.astor-open .astor-panel{display:flex}",
    "#astor-chat.astor-open .astor-bubble{display:none}",
    "#astor-chat .astor-head{padding:12px 14px;background:#111;color:#fff;font-weight:600;display:flex;justify-content:space-between;align-items:center}",
    "#astor-chat .astor-close{background:none;border:none;color:#fff;font-size:18px;cursor:pointer;line-height:1}",
    "#astor-chat .astor-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;background:#fafafa}",
    "#astor-chat .astor-msg{max-width:85%;padding:8px 11px;border-radius:12px;font-size:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}",
    "#astor-chat .astor-user{align-self:flex-end;background:#111;color:#fff}",
    "#astor-chat .astor-bot{align-self:flex-start;background:#fff;border:1px solid #e5e5e5;color:#111}",
    "#astor-chat .astor-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}",
    "#astor-chat .astor-chip{font-size:12px;padding:3px 8px;border:1px solid #ddd;border-radius:999px;background:#f3f3f3;color:#333}",
    "#astor-chat .astor-ex{align-self:flex-start;text-align:left;font-size:13px;padding:7px 10px;border:1px solid #ddd;border-radius:10px;background:#fff;color:#111;cursor:pointer}",
    "#astor-chat .astor-foot{display:flex;gap:6px;padding:10px;border-top:1px solid #eee;background:#fff}",
    "#astor-chat .astor-input{flex:1;padding:9px 11px;border:1px solid #ddd;border-radius:8px;font-size:14px;outline:none}",
    "#astor-chat .astor-send{padding:0 14px;border:none;border-radius:8px;background:#111;color:#fff;cursor:pointer;font-size:14px}",
    "#astor-chat .astor-send:disabled{opacity:.5;cursor:default}",
  ].join("");
  document.head.appendChild(style);

  // ---- mount ----
  var root = document.getElementById("astor-chat");
  if (!root) {
    root = document.createElement("div");
    root.id = "astor-chat";
    document.body.appendChild(root);
  }
  root.innerHTML =
    '<button class="astor-bubble" aria-label="Open chat">&#128172;</button>' +
    '<div class="astor-panel" role="dialog" aria-label="Astor assistant">' +
      '<div class="astor-head"><span>Astor Assistant</span>' +
        '<button class="astor-close" aria-label="Close">&times;</button></div>' +
      '<div class="astor-log"></div>' +
      '<div class="astor-foot">' +
        '<input class="astor-input" type="text" placeholder="Ask about a protocol or product…" />' +
        '<button class="astor-send">Send</button>' +
      '</div>' +
    '</div>';

  var log = root.querySelector(".astor-log");
  var input = root.querySelector(".astor-input");
  var sendBtn = root.querySelector(".astor-send");
  var greeted = false;

  root.querySelector(".astor-bubble").addEventListener("click", openPanel);
  root.querySelector(".astor-close").addEventListener("click", function () {
    root.classList.remove("astor-open");
  });
  sendBtn.addEventListener("click", function () { submit(input.value); });
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(input.value); });

  function openPanel() {
    root.classList.add("astor-open");
    if (!greeted) { greeted = true; renderGreeting(); }
    input.focus();
  }

  function el(cls, text) {
    var d = document.createElement("div");
    d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  }
  function scrollLog() { log.scrollTop = log.scrollHeight; }

  function renderGreeting() {
    log.appendChild(el("astor-msg astor-bot",
      "Hi! Tell me the experiment or product you need, and I'll find the protocol and the Astor products for it."));
    EXAMPLES.forEach(function (ex) {
      var b = document.createElement("button");
      b.className = "astor-ex";
      b.textContent = ex;
      b.addEventListener("click", function () { submit(ex); });
      log.appendChild(b);
    });
    scrollLog();
  }

  function addMsg(role, text) {
    var m = el("astor-msg " + (role === "user" ? "astor-user" : "astor-bot"), text);
    log.appendChild(m);
    scrollLog();
    return m;
  }

  function addChips(container, items) {
    if (!items || !items.length) return;
    var chips = el("astor-chips");
    items.forEach(function (it) { chips.appendChild(el("astor-chip", it.name)); });
    container.appendChild(chips);
    scrollLog();
  }

  function setBusy(b) { busy = b; sendBtn.disabled = b; input.disabled = b; }

  function submit(text) {
    var q = (text || "").trim();
    if (!q || busy) return;
    Array.prototype.slice.call(log.querySelectorAll(".astor-ex")).forEach(function (n) { n.remove(); });
    input.value = "";
    addMsg("user", q);
    messages.push({ role: "user", content: q });
    setBusy(true);
    var typing = addMsg("assistant", "…");
    send(typing);
  }

  function send(typingEl) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 30000);
    fetch(CHAT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: messages }),
      signal: ctrl.signal,
    }).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }).then(function (data) {
      typingEl.textContent = data.reply || "";
      messages.push({ role: "assistant", content: data.reply || "" });
      addChips(typingEl, data.items);
      setBusy(false);
      input.focus();
    }).catch(function () {
      clearTimeout(timer);
      typingEl.textContent = ERROR_MSG;
      // don't record the error in history, so the next turn retries cleanly
      setBusy(false);
      input.focus();
    });
  }
})();
```

- [ ] **Step 4: Run to verify the contract test passes**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -k widget -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/static/widget.js tests/api/test_shopify_proxy_chat.py
git commit -m "feat: vanilla-JS storefront chat widget"
```

---

### Task 3: `GET /proxy/widget.js` + ship the asset

**Files:**
- Modify: `src/astor/api/routers/shopify_proxy.py`
- Modify: `pyproject.toml` (package-data so the asset ships in installs/Docker)
- Test: `tests/api/test_shopify_proxy_chat.py` (append serving tests)

**Interfaces:**
- Consumes: `verify_app_proxy`; the file `src/astor/api/static/widget.js` (Task 2).
- Produces: `GET /proxy/widget.js` → `200`, `Content-Type: application/javascript`, body = widget source.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_shopify_proxy_chat.py`:

```python
def test_widget_js_served_with_valid_signature(monkeypatch):
    c = _client(monkeypatch, lambda *a, **k: agent.ChatReply("", []))
    resp = c.get("/proxy/widget.js", params=_signed({"shop": "astor-dev.myshopify.com"}))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert "astor-chat" in resp.text


def test_widget_js_401_on_bad_signature(monkeypatch):
    c = _client(monkeypatch, lambda *a, **k: agent.ChatReply("", []))
    resp = c.get("/proxy/widget.js",
                 params={"shop": "astor-dev.myshopify.com", "signature": "deadbeef"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -k widget_js -q`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Implement the route**

In `src/astor/api/routers/shopify_proxy.py`, add to the import block:

```python
from pathlib import Path

from fastapi import Response
```

Add a module-level path constant after `router = APIRouter(...)` (this file is
`src/astor/api/routers/shopify_proxy.py`, so `parent.parent` is `src/astor/api`):

```python
_WIDGET_JS = Path(__file__).resolve().parent.parent / "static" / "widget.js"
```

Add the route (after `/chat`):

```python
@router.get("/widget.js")
def widget_js(ctx: dict = Depends(verify_app_proxy)) -> Response:
    """Serve the storefront chat widget. Gated like everything under /proxy; the theme
    loads it from `<store>/apps/astor/widget.js`, which Shopify signs."""
    return Response(
        content=_WIDGET_JS.read_text(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=60"},
    )
```

- [ ] **Step 4: Ship the asset in builds**

The route reads the file via `__file__`, which works for source runs. So it also works
in `pip install`/Docker, add package-data to `pyproject.toml`. After the
`[tool.setuptools.packages.find]` block, add:

```toml
[tool.setuptools.package-data]
"astor.api" = ["static/*.js"]
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -q`
Expected: PASS (all chat + widget + serving tests).

- [ ] **Step 6: Commit**

```bash
git add src/astor/api/routers/shopify_proxy.py pyproject.toml tests/api/test_shopify_proxy_chat.py
git commit -m "feat: GET /proxy/widget.js serves the chat widget; ship asset in builds"
```

---

### Task 4: Runbook — storefront chat section

**Files:**
- Modify: `docs/shopify-app-proxy-runbook.md`

- [ ] **Step 1: Append the section**

Add to the end of `docs/shopify-app-proxy-runbook.md`:

```markdown
## Storefront chat (sub-project #2)

Once the ping verifies, put the assistant on the storefront.

1. Ensure the engine + `cloudflared` tunnel are running and the App Proxy "Proxy URL" in
   the Shopify app still points at the current tunnel (`https://<tunnel>/proxy`). The
   tunnel URL is ephemeral — if it changed, update the app config and Release again.
2. In the dev store admin: **Online Store → Themes → ⋯ → Edit code**, open
   `layout/theme.liquid`, and paste this just before `</body>`, then Save:

   ```html
   <div id="astor-chat"></div>
   <script src="/apps/astor/widget.js" defer></script>
   ```
3. Make sure the storefront isn't password-gated (**Online Store → Preferences →
   Password protection**), then open the storefront. A chat bubble appears bottom-right.
4. Open it and ask, e.g. "I need to run a Western blot — what do I need?" You should get a
   reply with product/protocol chips — served by your local engine, verified through the
   App Proxy.

If the bubble never appears, view-source and confirm `/apps/astor/widget.js` loads (200,
`application/javascript`). If it appears but every message errors, the engine or tunnel is
down, or `ANTHROPIC_API_KEY` isn't set in `.env`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/shopify-app-proxy-runbook.md
git commit -m "docs: storefront chat runbook (theme snippet)"
```

---

### Task 5: Full-suite regression

**Files:**
- Test: full `pytest`

- [ ] **Step 1: Run the sub-project tests**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -q`
Expected: PASS (8 tests: 4 chat, 2 widget contract, 2 widget serving).

- [ ] **Step 2: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions. The new router imports (`agent`, `get_session`) must not
break app construction, and `/api/chat` behavior is unchanged.

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: storefront chat suite green"
```

---

## Self-Review

**Spec coverage:**
- `POST /proxy/chat` reuse `run_chat`, 503 on RuntimeError, verify + session → Task 1. ✓
- `GET /proxy/widget.js` serves `static/widget.js` as `application/javascript`, gated → Task 3. ✓
- Vanilla-JS widget: bubble/panel, message list, typing indicator, example chips, reply +
  plain non-clickable item chips, base derived from own script src, `AbortController` ~30s,
  friendly error → Task 2. ✓
- Offline tests (valid/tampered/missing signature, 503 on raise, widget.js content-type +
  marker) → Tasks 1 & 3; widget structural contract → Task 2. ✓
- Runbook theme-snippet section → Task 4. ✓
- Non-goals (streaming, deep-linking, Theme App Extension, rate-limiting) → none added. ✓
- Loader snippet `<div id="astor-chat"></div><script src="/apps/astor/widget.js" defer>` →
  runbook (Task 4) + widget mounts into `#astor-chat` if present, else self-appends. ✓

**Placeholder scan:** none — every step has complete code/content.

**Type consistency:** `_sign`/`_signed`/`_client` helpers defined once in Task 1 and reused
by Tasks 2–3 in the same file; `ChatReply(reply, items)` and `ReferencedItem(type,id,name)`
match `agent`/`tools`; response shape `{"reply","items":[{type,id,name}]}` identical across
`/proxy/chat` (Task 1) and the widget's render (Task 2); widget marker `astor-chat` asserted
by both the contract test (Task 2) and the serving test (Task 3); `_WIDGET_JS` path
(`parent.parent/static/widget.js` from `routers/`) resolves to the file the contract test
locates via `parents[2]/src/astor/api/static/widget.js`.

**Verified against code:** existing `/api/chat` shape + `RuntimeError`→503 (`routers/chat.py`);
test pattern `dependency_overrides[get_session]=lambda:None` + `monkeypatch.setattr(agent,"run_chat",…)`
(`tests/api/test_chat.py`); `ReferencedItem(type,id,name)` (`chat/tools.py`); proxy router
already registered (`main.py`); `pyproject` uses `[tool.setuptools.packages.find] where=["src"]`.

**One caveat for the implementer:** the App Proxy signs query params, not the POST body — so
the tests sign only `{"shop": ...}` (and any query params) and send `messages` as the JSON
body. That mirrors how Shopify actually proxies a storefront POST. The live dev-store
round-trip (runbook Task 4) is the real end-to-end proof; the offline tests lock the
contract.
