"""GET /api/products/{id}/protocols — reverse query (protocols that use a product).

Offline: the router is exercised with the repo layer monkeypatched, so no Postgres
is needed (mirrors test_catalog.py)."""
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

RESULT = {
    "product_name": "Bicinchoninic Acid (BCA) Protein Assay Kit",
    "protocols": [
        {"title": "Western blotting to detect ATP13A2",
         "source_uri": "https://protocols.io/view/x",
         "material_name": "BCA assay kit (Pierce)", "confidence": 0.867, "kind": "exact"},
        {"title": "Mild Immunoprecipitation with Low Background",
         "source_uri": "https://protocols.io/view/y",
         "material_name": "Pierce BCA Protein Assay", "confidence": 0.81, "kind": "substitute"},
    ],
}


def _client(monkeypatch, fn=None):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "product_protocols",
                        fn or (lambda s, pid, *, reviewed_only, limit: RESULT if pid == "p1" else None))
    return TestClient(app)


def test_returns_protocols_using_the_product(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/p1/protocols")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product_id"] == "p1"
    assert body["product_name"] == "Bicinchoninic Acid (BCA) Protein Assay Kit"
    assert body["count"] == 2
    assert body["protocols"][0]["kind"] == "exact"
    assert body["protocols"][0]["confidence"] == 0.867


def test_404_when_product_does_not_exist(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/nope/protocols")
    assert resp.status_code == 404


def test_passes_reviewed_only_and_limit_through(monkeypatch):
    seen = {}

    def fake(s, pid, *, reviewed_only, limit):
        seen["reviewed_only"], seen["limit"] = reviewed_only, limit
        return RESULT

    _client(monkeypatch, fake).get("/api/products/p1/protocols?reviewed_only=true&limit=10")
    assert seen == {"reviewed_only": True, "limit": 10}
