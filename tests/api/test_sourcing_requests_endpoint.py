from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, fn):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "list_sourcing_requests", fn)
    return TestClient(app)


def test_lists_requests(monkeypatch):
    def fake(session, *, limit):
        assert limit == 50
        return [{"id": "1", "requested_item": "Anti-FLAG antibody", "context": "WB",
                 "shop": "astor-dev.myshopify.com", "customer_id": "c9",
                 "email": None, "status": "new", "created_at": "2026-08-14T00:00:00+00:00"}]
    resp = _client(monkeypatch, fake).get("/api/sourcing-requests")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["items"][0]["requested_item"] == "Anti-FLAG antibody"


def test_limit_capped_at_200(monkeypatch):
    seen = {}
    def fake(session, *, limit):
        seen["limit"] = limit
        return []
    _client(monkeypatch, fake).get("/api/sourcing-requests?limit=9999")
    assert seen["limit"] == 200
