# Shopify App Proxy Verification (sub-project #1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine verifies Shopify App Proxy-signed requests and exposes one verified `GET /proxy/ping`, so a signed request from a Shopify dev store is proven to reach and pass the engine.

**Architecture:** A pure `valid_app_proxy_signature` (recomputes Shopify's App-Proxy HMAC-SHA256 hex over sorted query params, keyed by the app secret) + a `verify_app_proxy` FastAPI dependency (401/503), consumed by a `/proxy/ping` route. All offline-tested by constructing valid/tampered signatures with `TestClient`. Plus a dev-store setup runbook.

**Tech Stack:** Python 3.11, FastAPI/Starlette, stdlib `hmac`/`hashlib`. No new dependencies.

## Global Constraints

- **No new dependencies.**
- **App Proxy signature (exact):** hex HMAC-SHA256 over the query params *excluding* `signature`, each rendered `key=value` (array/duplicate values joined by `,`), **sorted by key, concatenated with NO separator**, keyed by the app secret. Constant-time compare (`hmac.compare_digest`). This is NOT the webhook HMAC (body/base64/header).
- **Secret:** `settings.shopify_app_proxy_secret` if set, else `settings.shopify_client_secret`.
- **Errors:** missing/invalid/tampered signature → `401`; no secret configured → `503`. No dev bypass.
- **Router prefix `/proxy`** (App Proxy "Proxy URL" points at `https://<tunnel>/proxy`; Shopify appends `/ping`).
- Routers follow the existing pattern (`APIRouter(prefix=...)`, `TestClient` tests, registered in `main.py`).

---

### Task 1: Config secret + `valid_app_proxy_signature` + `verify_app_proxy`

**Files:**
- Modify: `src/astor/config.py` (add `shopify_app_proxy_secret`)
- Create: `src/astor/api/shopify_proxy.py`
- Test: `tests/api/test_shopify_proxy_signature.py`

**Interfaces:**
- Produces:
  - `settings.shopify_app_proxy_secret: str | None` (default `None`).
  - `valid_app_proxy_signature(query_items: list[tuple[str, str]], secret: str) -> bool`.
  - `verify_app_proxy(request: Request) -> dict` — FastAPI dependency; returns `{"shop": <str|None>}`, raises `HTTPException(401)`/`(503)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_shopify_proxy_signature.py
import hashlib
import hmac

from astor.api.shopify_proxy import valid_app_proxy_signature


def _sign(params: dict[str, str], secret: str) -> str:
    message = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    secret = "s3cr3t"
    params = {"shop": "demo.myshopify.com", "timestamp": "1700000000"}
    items = list(params.items()) + [("signature", _sign(params, secret))]
    assert valid_app_proxy_signature(items, secret) is True


def test_wrong_secret_fails():
    params = {"shop": "demo.myshopify.com"}
    items = list(params.items()) + [("signature", _sign(params, "right"))]
    assert valid_app_proxy_signature(items, "wrong") is False


def test_tampered_param_fails():
    secret = "s3cr3t"
    params = {"shop": "demo.myshopify.com", "path_prefix": "/apps/astor"}
    sig = _sign(params, secret)
    tampered = [("shop", "evil.myshopify.com"), ("path_prefix", "/apps/astor"), ("signature", sig)]
    assert valid_app_proxy_signature(tampered, secret) is False


def test_missing_signature_fails():
    assert valid_app_proxy_signature([("shop", "demo.myshopify.com")], "s3cr3t") is False


def test_multi_value_params_joined_by_comma():
    # Shopify joins repeated params with a comma before signing.
    secret = "s3cr3t"
    message = "ids=1,2,3shop=demo.myshopify.com"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    items = [("ids", "1"), ("ids", "2"), ("ids", "3"),
             ("shop", "demo.myshopify.com"), ("signature", sig)]
    assert valid_app_proxy_signature(items, secret) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_shopify_proxy_signature.py -q`
Expected: FAIL (`ModuleNotFoundError: astor.api.shopify_proxy`)

- [ ] **Step 3: Implement**

In `src/astor/config.py`, add after the block of `shopify_*` fields (e.g. after `shopify_supplier_tier`):

```python
    # App Proxy request verification. Same value as the app's API secret key
    # (shopify_client_secret); a dedicated field lets it be scoped/rotated apart.
    shopify_app_proxy_secret: str | None = None
```

Create `src/astor/api/shopify_proxy.py`:

```python
"""Shopify App Proxy request verification.

Shopify signs every App-Proxy'd storefront request with a `signature` query param:
hex HMAC-SHA256 over the OTHER query params — each rendered `key=value` (repeated
values joined by ','), sorted by key, concatenated with NO separator — keyed by the
app's API secret. We recompute and constant-time compare. (This is distinct from the
webhook HMAC, which signs the raw body and arrives base64 in a header.)
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from astor.config import settings


def valid_app_proxy_signature(query_items: list[tuple[str, str]], secret: str) -> bool:
    params: dict[str, list[str]] = {}
    provided = ""
    for key, value in query_items:
        if key == "signature":
            provided = value
            continue
        params.setdefault(key, []).append(value)
    message = "".join(f"{k}={','.join(vals)}" for k, vals in sorted(params.items()))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(provided) and hmac.compare_digest(digest, provided)


def verify_app_proxy(request: Request) -> dict:
    """FastAPI dependency: reject any request not signed by Shopify's App Proxy."""
    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Shopify App Proxy secret not configured.")
    if not valid_app_proxy_signature(list(request.query_params.multi_items()), secret):
        raise HTTPException(status_code=401, detail="invalid App Proxy signature")
    return {"shop": request.query_params.get("shop")}
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/api/test_shopify_proxy_signature.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/config.py src/astor/api/shopify_proxy.py tests/api/test_shopify_proxy_signature.py
git commit -m "feat: Shopify App Proxy signature verification (verify_app_proxy)"
```

---

### Task 2: `GET /proxy/ping` router + registration + HTTP tests

**Files:**
- Create: `src/astor/api/routers/shopify_proxy.py`
- Modify: `src/astor/api/main.py` (import + include router)
- Test: `tests/api/test_shopify_proxy_endpoint.py`

**Interfaces:**
- Consumes: `verify_app_proxy` (Task 1), `settings.shopify_client_secret` / `shopify_app_proxy_secret`.
- Produces: `GET /proxy/ping` → `{"ok": true, "shop": <str|None>}`, gated by `verify_app_proxy`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_shopify_proxy_endpoint.py
import hashlib
import hmac

from fastapi.testclient import TestClient

from astor.config import settings
from astor.api.main import create_app

SECRET = "s3cr3t"


def _sign(params: dict[str, str], secret: str) -> str:
    message = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _client(monkeypatch, secret=SECRET):
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", secret)
    monkeypatch.setattr(settings, "shopify_client_secret", secret)
    return TestClient(create_app())


def test_ping_ok_with_valid_signature(monkeypatch):
    params = {"shop": "demo.myshopify.com", "timestamp": "1700000000"}
    q = {**params, "signature": _sign(params, SECRET)}
    resp = _client(monkeypatch).get("/proxy/ping", params=q)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "shop": "demo.myshopify.com"}


