# Shopify App Proxy + HMAC verification (sub-project #1) — Design

**Date:** 2026-08-12
**Status:** Approved (design), pending implementation plan
**Scope:** The first slice of the real Shopify integration (ARCHITECTURE.md §11/§12):
the engine verifies Shopify **App Proxy**-signed requests, proven end-to-end on a free
**dev store** with a tunnel to the local engine. Minimal — one verified `ping` endpoint.
Deliberately NOT the chat-through-proxy (#2), hosting (#3), or commerce/webhooks (#4).

## Problem

To put the assistant on `astorscientific.us`, the storefront can't call the engine
directly (exposes the URL, CORS, secrets). Shopify's **App Proxy** solves this: the
browser calls `store/apps/astor/...`, Shopify forwards to the engine with a signed
`signature` query param, and the engine verifies it. Nothing else in the launch works
until the engine can trust those proxied requests. This sub-project builds and proves
that trust boundary — on a dev store, at zero cost, touching nothing live.

## Decisions (from brainstorming)

- **Minimal verified ping** — the `verify_app_proxy` dependency + one `GET /proxy/ping`.
  The chat is wired through it in sub-project #2.
- **Dev store first** — build a Partner app with App Proxy, install on a free dev store,
  tunnel App Proxy → local engine. The same app later installs on astorscientific.us.
- **Full setup in the runbook** — assume no Partner account yet.

## Key facts (verified)

- App Proxy signature (distinct from webhook HMAC): Shopify appends `shop`,
  `path_prefix`, `timestamp`, `signature` (and `logged_in_customer_id` when present) to
  the proxied request. `signature` = **hex** HMAC-SHA256 over the query params *excluding
  `signature`*, each rendered `key=value` (array values joined by `,`), **sorted by key
  and concatenated with no separator**, keyed by the app's **API secret key** (= the
  existing `settings.shopify_client_secret`). Verify by recompute + constant-time compare.
  (Webhook HMAC, by contrast, is base64 over the raw body in a header — not used here.)
- The engine is FastAPI (`astor.api.main:create_app`); routers under
  `src/astor/api/routers/` (`APIRouter(prefix=...)`), tested with `TestClient`.
- `settings` already has `shopify_client_secret`; App Proxy config anticipated (§11).

## Design

### 1. Config — `src/astor/config.py`
Add `shopify_app_proxy_secret: str | None = None`. The verifier uses it, falling back to
`shopify_client_secret` (they're the same value — the app's API secret key — but a
dedicated var lets the proxy secret be rotated/scoped separately if ever needed).

### 2. Verifier — `src/astor/api/shopify_proxy.py` (new)
Pure, framework-light:

```python
def valid_app_proxy_signature(query_items: list[tuple[str, str]], secret: str) -> bool:
    # query_items: (key, value) pairs AS RECEIVED (duplicates preserved).
    # Group multi-values, drop `signature`, render key=value (values joined by ","),
    # sort by key, concatenate with NO separator, HMAC-SHA256 hex, constant-time compare.
    params: dict[str, list[str]] = {}
    provided = ""
    for k, v in query_items:
        if k == "signature":
            provided = v
            continue
        params.setdefault(k, []).append(v)
    message = "".join(f"{k}={','.join(vals)}" for k, vals in sorted(params.items()))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(provided) and hmac.compare_digest(digest, provided)
```

FastAPI dependency in the same module:

```python
def verify_app_proxy(request: Request) -> dict:
    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise HTTPException(503, "Shopify App Proxy secret not configured.")
    if not valid_app_proxy_signature(list(request.query_params.multi_items()), secret):
        raise HTTPException(401, "invalid App Proxy signature")
    return {"shop": request.query_params.get("shop")}
```

`request.query_params.multi_items()` preserves order + duplicates (Starlette API).

### 3. Router — `src/astor/api/routers/shopify_proxy.py` (new)
```python
router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])

@router.get("/ping")
def ping(ctx: dict = Depends(verify_app_proxy)) -> dict:
    return {"ok": True, "shop": ctx["shop"]}
```
Registered in `main.py` (`include_router`). Prefix `/proxy` is deliberate: the App Proxy
"Proxy URL" points at `https://<tunnel>/proxy`, and Shopify appends the storefront path
after the subpath, so `store/apps/astor/ping` → `<tunnel>/proxy/ping`.

### 4. Data flow
`browser: store/apps/astor/ping` → Shopify appends `shop/timestamp/signature`, forwards
→ `<tunnel>/proxy/ping?...` → `verify_app_proxy` recomputes the HMAC, matches → `{ok,
shop}`. The browser never sees the engine URL or the secret.

### 5. Error handling
- Missing/invalid/tampered signature → `401`.
- Secret not configured → `503` (clear operator error, not a silent pass).
- No dev bypass: verification is always on; tests inject valid signatures.

### 6. Testing (offline, no Shopify)
`tests/api/test_shopify_proxy.py` — helper computes a valid signature for a param set +
test secret; via `TestClient`:
- valid signature → 200 `{"ok": true, "shop": "<store>"}`.
- tamper a param (recompute stale) → 401.
- omit `signature` → 401.
- secret unset (monkeypatch both to None) → 503.
- `valid_app_proxy_signature` unit tests: multi-value join, sort order, wrong secret.

### 7. Runbook — `docs/shopify-app-proxy-runbook.md` (yours; all free)
1. Create a **Shopify Partner account** (partners.shopify.com).
2. Partner Dashboard → **Stores → Add store → Development store** (free sandbox).
3. Partner Dashboard → **Apps → Create app** ("Astor Assistant").
4. Install `cloudflared`; run `cloudflared tunnel --url http://localhost:8000` → copy the
   `https://….trycloudflare.com` URL.
5. App → **Configuration → App proxy**: Subpath prefix `apps`, Subpath `astor`, Proxy URL
   `https://<tunnel>/proxy`. Save.
6. Copy the app's **API secret key** → `.env`: `SHOPIFY_APP_PROXY_SECRET=<secret>` (or set
   `SHOPIFY_CLIENT_SECRET`). Restart the engine.
7. Install the app on the dev store; visit `https://<dev-store>.myshopify.com/apps/astor/ping`
   → expect `{"ok": true, "shop": "<dev-store>.myshopify.com"}`. Tamper the URL → 401.

## Non-goals
- Chat/read endpoints through the proxy (sub-project #2).
- Hosting the engine/DB (sub-project #3) — the tunnel points at the local engine.
- Webhooks, HMAC-on-body, draft orders, commerce (sub-project #4).
- Production install on astorscientific.us — same app, later, once proven on the dev store.
