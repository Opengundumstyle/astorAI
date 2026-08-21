import inspect

from fastapi.testclient import TestClient

from astor.api.main import create_app
from astor.api.routers.health import healthz


def test_healthz_returns_ok():
    resp = TestClient(create_app()).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_healthz_is_registered_on_the_app():
    # NOTE: do not introspect `app.routes` for this. FastAPI 0.139+ does not flatten
    # included routers into it — an included router appears as one opaque object with
    # `path=None`, so a path scan finds nothing and tempts a duplicate inline route.
    # The public OpenAPI schema is the version-stable way to ask what is registered.
    assert "/healthz" in create_app().openapi()["paths"]


def test_healthz_takes_no_dependencies():
    # No parameters means no `Depends(...)`, hence no DB session: a Postgres blip
    # cannot fail Render's health check and roll back a healthy deploy.
    assert inspect.signature(healthz).parameters == {}
