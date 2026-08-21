from fastapi.testclient import TestClient

from astor.api.main import create_app


def test_healthz_returns_ok():
    resp = TestClient(create_app()).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_healthz_does_not_touch_the_database():
    # No get_session override is installed here. If the handler acquired a session
    # it would try to reach Postgres; a passing assertion proves it does not.
    app = create_app()
    assert TestClient(app).get("/healthz").status_code == 200
    route = next(r for r in app.routes if getattr(r, "path", None) == "/healthz")
    assert route.dependant.dependencies == []
