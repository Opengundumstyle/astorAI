import pytest
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.auth import require_admin_token
from astor.api.deps import get_session
from astor.api.main import create_app
from astor.config import settings

TOKEN = "adm1n-t0ken"


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "get_stats", lambda session: {"products": 0})
    return TestClient(app)


def _effective_api_routes(app):
    """Flatten the live route table under `/api/*`, resolving FastAPI's lazy
    per-include merge (`_IncludedRouter.effective_candidates()`) so that a
    router-level `dependencies=[...]` actually shows up on each route's
    resolved `dependant`. `app.routes` alone does NOT reflect this: an
    included router appears there as an opaque `_IncludedRouter` whose
    wrapped routes still carry their pre-merge (ungated) dependant.

    Returns {path: set-of-dependency-callables}.
    """

    def walk(routes):
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from walk(route.effective_candidates())
            elif hasattr(route, "routes"):
                yield from walk(route.routes)
            else:
                yield route

    found: dict[str, set] = {}
    for route in walk(app.routes):
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api"):
            continue
        dependant = getattr(route, "dependant", None)
        calls = {d.call for d in dependant.dependencies} if dependant else set()
        found[path] = calls
    return found


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


def test_non_ascii_token_is_rejected_cleanly(monkeypatch):
    # hmac.compare_digest raises TypeError on non-ASCII str input; an anonymous
    # request must never be able to raise inside the auth dependency (500s an
    # unauthenticated request, and would brick the whole /api surface if an
    # operator ever generated a token containing a non-ASCII character).
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    client = _client(monkeypatch)
    resp = client.get("/api/stats", headers=[(b"x-admin-token", b"n\xf8pe")])
    assert resp.status_code == 401


def test_every_api_route_is_gated(monkeypatch):
    # Fail-closed by construction: a future router included without the admin
    # dependency list must be caught here, not discovered live on a public URL.
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    app = create_app()
    routes = _effective_api_routes(app)
    gated = {path for path, calls in routes.items() if require_admin_token in calls}
    ungated = set(routes) - gated
    assert len(gated) >= 12, "traversal found too few routes — it is not seeing the real table"
    assert ungated == {"/api/health"}


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
