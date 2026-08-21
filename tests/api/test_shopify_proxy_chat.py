import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astor.api.deps import get_session
from astor.api.main import create_app
from astor.api.ratelimit import SlidingWindowLimiter
from astor.api.routers import shopify_proxy as proxy_router
from astor.chat import agent
from astor.chat.tools import ReferencedItem
from astor.config import settings

SECRET = "s3cr3t"


@pytest.fixture(autouse=True)
def _fresh_chat_limiter(monkeypatch):
    """Each test gets its own limiter — the module-level one is a process-wide singleton,
    and without this every future /proxy/chat test would silently share one 20-call budget."""
    monkeypatch.setattr(
        proxy_router, "_chat_limiter",
        SlidingWindowLimiter(proxy_router.settings.proxy_chat_rate_per_min))


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
        return agent.ChatReply("Here you go.",
                               [ReferencedItem("protocol", "x1", "WB", "https://www.protocols.io/view/wb")])
    c = _client(monkeypatch, fake)
    resp = c.post("/proxy/chat", params=_signed({"shop": "astor-dev.myshopify.com"}),
                  json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json() == {"reply": "Here you go.",
                           "items": [{"type": "protocol", "id": "x1", "name": "WB",
                                      "url": "https://www.protocols.io/view/wb"}]}


def test_proxy_chat_threads_identity_into_request_context(monkeypatch):
    # Trust-boundary coverage: a regression that drops shop/customer_id between
    # verify_app_proxy and run_chat's request_context must fail this test.
    captured: dict = {}

    def fake(session, messages, **kw):
        captured.update(kw)
        return agent.ChatReply("ok", [])

    c = _client(monkeypatch, fake)
    resp = c.post(
        "/proxy/chat",
        params=_signed({"shop": "astor-dev.myshopify.com", "logged_in_customer_id": "cust-42"}),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert captured["request_context"] == {"shop": "astor-dev.myshopify.com", "customer_id": "cust-42"}


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


def test_widget_chips_drive_the_chat_not_external_links():
    src = WIDGET_JS.read_text()
    # a chip click asks the assistant about that item (id-grounded), in the chat
    assert "Tell me more about" in src
    assert "id: " in src
    # chips never navigate away from the storefront
    assert "_blank" not in src and "it.url" not in src


def test_widget_formats_bot_replies_safely():
    src = WIDGET_JS.read_text()
    assert "formatReply" in src            # bot text goes through the formatter
    assert "&amp;" in src and "&lt;" in src  # HTML-escaped before any innerHTML
    assert "• " in src                     # markdown/hyphen list markers become bullets


def test_widget_more_chip_expands_remaining():
    src = WIDGET_JS.read_text()
    assert "astor-chip-more" in src
    assert "expandMore" in src            # the "+N more" chip reveals the hidden chips


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


def test_proxy_chat_429_over_the_per_shop_cap(monkeypatch):
    monkeypatch.setattr(proxy_router, "_chat_limiter", SlidingWindowLimiter(limit=2))
    c = _client(monkeypatch, lambda session, messages, **kw: agent.ChatReply("ok", []))
    params = _signed({"shop": "astor-dev.myshopify.com"})
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert c.post("/proxy/chat", params=params, json=body).status_code == 200
    assert c.post("/proxy/chat", params=params, json=body).status_code == 200
    over = c.post("/proxy/chat", params=params, json=body)
    assert over.status_code == 429


def test_proxy_chat_cap_is_per_shop(monkeypatch):
    monkeypatch.setattr(proxy_router, "_chat_limiter", SlidingWindowLimiter(limit=1))
    c = _client(monkeypatch, lambda session, messages, **kw: agent.ChatReply("ok", []))
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert c.post("/proxy/chat", params=_signed({"shop": "a.myshopify.com"}),
                  json=body).status_code == 200
    assert c.post("/proxy/chat", params=_signed({"shop": "a.myshopify.com"}),
                  json=body).status_code == 429
    # A different shop has its own bucket and is unaffected.
    assert c.post("/proxy/chat", params=_signed({"shop": "b.myshopify.com"}),
                  json=body).status_code == 200


def test_proxy_chat_missing_shop_still_capped(monkeypatch):
    # A malformed-but-signed proxy request with no `shop` param must fall back to a
    # shared bucket, not escape the cap entirely.
    monkeypatch.setattr(proxy_router, "_chat_limiter", SlidingWindowLimiter(limit=1))
    c = _client(monkeypatch, lambda session, messages, **kw: agent.ChatReply("ok", []))
    params = _signed({"logged_in_customer_id": "c1"})  # no "shop" key at all
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert c.post("/proxy/chat", params=params, json=body).status_code == 200
    over = c.post("/proxy/chat", params=params, json=body)
    assert over.status_code == 429
