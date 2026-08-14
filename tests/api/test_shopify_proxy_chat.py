import hashlib
import hmac
from pathlib import Path

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


def test_widget_review_fixes():
    src = WIDGET_JS.read_text()
    assert "!e.isComposing" in src        # IME guard
    assert "__astorChatLoaded" in src     # double-load guard
    assert "messages.pop()" in src        # error-path history fix


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
