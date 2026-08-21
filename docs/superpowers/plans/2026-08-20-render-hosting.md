# Render Hosting (sub-project #3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Astor engine and its Postgres/pgvector database off the founder's laptop onto Render, so the storefront assistant answers 24/7 at a permanent HTTPS URL with the existing data intact and the public attack surface closed.

**Architecture:** A committed Render Blueprint (`render.yaml`) provisions a Docker web service plus a managed Postgres in one connect. The existing 342 MB database moves via `pg_dump`/`pg_restore` (embeddings included — no re-embed, no Voyage spend). Code changes are four small, independently testable additions: a `DATABASE_URL` driver normalizer, a DB-free `/healthz`, an admin-token gate over the whole `/api/*` surface, and a per-shop rate cap on `/proxy/chat`. Shopify's App Proxy is repointed at the `onrender.com` URL once, permanently.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (`postgresql+psycopg`/psycopg3), pydantic-settings, pytest + `fastapi.testclient`, Docker, Render Blueprints, Postgres 16 + pgvector.

**Spec:** `docs/superpowers/specs/2026-08-20-render-hosting-design.md`

## Global Constraints

- **Region:** `oregon` for both the web service and the database. (Operator decision, 2026-08-20.)
- **Web plan:** `starter` (always-on, ~$7/mo). **DB plan:** `basic-256mb` with `diskSizeGB: 10` (data is 342 MB; `diskSizeGB` must be `1` or a multiple of `5`).
- **Blueprint keys:** use `runtime: docker` (the `env:` key in the spec is deprecated) and `autoDeployTrigger: commit` (replaces `autoDeploy: true`). Verified against Render's blueprint spec, 2026-08-20.
- **Demo chat is OFF in production:** Render sets `ENABLE_DEMO_CHAT=false`. Local dev default stays `True`. (Operator decision.)
- **`/api/*` is token-gated in production, not just `/api/sourcing-requests`:** this plan *widens* spec §4's third bullet. `roles.py:19` defaults the caller-supplied `role` to `ops`, so an open `GET /api/products` would publish supplier identity, origin, MPN and cost internals for all 16,016 products; `POST /api/ingest` is an unauthenticated pipeline-triggering write. Both go behind `X-Admin-Token`. (Operator decision.)
- **Never gated:** `/proxy/*` (Shopify App-Proxy-signature gated already) and `/healthz` (Render's health check). `/api/health` also stays open — it is a static `{"status": "ok"}` with no DB access and an existing test asserts it.
- **Fail closed in production:** an unset `ADMIN_TOKEN` means *open* locally, which is what the dev loop needs — but spec §7 requires a deploy missing the token to be a visible operator error, not a silent leak. `ADMIN_TOKEN_REQUIRED=true` (set in `render.yaml`) makes `create_app` refuse to start without a token, so the health check fails and Render holds the previous deploy.
- **Secrets never enter `render.yaml`** (it is committed). They are `sync: false` env vars set in the Render dashboard.
- **Driver:** SQLAlchemy must receive `postgresql+psycopg://`. Render injects a bare `postgres://`-style URL.
- **TDD:** every code task writes the failing test first, watches it fail, then implements. Commit at the end of each task.

---

### Task 1: `DATABASE_URL` driver normalizer

Render's `fromDatabase` injects a connection string like `postgres://user:pass@host/db`. `astor/db/base.py:17` passes `settings.database_url` straight into `create_engine`, and SQLAlchemy resolves the bare `postgres://` / `postgresql://` scheme to psycopg2 (not installed) or rejects it. Normalize at the settings boundary so nothing downstream has to care.

**Files:**
- Modify: `src/astor/config.py:10` (the `database_url` field; add a validator below it)
- Test: `tests/test_config.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `astor.config.Settings.database_url` is guaranteed to carry an explicit `+driver` for Postgres URLs. Task 6's `render.yaml` relies on this to inject Render's raw connection string unmodified.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from astor.config import Settings


def test_bare_postgres_scheme_is_normalized_to_psycopg():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_bare_postgresql_scheme_is_normalized_to_psycopg():
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_explicit_driver_is_left_untouched():
    url = "postgresql+psycopg://astor:astor@localhost:5432/astor"
    assert Settings(database_url=url).database_url == url


def test_query_string_survives_normalization():
    s = Settings(database_url="postgres://u:p@host/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_non_postgres_url_is_left_untouched():
    assert Settings(database_url="sqlite:///./x.db").database_url == "sqlite:///./x.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — the first two tests assert on an unmodified `postgres://...` string.

- [ ] **Step 3: Write minimal implementation**

In `src/astor/config.py`, add `field_validator` to the pydantic import line and insert the validator immediately after the `database_url` field declaration (line 10):

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://astor:astor@localhost:5432/astor"

    @field_validator("database_url")
    @classmethod
    def _force_psycopg_driver(cls, v: str) -> str:
        """Managed hosts (Render) inject a bare `postgres://` URL; SQLAlchemy needs an
        explicit driver or it reaches for psycopg2, which this project does not install."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/astor/config.py tests/test_config.py
git commit -m "feat: normalize managed-host DATABASE_URL to the psycopg driver"
```

---

### Task 2: DB-free `/healthz` for Render's health check

Render polls `healthCheckPath` to gate every rollout. It must not touch Postgres — a DB blip would otherwise roll back a healthy deploy. The existing `/api/health` (`main.py:22`) is already DB-free but lives under the `/api` prefix that Task 3 gates; `/healthz` is a separate, permanently-open route.

**Files:**
- Create: `src/astor/api/routers/health.py`
- Modify: `src/astor/api/main.py:9` (import) and `:26` (registration)
- Test: `tests/api/test_healthz.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `astor.api.routers.health.router` — an `APIRouter` with no prefix exposing `GET /healthz` → `{"ok": True}`. Task 3 must register it *without* the admin dependency; Task 6's `render.yaml` sets `healthCheckPath: /healthz`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_healthz.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_healthz.py -v`
Expected: FAIL with 404 — `/healthz` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `src/astor/api/routers/health.py`:

```python
"""Liveness probe for the platform (Render polls this to gate every rollout).

Deliberately DB-free: a transient Postgres blip must not roll back a healthy deploy.
Unprefixed and never auth-gated — see `create_app`.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
```

In `src/astor/api/main.py`, add `health` to the router import (line 9) and register it first.
Register it ONLY via `include_router` — do not also add an inline `@app.get("/healthz")`;
two registrations would leave the router's copy shadowed and make the OpenAPI schema
describe a handler that never runs. Note the module name `health` collides with the
existing inline `def health()` inside `create_app`; import the module aliased rather than
renaming that pre-existing function, which must stay exactly as it is:

```python
from astor.api.routers import health as health_router
from astor.api.routers import catalog, chat, dashboard, pricing, protocols, shopify_proxy
```

```python
    app.include_router(health_router.router)
    app.include_router(catalog.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/api/test_healthz.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/health.py src/astor/api/main.py tests/api/test_healthz.py
git commit -m "feat: DB-free /healthz endpoint for platform health checks"
```

---

### Task 3: Admin-token gate over the whole `/api/*` surface

Widens spec §4's per-endpoint gate to every internal router, because `role=ops` is the default on the catalog endpoints and `/api/ingest` is an unauthenticated write. Fail-closed by construction: a future router added to the `admin` list inherits the gate.

When `settings.admin_token` is unset (local dev, and every existing test), the dependency is a no-op — so the current test suite must keep passing untouched.

**Files:**
- Create: `src/astor/api/auth.py`
- Modify: `src/astor/config.py` (add `admin_token` beside `log_level`)
- Modify: `src/astor/api/main.py:26-31` (router registration)
- Test: `tests/api/test_admin_auth.py` (create)

**Interfaces:**
- Consumes: `astor.api.routers.health.router` from Task 2 (must stay ungated).
- Produces: `astor.api.auth.require_admin_token(x_admin_token: str | None = Header(default=None)) -> None` — a FastAPI dependency raising `HTTPException(401)` when `settings.admin_token` is set and the `X-Admin-Token` header does not match. Reads `settings` at call time so tests can monkeypatch it. Also `astor.config.Settings.admin_token_required: bool = False`, which makes `create_app()` raise `RuntimeError` when no token is configured. Task 4 reuses the same dependency list for the conditionally-registered chat router; Task 6 sets both env vars.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_admin_auth.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_admin_auth.py -v`
Expected: FAIL — `settings` has no `admin_token` attribute, so `monkeypatch.setattr` raises `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/astor/api/auth.py`:

```python
"""Admin-token gate for the internal API surface.

Everything under `/api/*` is operator-facing: the catalog endpoints default to
`role="ops"` (see `roles.py`), which returns supplier identity, origin, MPN and cost
internals, and `/api/ingest` triggers a pipeline write. On a public host that surface
must not be anonymous. `/proxy/*` is excluded — Shopify's App Proxy signature already
authenticates it — and so are the health probes.

Unset token (local dev) = open, so the developer loop is unchanged.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from astor.config import settings


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.admin_token
    if not expected:
        return
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing admin token")
```

In `src/astor/config.py`, add beside `log_level`:

```python
    # Gates the internal /api/* surface when set (prod). Unset = open (local dev).
    admin_token: str | None = None
    # Public hosts set this true so a missing admin_token is a startup failure rather
    # than a silently open API. See create_app.
    admin_token_required: bool = False

    log_level: str = "INFO"
```

In `src/astor/api/main.py`, import `Depends` and the dependency, then apply it to the internal routers:

```python
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astor.api.auth import require_admin_token
from astor.api.routers import health as health_router
from astor.api.routers import catalog, chat, dashboard, pricing, protocols, shopify_proxy
from astor.config import settings
```

```python
def create_app() -> FastAPI:
    if settings.admin_token_required and not settings.admin_token:
        raise RuntimeError(
            "ADMIN_TOKEN_REQUIRED is set but ADMIN_TOKEN is empty — refusing to start "
            "with an unauthenticated /api/* surface.")

    app = FastAPI(title="AstorScientific API", version="0.1.0")
```

...and then register the routers:

```python
    app.include_router(health_router.router)

    # Internal, operator-facing surface: gated when ADMIN_TOKEN is set.
    admin = [Depends(require_admin_token)]
    app.include_router(catalog.router, dependencies=admin)
    app.include_router(chat.router, dependencies=admin)
    app.include_router(dashboard.router, dependencies=admin)
    app.include_router(pricing.router, dependencies=admin)
    app.include_router(protocols.router, dependencies=admin)

    # Storefront surface: authenticated by the Shopify App Proxy signature instead.
    app.include_router(shopify_proxy.router)
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `python -m pytest tests/api/test_admin_auth.py -v`
Expected: 10 passed.

Run: `python -m pytest -q`
Expected: the full suite still passes — every pre-existing test runs with `admin_token` unset, so the gate is inert for them.

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/auth.py src/astor/config.py src/astor/api/main.py tests/api/test_admin_auth.py
git commit -m "feat: admin-token gate on the internal /api/* surface"
```

---

### Task 4: `ENABLE_DEMO_CHAT` toggle for the unsigned chat routes

`/api/chat` and `/api/chat/stream` run the Anthropic tool-use loop with no signature check. On a public host that is an unmetered bill anyone can run up. When the flag is false the routes are not registered at all — a 404, not a 403, so there is nothing to probe.

**Files:**
- Modify: `src/astor/config.py` (add `enable_demo_chat` beside `chat_model`)
- Modify: `src/astor/api/main.py` (conditional `chat` router registration from Task 3)
- Test: `tests/api/test_demo_chat_toggle.py` (create)

**Interfaces:**
- Consumes: the `admin` dependency list from Task 3.
- Produces: `astor.config.Settings.enable_demo_chat: bool = True`. Task 6's `render.yaml` sets `ENABLE_DEMO_CHAT=false`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_demo_chat_toggle.py`:

```python
from fastapi.testclient import TestClient

from astor.api.main import create_app
from astor.config import settings


def _paths(app) -> set[str]:
    # NOT `app.routes` — FastAPI 0.139+ leaves included routers unflattened there, so a
    # path scan returns nothing and every `not in` assertion would pass vacuously.
    return set(app.openapi()["paths"])


def test_demo_chat_routes_present_by_default(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", True)
    paths = _paths(create_app())
    assert "/api/chat" in paths
    assert "/api/chat/stream" in paths


def test_demo_chat_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    paths = _paths(create_app())
    assert "/api/chat" not in paths
    assert "/api/chat/stream" not in paths


def test_disabled_demo_chat_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    client = TestClient(create_app())
    assert client.post("/api/chat", json={"messages": []}).status_code == 404
    assert client.post("/api/chat/stream", json={"messages": []}).status_code == 404


def test_storefront_proxy_chat_survives_the_toggle(monkeypatch):
    # The revenue path must be unaffected by the demo lockdown.
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    assert "/proxy/chat" in _paths(create_app())


def test_read_routes_survive_the_toggle(monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_chat", False)
    paths = _paths(create_app())
    assert "/api/products" in paths
    assert "/api/sourcing-requests" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_demo_chat_toggle.py -v`
Expected: FAIL — `settings` has no `enable_demo_chat` attribute.

- [ ] **Step 3: Write minimal implementation**

In `src/astor/config.py`, beside `chat_model`:

```python
    anthropic_api_key: str | None = None
    chat_model: str = "claude-sonnet-5"  # storefront assistant (tool-use loop)

    # The unsigned demo chat routes (/api/chat, /api/chat/stream) call Anthropic with
    # no caller authentication. Fine on a laptop behind a tunnel; off on a public host.
    enable_demo_chat: bool = True
```

In `src/astor/api/main.py`, make the chat registration conditional (`settings` is already imported by Task 3):

```python
    app.include_router(catalog.router, dependencies=admin)
    if settings.enable_demo_chat:
        app.include_router(chat.router, dependencies=admin)
    app.include_router(dashboard.router, dependencies=admin)
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `python -m pytest tests/api/test_demo_chat_toggle.py -v`
Expected: 5 passed.

Run: `python -m pytest -q`
Expected: full suite green — `tests/api/test_chat.py` and `tests/api/test_chat_stream.py` rely on the default `True`.

- [ ] **Step 5: Commit**

```bash
git add src/astor/config.py src/astor/api/main.py tests/api/test_demo_chat_toggle.py
git commit -m "feat: ENABLE_DEMO_CHAT toggle to unregister the unsigned chat routes"
```

---

### Task 5: Per-shop sliding-window rate cap on `/proxy/chat`

A signed App Proxy request is authenticated but not metered — one storefront visitor in a loop is an unbounded Anthropic bill. A single Render instance at this scale makes an in-process limiter sufficient. It lives in its own module with an injected clock so it is unit-testable without sleeping.

**Files:**
- Create: `src/astor/api/ratelimit.py`
- Modify: `src/astor/config.py` (add `proxy_chat_rate_per_min`)
- Modify: `src/astor/api/routers/shopify_proxy.py:38-55` (the `chat` handler)
- Test: `tests/api/test_ratelimit.py` (create)
- Test: `tests/api/test_shopify_proxy_chat.py` (append one endpoint test)

**Interfaces:**
- Consumes: `verify_app_proxy` returns `{"shop": ..., "customer_id": ...}` (`src/astor/api/shopify_proxy.py:41`); `shop` may be `None`.
- Produces: `astor.api.ratelimit.SlidingWindowLimiter(limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic)` with `.allow(key: str) -> bool`, and the module-level instance `astor.api.routers.shopify_proxy._chat_limiter` that tests monkeypatch.

- [ ] **Step 1: Write the failing unit test**

Create `tests/api/test_ratelimit.py`:

```python
from astor.api.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_allows_up_to_the_limit_then_blocks():
    lim = SlidingWindowLimiter(limit=3, window_seconds=60.0, clock=FakeClock())
    assert [lim.allow("shop-a") for _ in range(3)] == [True, True, True]
    assert lim.allow("shop-a") is False


def test_window_slides_open_again():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_seconds=60.0, clock=clock)
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False
    clock.advance(61)
    assert lim.allow("shop-a") is True


def test_partial_window_expiry_frees_exactly_one_slot():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_seconds=60.0, clock=clock)
    lim.allow("shop-a")
    clock.advance(30)
    lim.allow("shop-a")
    clock.advance(31)          # first hit is now outside the window, second is not
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False


def test_keys_are_independent():
    lim = SlidingWindowLimiter(limit=1, window_seconds=60.0, clock=FakeClock())
    assert lim.allow("shop-a") is True
    assert lim.allow("shop-a") is False
    assert lim.allow("shop-b") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_ratelimit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'astor.api.ratelimit'`.

- [ ] **Step 3: Write the limiter**

Create `src/astor/api/ratelimit.py`:

```python
"""In-process sliding-window rate limiting.

A signed App Proxy request is authenticated but not metered; one storefront visitor in
a loop is an unbounded Anthropic bill. At single-instance scale an in-memory window is
enough — move to a shared store only if the web service is scaled out.

The clock is injected so the window is testable without sleeping.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Record a hit for `key` and report whether it fits inside the window."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
```

- [ ] **Step 4: Run the unit tests**

Run: `python -m pytest tests/api/test_ratelimit.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the failing endpoint test**

Append to `tests/api/test_shopify_proxy_chat.py` (its `_client` and `_signed` helpers are already defined at the top of that file):

```python
def test_proxy_chat_429_over_the_per_shop_cap(monkeypatch):
    from astor.api.ratelimit import SlidingWindowLimiter
    from astor.api.routers import shopify_proxy as proxy_router

    monkeypatch.setattr(proxy_router, "_chat_limiter", SlidingWindowLimiter(limit=2))
    c = _client(monkeypatch, lambda session, messages, **kw: agent.ChatReply("ok", []))
    params = _signed({"shop": "astor-dev.myshopify.com"})
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert c.post("/proxy/chat", params=params, json=body).status_code == 200
    assert c.post("/proxy/chat", params=params, json=body).status_code == 200
    over = c.post("/proxy/chat", params=params, json=body)
    assert over.status_code == 429


def test_proxy_chat_cap_is_per_shop(monkeypatch):
    from astor.api.ratelimit import SlidingWindowLimiter
    from astor.api.routers import shopify_proxy as proxy_router

    monkeypatch.setattr(proxy_router, "_chat_limiter", SlidingWindowLimiter(limit=1))
    c = _client(monkeypatch, lambda session, messages, **kw: agent.ChatReply("ok", []))
    body = {"messages": [{"role": "user", "content": "hi"}]}

    assert c.post("/proxy/chat", params=_signed({"shop": "a.myshopify.com"}),
                  json=body).status_code == 200
    assert c.post("/proxy/chat", params=_signed({"shop": "a.myshopify.com"}),
                  json=body).status_code == 429
    # A different shop has its own bucket and is unaffected.
    assert c.post("/proxy/chat", params=_signed({"shop": "b.myshopify.com"}),
                  json=body).status_code == 200
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -k 429 -v`
Expected: FAIL — `shopify_proxy` has no `_chat_limiter` attribute.

- [ ] **Step 7: Wire the limiter into the handler**

In `src/astor/config.py`, beside the other proxy settings:

```python
    shopify_app_proxy_secret: str | None = None

    # Per-shop cap on storefront chat turns (sliding 60s window).
    proxy_chat_rate_per_min: int = 20
```

In `src/astor/api/routers/shopify_proxy.py`, add the imports and the module-level limiter, then guard the handler:

```python
from astor.api.deps import get_session
from astor.api.ratelimit import SlidingWindowLimiter
from astor.api.shopify_proxy import verify_app_proxy
from astor.chat import agent
from astor.config import settings

router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])

_WIDGET_JS = Path(__file__).resolve().parent.parent / "static" / "widget.js"
_chat_limiter = SlidingWindowLimiter(settings.proxy_chat_rate_per_min)
```

```python
@router.post("/chat")
def chat(
    body: ChatRequest,
    ctx: dict = Depends(verify_app_proxy),
    session: Session = Depends(get_session),
) -> dict:
    """Storefront chat turn, verified as a signed App Proxy request. Reuses the same
    agent + response shape as /api/chat; non-streaming."""
    # A missing shop (malformed proxy request that still verified) shares one bucket
    # rather than escaping the cap entirely.
    if not _chat_limiter.allow(ctx["shop"] or "__no_shop__"):
        raise HTTPException(
            status_code=429,
            detail="Too many chat requests right now — please try again in a minute.",
        )
    try:
        reply = agent.run_chat(
            session, [m.model_dump() for m in body.messages],
            request_context={"shop": ctx["shop"], "customer_id": ctx["customer_id"]})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "reply": reply.reply,
        "items": [{"type": i.type, "id": i.id, "name": i.name, "url": i.url} for i in reply.items],
    }
```

> The `"url": i.url` field above arrived on `main` in `b5a2e0f` (clickable chat chips) after
> this plan was written, and was merged into this branch in `bfd73fa`. Add ONLY the
> rate-limit guard at the top of the handler; leave the rest of the body exactly as it
> currently stands on disk. Do not retype the return statement from an older copy.

- [ ] **Step 8: Run the endpoint tests, then the whole suite**

Run: `python -m pytest tests/api/test_shopify_proxy_chat.py -v`
Expected: all pass, including the two new ones. (The pre-existing tests each send at most one chat request against the default cap of 20, so the real module-level limiter does not trip them.)

Run: `python -m pytest -q`
Expected: full suite green.

- [ ] **Step 9: Commit**

```bash
git add src/astor/api/ratelimit.py src/astor/config.py src/astor/api/routers/shopify_proxy.py tests/api/test_ratelimit.py tests/api/test_shopify_proxy_chat.py
git commit -m "feat: per-shop sliding-window rate cap on /proxy/chat"
```

---

### Task 6: Render Blueprint (`render.yaml`)

Declarative provisioning of both services from one "New Blueprint" connect. Committed to the repo, so it carries no secrets.

**Files:**
- Create: `render.yaml` (repo root)
- Modify: `.env.example` (document the new settings)
- Test: `tests/test_render_blueprint.py` (create) — guards the invariants a typo would silently break

**Interfaces:**
- Consumes: `/healthz` (Task 2), `ENABLE_DEMO_CHAT` (Task 4), `ADMIN_TOKEN` (Task 3), the URL normalizer (Task 1).
- Produces: a Render web service named `astor-engine` and a database named `astor-db`. Task 7's runbook refers to both by name.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_blueprint.py`:

```python
from pathlib import Path

import yaml

BLUEPRINT = Path(__file__).resolve().parents[1] / "render.yaml"


def _spec() -> dict:
    return yaml.safe_load(BLUEPRINT.read_text())


def test_blueprint_exists_and_parses():
    assert BLUEPRINT.is_file()
    assert _spec()["services"]


def test_web_service_is_docker_and_health_checked():
    svc = _spec()["services"][0]
    assert svc["type"] == "web"
    assert svc["runtime"] == "docker"          # `env:` is the deprecated spelling
    assert svc["dockerfilePath"] == "./Dockerfile"
    assert svc["healthCheckPath"] == "/healthz"
    assert svc["plan"] == "starter"            # always-on; `free` sleeps
    assert svc["region"] == "oregon"


def test_database_url_is_injected_from_the_managed_database():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["DATABASE_URL"]["fromDatabase"] == {
        "name": "astor-db", "property": "connectionString"}


def test_demo_chat_is_off_in_production():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["ENABLE_DEMO_CHAT"]["value"] == "false"


def test_production_fails_closed_without_an_admin_token():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    assert env["ADMIN_TOKEN_REQUIRED"]["value"] == "true"


def test_no_secret_values_are_committed():
    env = {e["key"]: e for e in _spec()["services"][0]["envVars"]}
    for key in ("ANTHROPIC_API_KEY", "SHOPIFY_APP_PROXY_SECRET",
                "SHOPIFY_CLIENT_SECRET", "ADMIN_TOKEN"):
        assert env[key].get("sync") is False, f"{key} must be dashboard-set"
        assert "value" not in env[key], f"{key} must not carry a committed value"


def test_database_sized_for_the_existing_data():
    db = _spec()["databases"][0]
    assert db["name"] == "astor-db"
    assert db["region"] == "oregon"
    # Live data is 342 MB; diskSizeGB must be 1 or a multiple of 5.
    assert db["diskSizeGB"] >= 10 and db["diskSizeGB"] % 5 == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_blueprint.py -v`
Expected: FAIL — `render.yaml` does not exist. (`pyyaml` 6.0.3 is already present in `.venv`, pulled in transitively; if a clean environment lacks it, add `"pyyaml>=6.0"` to the `dev` extra in `pyproject.toml:23`.)

- [ ] **Step 3: Write the blueprint**

Create `render.yaml`:

```yaml
# Render Blueprint — provisions the Astor engine + its Postgres in one connect.
# Committed, so it holds NO secrets: every `sync: false` var is set in the dashboard.
# Docs: https://render.com/docs/blueprint-spec
services:
  - type: web
    name: astor-engine
    runtime: docker
    dockerfilePath: ./Dockerfile
    dockerContext: .
    plan: starter          # always-on; the free plan sleeps and would cold-start shoppers
    region: oregon
    branch: main
    autoDeployTrigger: commit
    healthCheckPath: /healthz
    envVars:
      # Render injects a bare postgres:// URL; Settings normalizes it to +psycopg.
      - key: DATABASE_URL
        fromDatabase:
          name: astor-db
          property: connectionString
      # The unsigned demo chat routes stay unregistered in production.
      - key: ENABLE_DEMO_CHAT
        value: "false"
      - key: LOG_LEVEL
        value: INFO
      # Dashboard-set secrets.
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: SHOPIFY_APP_PROXY_SECRET
        sync: false
      - key: SHOPIFY_CLIENT_SECRET
        sync: false
      - key: ADMIN_TOKEN          # gates the whole /api/* surface
        sync: false
      # Fail closed: without ADMIN_TOKEN the app refuses to boot, the health check
      # fails, and Render keeps serving the previous deploy.
      - key: ADMIN_TOKEN_REQUIRED
        value: "true"

databases:
  - name: astor-db
    databaseName: astor
    user: astor
    plan: basic-256mb
    region: oregon
    diskSizeGB: 10               # live data is 342 MB; must be 1 or a multiple of 5
    postgresMajorVersion: "16"   # matches the local pgvector/pgvector:pg16 dump
```

Append to `.env.example`:

```bash
# Gates the internal /api/* surface. Unset locally = open; REQUIRED in production.
# ADMIN_TOKEN=

# Set to false on a public host: unregisters the unsigned /api/chat routes.
# ENABLE_DEMO_CHAT=true

# Per-shop cap on storefront chat turns, sliding 60s window.
# PROXY_CHAT_RATE_PER_MIN=20
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_render_blueprint.py -v`
Expected: 7 passed.

- [ ] **Step 5: Validate the Docker build locally before Render ever sees it**

Run: `docker build -t astor-engine:test .`
Expected: build succeeds.

Run: `docker run --rm -e PORT=8010 -e DATABASE_URL="postgres://astor:astor@host.docker.internal:5432/astor" -e ADMIN_TOKEN=local-test -e ADMIN_TOKEN_REQUIRED=true -e ENABLE_DEMO_CHAT=false -p 8010:8010 astor-engine:test`
Then, in a second shell: `curl -s localhost:8010/healthz` → `{"ok":true}`; `curl -s -o /dev/null -w '%{http_code}\n' localhost:8010/api/stats` → `401`; `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8010/api/chat` → `404`.
This proves the bare `postgres://` URL, the health check, the admin gate and the demo lockdown all work in the actual image.

Then prove the fail-closed guard fires — rerun the same command with `-e ADMIN_TOKEN=` (empty) and expect the container to exit immediately with `RuntimeError: ADMIN_TOKEN_REQUIRED is set but ADMIN_TOKEN is empty`. Stop the container when done.

> If `postgresMajorVersion` is rejected by Render's blueprint validator on connect, drop that one line and instead confirm the version in the dashboard — `pg_restore` from 16 into a later major works for this schema.

- [ ] **Step 6: Commit**

```bash
git add render.yaml .env.example tests/test_render_blueprint.py
git commit -m "feat: Render Blueprint for the engine + managed Postgres"
```

---

### Task 7: Cutover runbook — provision, migrate the data, repoint Shopify

The one-time operator steps. This task produces a committed runbook and then executes it. Nothing here is unit-testable; the verification checklist in Step 6 *is* the test, and every command below is real, not illustrative.

`pg_dump`/`pg_restore` run **inside the existing `astorai-db-1` container**, which guarantees a client version matching the source database and avoids installing Postgres client tools on the host.

**Before you start, three things this runbook depends on:**
- The Render database is internal-only (`ipAllowList: []`). The one-time `pg_restore` in Step 4 and any laptop-run pipeline in Step 8 require temporarily adding your IP in the Render dashboard's database Access Control page, and removing it again once you're done — an internet-reachable database is a visible, temporary act, never the committed default.
- `POST /api/ingest` against the hosted engine will write junk embeddings unless `EMBEDDINGS_PROVIDER` and its matching API key are configured there. As of the whole-branch review fix, a configured real provider (`voyage`/`openai`) with a missing key now raises instead of silently degrading to `DevEmbedder` — that failure is the intended behavior, not a bug to work around.
- `/docs`, `/redoc`, and `/openapi.json` are disabled on the Render instance (`ADMIN_TOKEN_REQUIRED=true` makes it a "public host"). Read the API schema against a local instance instead.

**Files:**
- Create: `docs/render-runbook.md`
- Modify: `docs/handoff-2026-08-14-shopify-storefront-chat.md` (retire the ngrok section)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: a live `https://astor-engine.onrender.com`, and the App Proxy pointed at `https://astor-engine.onrender.com/proxy`.

- [ ] **Step 1: Push the branch and connect the Blueprint**

```bash
git push origin main
```

In the Render dashboard: **New → Blueprint** → connect this repo → Render reads `render.yaml` and shows `astor-engine` + `astor-db`. Set the four `sync: false` secrets when prompted:
- `ANTHROPIC_API_KEY` and `SHOPIFY_CLIENT_SECRET` / `SHOPIFY_APP_PROXY_SECRET` — copy from the local `.env`.
- `ADMIN_TOKEN` — generate a fresh one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Store it in your password manager; it is not written to the repo. (`ADMIN_TOKEN_REQUIRED=true` already comes from the blueprint, so leaving this blank makes the service refuse to boot — by design.)

Apply. The first deploy will fail its health check until the database exists — that is expected and self-corrects once the DB finishes provisioning.

- [ ] **Step 2: Enable the extensions on the Render database**

Copy the **External Database URL** from the `astor-db` dashboard page, then:

```bash
export RENDER_DATABASE_URL='<paste the External Database URL>'
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c \
  'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;'
```

Expected: `CREATE EXTENSION` (or a notice that it already exists). Extensions must exist *before* the restore, because the dump's column types reference `vector`.

- [ ] **Step 3: Dump the local database**

```bash
docker exec astorai-db-1 pg_dump -U astor -d astor --no-owner --no-acl -Fc \
  > /private/tmp/astor-$(git rev-parse --short HEAD).dump
ls -lh /private/tmp/astor-*.dump
```

Expected: a file on the order of 250-350 MB. `-Fc` (custom format) is required for `pg_restore`.

- [ ] **Step 4: Restore into Render**

```bash
docker exec -i astorai-db-1 pg_restore --no-owner --no-acl --no-comments \
  -d "$RENDER_DATABASE_URL" < /private/tmp/astor-*.dump
```

Expected: completes in a few minutes. `errors ignored on restore` mentioning `extension "vector" already exists` or `must be owner of extension` is **benign** — you pre-created them in Step 2. Any error mentioning a *table* or *row* is not benign; stop and investigate before continuing.

- [ ] **Step 5: Verify the data landed intact**

```bash
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c "
  SELECT 'products' t, count(*) FROM products
  UNION ALL SELECT 'equivalences', count(*) FROM equivalences
  UNION ALL SELECT 'protocols', count(*) FROM protocols
  UNION ALL SELECT 'protocol_material_links', count(*) FROM protocol_material_links
  UNION ALL SELECT 'supplier_offers', count(*) FROM supplier_offers
  UNION ALL SELECT 'sourcing_requests', count(*) FROM sourcing_requests;"
```

Expected, per the spec's verified baseline: products **16,016**, equivalences **~314,000**, protocols **862**, protocol_material_links **827**, supplier_offers **15,988**.

Then prove the embeddings survived — an ANN query must return rows, not an error. The
column is `products.embedding` and its HNSW index is built with `vector_cosine_ops`
(`src/astor/db/models.py:106-110`), so the query must use the cosine operator `<=>` to
exercise that index:

```bash
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c "
  SELECT id, name FROM products
  WHERE embedding IS NOT NULL
  ORDER BY embedding <=> (SELECT embedding FROM products WHERE embedding IS NOT NULL LIMIT 1)
  LIMIT 5;"
```

Expected: 5 rows, the first being the reference product itself (distance 0).

Also confirm the index itself survived the restore:

```bash
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c \
  "SELECT indexname FROM pg_indexes WHERE tablename = 'products';"
```

Expected: the list includes `ix_product_embedding_hnsw`.

- [ ] **Step 6: Verify the running service**

Redeploy `astor-engine` from the dashboard so it picks up the now-populated database, then:

```bash
BASE=https://astor-engine.onrender.com
curl -s $BASE/healthz                                             # {"ok":true}
curl -s -o /dev/null -w 'demo chat: %{http_code}\n' -X POST $BASE/api/chat   # 404
curl -s -o /dev/null -w 'api unauthed: %{http_code}\n' $BASE/api/stats       # 401
curl -s -o /dev/null -w 'api authed: %{http_code}\n' \
  -H "X-Admin-Token: $ADMIN_TOKEN" $BASE/api/stats                           # 200
curl -s -o /dev/null -w 'proxy unsigned: %{http_code}\n' \
  "$BASE/proxy/ping?shop=astor-dev.myshopify.com"                            # 401
```

All five must match the commented expectations. A `200` on `/api/stats` without the header means the gate is not wired — stop and fix before repointing Shopify.

- [ ] **Step 7: Repoint Shopify's App Proxy**

In the Shopify Dev Dashboard → the Astor app → **App proxy**:
- Subpath prefix: `apps`, Subpath: `astor`
- **Proxy URL:** `https://astor-engine.onrender.com/proxy`
- Save and **Release** the version.

Then load a storefront page carrying the widget and send one message. Expected: a real reply. Because Render serves the JS with no interstitial, the theme snippet can revert to the plain loader:

```liquid
<script src="/apps/astor/widget.js" defer></script>
```

- [ ] **Step 8: Retire the laptop tunnel**

Only after Step 7 answers correctly:

```bash
pkill -f 'ngrok http 8000'
pkill -f 'cloudflared tunnel'     # leftover from the pre-ngrok setup
```

The local uvicorn and the `astorai-db-1` container can keep running for development — but
Render's Postgres is now the source of truth. The offline pipeline still runs from the
laptop; point it at Render by overriding `DATABASE_URL` for the command (the normalizer
from Task 1 accepts Render's URL as-is):

```bash
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.ingest_shopify
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.load_protocols --serving-basis "<ref>" --extract-materials
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.match_materials --dry-run
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.backfill_embeddings
```

Run `--dry-run` first on anything that writes: these now hit production data.

- [ ] **Step 9: Write the runbook and update the handoff**

Create `docs/render-runbook.md` capturing Steps 1-8 as the standing operational reference — provisioning, the dump/restore commands, the verification checklist, the App Proxy setting, how to rotate `ADMIN_TOKEN`, and the note that deploys are now `git push origin main`.

In `docs/handoff-2026-08-14-shopify-storefront-chat.md`, replace the ngrok section (lines ~72-90) with a pointer to the Render URL and the new runbook, so the next session does not restart a tunnel.

- [ ] **Step 10: Commit**

```bash
git add docs/render-runbook.md docs/handoff-2026-08-14-shopify-storefront-chat.md
git commit -m "docs: Render cutover runbook; retire the laptop tunnel"
git push origin main
```

---

## Post-cutover notes

- **Cost:** ~$7/mo web + ~$7/mo Postgres. The `starter` web plan is deliberate — `free` sleeps, which would cold-start real shoppers.
- **Deploys:** `git push origin main` → Render rebuilds the Dockerfile → the `/healthz` check gates the rollout. A failing check holds the previous deploy.
- **The internal Next.js dashboard** (`web/`) now needs `NEXT_PUBLIC_API_URL=https://astor-engine.onrender.com` *and* a way to send `X-Admin-Token`. It currently sends no header, so it will 401 against Render. It still works unchanged against the local API. Wiring the dashboard's auth is deliberately out of scope here — treat it as the next sub-project.
- **The Task 5 rate cap is a per-STORE cap, not a per-visitor cap — this does not close the unmetered-bill hole by itself.** The limiter keys on `shop`, and Astor has exactly one shop, so all storefront visitors share one bucket. Two consequences: (a) one visitor sending 20 messages in a minute serves 429 to every other shopper for the rest of that window — a trivial self-inflicted denial of the revenue path; (b) 20/min sustained is ~28,800 chat turns/day, which is a three-figure daily Anthropic bill, not a cap. Tracked follow-ups: a per-visitor key (`f"{shop}:{customer_id or 'anon'}"`) with the current shop-wide bucket retained as an outer limit, plus a coarse daily counter to actually bound spend.
- **Deferred to a later hardening pass** (spec §9): signed-`timestamp` freshness checks, request-size caps, the diverged alembic baseline, and a custom `astorscientific.us` subdomain.
