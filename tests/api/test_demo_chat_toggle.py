from fastapi.testclient import TestClient

from astor.api.main import create_app
from astor.config import settings


def _paths(app) -> set[str]:
    # NOT `app.routes` — FastAPI 0.139+ leaves included routers unflattened there, so a
    # path scan returns nothing and every `not in` assertion would pass vacuously.
    return set(app.openapi()["paths"])


def test_demo_chat_routes_present_by_default(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", True)
    paths = _paths(create_app())
    assert "/api/chat" in paths
    assert "/api/chat/stream" in paths


def test_demo_chat_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    paths = _paths(create_app())
    assert "/api/chat" not in paths
    assert "/api/chat/stream" not in paths


def test_disabled_demo_chat_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    client = TestClient(create_app())
    assert client.post("/api/chat", json={"messages": []}).status_code == 404
    assert client.post("/api/chat/stream", json={"messages": []}).status_code == 404


def test_storefront_proxy_chat_survives_the_toggle(monkeypatch):
    # The revenue path must be unaffected by the demo lockdown.
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    assert "/proxy/chat" in _paths(create_app())


def test_read_routes_survive_the_toggle(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    paths = _paths(create_app())
    assert "/api/products" in paths
    assert "/api/sourcing-requests" in paths
