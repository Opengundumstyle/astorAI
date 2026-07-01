from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

BREAKDOWN = {"currency": "USD", "qty": 1, "ex_works": 16.8, "tariff": 4.2,
             "duty_rate": 0.25, "freight": 1.5, "margin": 4.5,
             "unit_price": 27.0, "line_total": 27.0}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "landed_for_product",
                        lambda s, pid, qty: BREAKDOWN if pid == "1" else None)
    return TestClient(app)


def test_landed_cost_ops_full_breakdown(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1/landed-cost")
    assert resp.status_code == 200
    assert resp.json()["ex_works"] == 16.8


def test_landed_cost_buyer_price_only(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1/landed-cost?role=buyer")
    body = resp.json()
    assert body["unit_price"] == 27.0
    for forbidden in ("ex_works", "tariff", "duty_rate", "freight", "margin"):
        assert forbidden not in body


def test_landed_cost_404(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/missing/landed-cost")
    assert resp.status_code == 404
