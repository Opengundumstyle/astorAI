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
