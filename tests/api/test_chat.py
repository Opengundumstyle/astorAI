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
        return agent.ChatReply("Here you go.",
                               [ReferencedItem("protocol", "x1", "WB", "https://www.protocols.io/view/wb")])
    resp = _client(monkeypatch, fake).post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Here you go."
    assert body["items"] == [{"type": "protocol", "id": "x1", "name": "WB",
                              "url": "https://www.protocols.io/view/wb"}]


def test_chat_503_when_no_key(monkeypatch):
    def fake(session, messages, **kw):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the assistant needs it.")
    resp = _client(monkeypatch, fake).post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
