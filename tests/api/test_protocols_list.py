"""GET /api/protocols — browse list. Offline: repo layer monkeypatched."""
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

ITEMS = [
    {"id": "x1", "title": "FLAG-TIFA / TRAF6 interaction", "source": "protocols.io",
     "rank_score": 12.3, "product_count": 27},
    {"id": "x2", "title": "Western Blot", "source": "protocols.io",
     "rank_score": 8.1, "product_count": 5},
]


def _client(monkeypatch, fn=None):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "list_protocols", fn or (lambda s, q, page, page_size: (ITEMS, 2)))
    return TestClient(app)


def test_lists_protocols_with_total(monkeypatch):
    resp = _client(monkeypatch).get("/api/protocols")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["items"][0]["product_count"] == 27
    assert body["items"][0]["title"].startswith("FLAG-TIFA")


def test_passes_search_and_paging_through(monkeypatch):
    seen = {}

    def fake(s, q, page, page_size):
        seen.update(q=q, page=page, page_size=page_size)
        return ITEMS, 2

    _client(monkeypatch, fake).get("/api/protocols?q=western&page=2&page_size=10")
    assert seen == {"q": "western", "page": 2, "page_size": 10}
