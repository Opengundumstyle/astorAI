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
