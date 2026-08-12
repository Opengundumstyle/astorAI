"""GET /api/protocols/{id}/materials — forward query (a protocol's product cart).

Offline: the router runs with the repo layer monkeypatched, no Postgres needed
(mirrors test_catalog.py / test_product_protocols.py)."""
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

RESULT = {
    "protocol_title": "Transfection then Immunoprecipitation + Western",
    "source_uri": "https://protocols.io/view/z",
    "materials": [
        {"material_name": "Anti-FLAG M2 Affinity Resin", "product_id": "prod-a",
         "product_name": "Anti-DDK(FLAG) Agarose Bead", "brand": "Astor",
         "confidence": 0.774, "kind": "substitute"},
        {"material_name": "NuPAGE LDS sample buffer 4X", "product_id": "prod-b",
         "product_name": "Laemmli Sample Buffer (4X)", "brand": "Astor",
         "confidence": 0.750, "kind": "substitute"},
    ],
}


def _client(monkeypatch, fn=None):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo monkeypatched, session unused
    monkeypatch.setattr(repo, "protocol_materials",
                        fn or (lambda s, pid, *, reviewed_only, limit: RESULT if pid == "x1" else None))
    return TestClient(app)


def test_returns_the_product_shopping_list(monkeypatch):
    resp = _client(monkeypatch).get("/api/protocols/x1/materials")
    assert resp.status_code == 200
    body = resp.json()
    assert body["protocol_id"] == "x1"
    assert body["protocol_title"] == "Transfection then Immunoprecipitation + Western"
    assert body["count"] == 2
    assert body["materials"][0]["product_name"] == "Anti-DDK(FLAG) Agarose Bead"
    assert body["materials"][0]["material_name"] == "Anti-FLAG M2 Affinity Resin"


def test_404_when_protocol_does_not_exist(monkeypatch):
    resp = _client(monkeypatch).get("/api/protocols/nope/materials")
    assert resp.status_code == 404


def test_passes_reviewed_only_and_limit_through(monkeypatch):
    seen = {}

    def fake(s, pid, *, reviewed_only, limit):
        seen["reviewed_only"], seen["limit"] = reviewed_only, limit
        return RESULT

    _client(monkeypatch, fake).get("/api/protocols/x1/materials?reviewed_only=true&limit=8")
    assert seen == {"reviewed_only": True, "limit": 8}