def test_ping_401_on_tamper(monkeypatch):
    params = {"shop": "demo.myshopify.com"}
    sig = _sign(params, SECRET)
    resp = _client(monkeypatch).get("/proxy/ping", params={"shop": "evil.myshopify.com", "signature": sig})
    assert resp.status_code == 401


def test_ping_401_without_signature(monkeypatch):
    resp = _client(monkeypatch).get("/proxy/ping", params={"shop": "demo.myshopify.com"})
    assert resp.status_code == 401


def test_ping_503_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "shopify_app_proxy_secret", None)
    monkeypatch.setattr(settings, "shopify_client_secret", None)
    resp = TestClient(create_app()).get("/proxy/ping", params={"shop": "demo.myshopify.com", "signature": "x"})
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_shopify_proxy_endpoint.py -q`
Expected: FAIL (route missing → 404)

- [ ] **Step 3: Implement**

Create `src/astor/api/routers/shopify_proxy.py`:

```python
"""Shopify App Proxy endpoints — reachable only via a signed Shopify proxy request.

The Proxy URL configured in the Shopify app points at `https://<host>/proxy`, and
Shopify appends the storefront subpath, so `store/apps/astor/ping` -> `/proxy/ping`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from astor.api.shopify_proxy import verify_app_proxy

router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])


@router.get("/ping")
def ping(ctx: dict = Depends(verify_app_proxy)) -> dict:
    """Proof endpoint: returns the verified shop domain from a signed proxy request."""
    return {"ok": True, "shop": ctx["shop"]}
```

In `src/astor/api/main.py`: add `shopify_proxy` to the routers import (alphabetical:
`catalog, chat, dashboard, pricing, protocols, shopify_proxy`) and add
`app.include_router(shopify_proxy.router)` with the others.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/api/test_shopify_proxy_endpoint.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/shopify_proxy.py src/astor/api/main.py tests/api/test_shopify_proxy_endpoint.py
git commit -m "feat: GET /proxy/ping gated by App Proxy verification"
```

---

### Task 3: Dev-store runbook

**Files:**
- Create: `docs/shopify-app-proxy-runbook.md`

- [ ] **Step 1: Write the runbook**

Create `docs/shopify-app-proxy-runbook.md`:

```markdown
# Shopify App Proxy — dev-store round-trip (sub-project #1)

Proves a signed Shopify request reaches and passes the engine. All free; touches nothing live.

## Prerequisites
- The engine running locally: `uvicorn astor.api.main:app --port 8000`.
- `cloudflared` installed (`brew install cloudflared`) — a free tunnel, no signup.

## Steps
1. **Partner account** — sign up at https://partners.shopify.com (free; separate from your store login).
2. **Dev store** — Partner Dashboard → Stores → Add store → **Development store** (free sandbox). Note its `*.myshopify.com` domain.
3. **App** — Partner Dashboard → Apps → **Create app** → name it "Astor Assistant".
4. **Tunnel** — run `cloudflared tunnel --url http://localhost:8000`; copy the printed `https://<random>.trycloudflare.com` URL.
5. **App Proxy** — App → Configuration → **App proxy**:
   - Subpath prefix: `apps`
   - Subpath: `astor`
   - Proxy URL: `https://<random>.trycloudflare.com/proxy`
   - Save.
6. **Secret** — copy the app's **API secret key** (Client credentials) → add to `.env`:
   `SHOPIFY_APP_PROXY_SECRET=<secret>` — then restart the engine.
7. **Install** — install the app on your dev store (App → Test on development store / Select store).
8. **Verify** — open `https://<dev-store>.myshopify.com/apps/astor/ping`.
   - Expect: `{"ok": true, "shop": "<dev-store>.myshopify.com"}`.
   - Tamper the URL (add `&x=1`) → `401 invalid App Proxy signature`. That's correct — the signature no longer matches.

## What this proves
Shopify signed the request, forwarded it through the App Proxy to your engine, and the
engine verified the signature. The same app later installs on astorscientific.us; only the
Proxy URL changes (to the hosted engine) — the verification code is identical.

## Next
- Sub-project #2: route the chat endpoints through `/proxy` and embed the widget in the theme.
```

- [ ] **Step 2: Commit**

```bash
git add docs/shopify-app-proxy-runbook.md
git commit -m "docs: Shopify App Proxy dev-store runbook"
```

---

### Task 4: Full-suite regression

**Files:**
- Test: full `pytest`

- [ ] **Step 1: Run the App Proxy tests**

Run: `pytest tests/api/test_shopify_proxy_signature.py tests/api/test_shopify_proxy_endpoint.py -q`
Expected: PASS.

- [ ] **Step 2: Run the whole suite**

Run: `pytest -q`
Expected: PASS, no regressions (new router import + registration must not break collection).

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: App Proxy suite green"
```

---

## Self-Review

**Spec coverage:**
- `shopify_app_proxy_secret` config, fallback to `shopify_client_secret` → Task 1. ✓
- `valid_app_proxy_signature` (sorted, comma-joined multi-values, no separator, hex, constant-time) → Task 1. ✓
- `verify_app_proxy` dependency (401 invalid, 503 no secret) → Task 1. ✓
- `GET /proxy/ping` → `{ok, shop}`, registered in main, prefix `/proxy` → Task 2. ✓
- Offline tests: valid/tamper/missing/503 via TestClient + pure-fn unit tests → Tasks 1-2. ✓
- Dev-store runbook (Partner acct, dev store, app, App Proxy config, cloudflared, secret, verify) → Task 3. ✓
- Non-goals (no chat/hosting/commerce) → respected; only the ping is exposed. ✓

**Placeholder scan:** none — every step has complete code/content.

**Type consistency:** `valid_app_proxy_signature(query_items: list[tuple[str,str]], secret) -> bool` and `verify_app_proxy(request) -> dict` identical across Task 1 (define) and Task 2 (consume); the `_sign` test helper matches the production concatenation (`"".join(f"{k}={v}")` over sorted single-valued params) so the signature the tests build is exactly what the verifier recomputes; router prefix `/proxy` + `/ping` matches the runbook's Proxy URL.

**Verified against code:** Starlette `request.query_params.multi_items()` returns ordered `(key, value)` tuples preserving duplicates; existing `main.py` router registration + `tests/api/test_chat.py` TestClient pattern; `settings.shopify_client_secret` exists.

**One caveat for the implementer:** the tests construct signatures over *single-valued* params (dict order → sorted), which matches Shopify's algorithm for the common case. The multi-value comma-join is covered by its own unit test. The real Shopify round-trip (actual `signature` from a live proxy request) is verified by the runbook against a dev store — the offline tests are the algorithm contract.
