import io

from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

RESULT = {"extracted": 3, "products": 3, "offers": 3, "equivalences_written": 2}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(
        repo, "run_ingest",
        lambda s, path, supplier, region, tier, run_match: RESULT)
    return TestClient(app)


def test_ingest_csv_returns_summary(monkeypatch):
    files = {"file": ("supplier.csv", io.BytesIO(b"supplier_sku,name\nX,Y\n"), "text/csv")}
    data = {"supplier": "Sample CN", "region": "CN", "tier": "public", "run_match": "true"}
    resp = _client(monkeypatch).post("/api/ingest", files=files, data=data)
    assert resp.status_code == 200
    assert resp.json() == RESULT


def test_ingest_rejects_bad_extension(monkeypatch):
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    data = {"supplier": "Sample CN", "region": "CN", "tier": "public", "run_match": "false"}
    resp = _client(monkeypatch).post("/api/ingest", files=files, data=data)
    assert resp.status_code == 422
