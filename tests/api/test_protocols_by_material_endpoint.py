from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, fn):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "protocols_by_material", fn)
    return TestClient(app)


def test_returns_payload(monkeypatch):
    def fake(session, material, *, limit):
        assert material == "trypsin" and limit == 10
        return {"total": 2, "protocols": [
            {"id": "p1", "title": "Cell passaging", "product_count": 3, "matched_material": "Trypsin-EDTA"}]}
    resp = _client(monkeypatch, fake).get("/api/protocols/by-material?q=trypsin")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert resp.json()["protocols"][0]["matched_material"] == "Trypsin-EDTA"


def test_limit_is_capped_at_50(monkeypatch):
    seen = {}
    def fake(session, material, *, limit):
        seen["limit"] = limit
        return {"total": 0, "protocols": []}
    _client(monkeypatch, fake).get("/api/protocols/by-material?q=x&limit=999")
    assert seen["limit"] == 50


def test_missing_q_returns_empty_payload(monkeypatch):
    called = {"n": 0}
    def fake(session, material, *, limit):
        called["n"] += 1
        return {"total": 0, "protocols": []}
    resp = _client(monkeypatch, fake).get("/api/protocols/by-material")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "protocols": []}
    assert called["n"] == 0  # short-circuits, never calls repo with an empty term
