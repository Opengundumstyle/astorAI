from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

SUMMARY = {"id": "1", "astor_sku": "ASR-7F3A21", "name": "Taq",
           "category": "molecular_biology", "brand": "Vazyme", "mpn": "P112",
           "region": None, "offer_count": 2, "best_landed": 24.8}

DETAIL = {"id": "1", "astor_sku": "ASR-7F3A21", "name": "Taq",
          "category": "molecular_biology", "brand": "Vazyme", "mpn": "P112",
          "specs": {"volume": "5 mL"},
          "offers": [{"supplier": "Sample CN", "region": "CN", "cost": 120.0,
                      "currency": "CNY", "supplier_sku": "VZ-EX-001",
                      "pack_size": "5 mL", "stock": 200, "lead_time_days": 7}],
          "equivalents": [{"id": "2", "astor_sku": "ASR-ABCDEF", "name": "NEB Taq",
                           "brand": "NEB", "region": None, "supplier": None,
                           "confidence": 0.86, "kind": "substitute"}]}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "list_products",
                        lambda s, q, category, page, page_size: ([SUMMARY], 1))
    monkeypatch.setattr(repo, "get_product_detail",
                        lambda s, pid: DETAIL if pid == "1" else None)
    return TestClient(app)


def test_products_list_ops_includes_brand(monkeypatch):
    resp = _client(monkeypatch).get("/api/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["brand"] == "Vazyme"


def test_products_list_buyer_hides_origin(monkeypatch):
    resp = _client(monkeypatch).get("/api/products?role=buyer")
    item = resp.json()["items"][0]
    assert item["astor_sku"] == "ASR-7F3A21"
    for forbidden in ("brand", "mpn", "region"):
        assert forbidden not in item


def test_product_detail_ops(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1")
    assert resp.status_code == 200
    assert resp.json()["offers"][0]["supplier"] == "Sample CN"


def test_product_detail_buyer_strips_offers_and_equivalent_brand(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1?role=buyer")
    body = resp.json()
    assert "offers" not in body
    assert "brand" not in body
    assert "brand" not in body["equivalents"][0]


def test_product_detail_404(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/missing")
    assert resp.status_code == 404
