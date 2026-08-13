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
