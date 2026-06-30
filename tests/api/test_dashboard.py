from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

STATS = {"products": 10, "offers": 20,
         "equivalences": {"exact": 3, "substitute": 5, "total": 8},
         "suppliers": 2, "avg_savings": 0.38}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "get_stats", lambda session: STATS)
    return TestClient(app)


def test_stats_endpoint(monkeypatch):
    resp = _client(monkeypatch).get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == STATS
