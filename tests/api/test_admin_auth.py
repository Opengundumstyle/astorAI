import pytest
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app
from astor.config import settings

TOKEN = "adm1n-t0ken"


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "get_stats", lambda session: {"products": 0})
    return TestClient(app)


def test_api_route_401s_without_token_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    assert _client(monkeypatch).get("/api/stats").status_code == 401


def test_api_route_401s_with_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    resp = _client(monkeypatch).get("/api/stats", headers={"X-Admin-Token": "nope"})
    assert resp.status_code == 401


def test_api_route_200s_with_correct_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    resp = _client(monkeypatch).get("/api/stats", headers={"X-Admin-Token": TOKEN})
    assert resp.status_code == 200


def test_api_route_open_when_admin_token_unset(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", None)
    assert _client(monkeypatch).get("/api/stats").status_code == 200


def test_ingest_write_endpoint_is_gated(monkeypatch):
    # /api/ingest triggers the ingest+match pipeline; it must never be anonymous in prod.
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    resp = _client(monkeypatch).post("/api/ingest")
    assert resp.status_code == 401


def test_healthz_is_never_gated(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    assert _client(monkeypatch).get("/healthz").status_code == 200


def test_api_health_stays_open(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    assert _client(monkeypatch).get("/api/health").status_code == 200


def test_create_app_refuses_to_start_when_token_required_but_missing(monkeypatch):
    # Fail-closed: a production deploy that forgot ADMIN_TOKEN must not boot serving
    # an open /api/*. Render's health check then holds the previous deploy.
    monkeypatch.setattr(settings, "admin_token_required", True)
    monkeypatch.setattr(settings, "admin_token", None)
    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        create_app()


def test_create_app_starts_when_token_required_and_present(monkeypatch):
    monkeypatch.setattr(settings, "admin_token_required", True)
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    assert create_app() is not None


def test_proxy_routes_are_not_admin_gated(monkeypatch):
    # /proxy/* is guarded by the Shopify App Proxy signature, not the admin token.
    # Without a signature it must be 401 from verify_app_proxy (or 503 when no secret
    # is configured) — never blocked by, nor passed through by, the admin gate.
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", "s3cr3t")
    monkeypatch.setattr(settings, "shopify_client_secret", "s3cr3t")
    resp = _client(monkeypatch).get("/proxy/ping", params={"shop": "x.myshopify.com"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid App Proxy signature"
