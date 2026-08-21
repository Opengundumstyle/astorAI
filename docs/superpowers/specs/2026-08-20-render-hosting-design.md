# Host the engine + DB on Render (sub-project #3) — Design

**Date:** 2026-08-20
**Status:** Approved (design), pending implementation plan
**Scope:** Move the Astor engine + Postgres/pgvector DB off the founder's laptop onto Render
so the storefront assistant is always-on at a permanent HTTPS URL, with the current data
migrated intact, Shopify pointed at it, and the worst public-cost/exposure hole closed.
Retires the ephemeral tunnel + ngrok interstitial pain entirely. Non-devops, coffee-money
(~$14/mo), low-ops.

## Problem

The engine, the Postgres DB (342 MB, pgvector), and the public tunnel all run on the
founder's laptop. The assistant only answers when the laptop is on and a tunnel is up, and
every free tunnel has a dealbreaker for a browser-loaded widget (cloudflared = ephemeral
URL churn; ngrok-free = an interstitial Shopify won't bypass). Hosting gives a stable URL,
24/7 availability, and ends the tunnel firefighting.

## Key facts (verified)

- Engine is FastAPI; `Dockerfile` already host-ready (`$PORT`, installs `.[api]`, no Voyage
  chain on the serve path).
- Config is pydantic `BaseSettings` (`src/astor/config.py`, `env_file=".env"`). Relevant env:
  `DATABASE_URL` (default `postgresql+psycopg://astor:astor@localhost:5432/astor`),
  `ANTHROPIC_API_KEY`, `SHOPIFY_APP_PROXY_SECRET`, `SHOPIFY_CLIENT_SECRET`.
- DB engine: `create_engine(settings.database_url, pool_pre_ping=True)` — uses the
  **`postgresql+psycopg://`** (psycopg3) driver. Render injects a plain `postgres://…` URL,
  which SQLAlchemy will not accept as-is → needs normalization.
- DB: 342 MB — `products` 16,016 rows / 247 MB (embeddings), `equivalences` 314k / 76 MB,
  `protocols` 862, `protocol_material_links` 827, `supplier_offers` 15,988,
  `sourcing_requests`. Extensions: `vector`, `pgcrypto`, `plpgsql`.
- Alembic history is diverged (`alembic_version` = phantom `0002_pack_size_text`); the live
  schema was materialized via `Base.metadata.create_all`. A `pg_dump` captures the real
  schema, so the restore reproduces it exactly — alembic is not on the critical path here.
- Chat routers: `/api/chat` + `/api/chat/stream` (`routers/chat.py`, the unsigned demo path,
  now write-capable via `flag_sourcing_request`); `/proxy/chat` (`routers/shopify_proxy.py`,
  App-Proxy-signature gated); `/api/sourcing-requests` (`routers/dashboard.py`, currently
  unauthenticated, returns customer emails).

## Design

### 1. Infrastructure — Render Blueprint (`render.yaml`, new, committed)

A declarative Blueprint provisions both services from one "New Blueprint" connect:
- **Web service:** `env: docker`, `dockerfilePath: ./Dockerfile`, `plan: starter` (always-on,
  ~$7/mo), `healthCheckPath: /healthz`, `autoDeploy: true` on `main`, `region` chosen near
  the store's customers.
- **Database:** a Render Postgres entry, small plan (~$7/mo). `DATABASE_URL` injected into the
  web service via `fromDatabase` (Render's key is `connectionString`).
- Secrets are NOT in `render.yaml` (it's committed) — declared as `sync: false` env vars set
  in the Render dashboard: `ANTHROPIC_API_KEY`, `SHOPIFY_APP_PROXY_SECRET`,
  `SHOPIFY_CLIENT_SECRET`, `ENABLE_DEMO_CHAT=false`, `ADMIN_TOKEN=<random>`.

### 2. Database migration — dump/restore (keep embeddings)

Runbook steps (one-time):
1. On Render Postgres (via `psql "$RENDER_DATABASE_URL"`): `CREATE EXTENSION IF NOT EXISTS
   vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;`
2. `pg_dump --no-owner --no-acl -Fc` the local DB → a dump file.
3. `pg_restore --no-owner --no-acl -d "$RENDER_DATABASE_URL"` the dump. Brings schema + all
   data + embeddings — **no re-embed, no Voyage cost.**
4. Verify row counts match (products 16,016, protocols 862, links 827, supplier_offers
   15,988) and a pgvector ANN query returns results.

Render Postgres becomes the **single source of truth**; the offline pipeline, when run from
the laptop, points at Render's `DATABASE_URL` to write new data.

### 3. Config + code changes

**`src/astor/config.py`:**
- **DATABASE_URL normalizer:** coerce a `postgres://…` or `postgresql://…` value (no
  `+driver`) to `postgresql+psycopg://…` (a validator/`__post_init__`-style normalization on
  the `database_url` field). Local `.env` value already has `+psycopg` and is left unchanged.
- New settings: `enable_demo_chat: bool = True`; `admin_token: str | None = None`;
  `proxy_chat_rate_per_min: int = 20`.

**`GET /healthz`** (a new tiny router or added to `main`/`dashboard`): returns `{"ok": True}`
with **no DB access**, for Render's health check.

### 4. Minimal safeguard (code — TDD)

- **Demo-chat lockdown:** in `create_app`, register the `chat` router (`/api/chat`,
  `/api/chat/stream`) only when `settings.enable_demo_chat` is true; when false the routes are
  absent → `404`. `/proxy/chat` (the storefront path) is unaffected. Local dev keeps the
  default `True`; Render sets `ENABLE_DEMO_CHAT=false`.
- **Per-shop rate cap on `/proxy/chat`:** a small in-memory sliding-window limiter keyed by
  the verified `shop` (from `verify_app_proxy`); over `proxy_chat_rate_per_min` requests in
  the trailing 60 s → `HTTPException(429)`. Single Render instance at this scale → in-memory
  is sufficient; a missing `shop` falls back to a shared bucket. Lives in a small module
  (`src/astor/api/ratelimit.py`) so it's unit-testable with an injected clock.
- **Admin-token gate on `/api/sourcing-requests`:** when `settings.admin_token` is set, the
  endpoint requires a matching `X-Admin-Token` header (else `401`); when unset (local dev),
  it's open as today. Prevents world-readable customer emails in prod.

### 5. Wire Shopify (one final time)

App Proxy **Proxy URL** → `https://<service>.onrender.com/proxy` (prefix `apps`, subpath
`astor`), Release. Permanent — no tunnel, no interstitial, no churn. The theme snippet can
revert to the simple `<script src="/apps/astor/widget.js" defer>` loader (Render serves JS
with no interstitial).

### 6. Deploy flow (ongoing)

`git push` to `main` → Render rebuilds the Dockerfile → deploys. Health check gates rollout.

### 7. Error handling

- Bad/missing `DATABASE_URL` driver → normalized before `create_engine`; a truly missing URL
  keeps the local default (fails fast on connect, logged).
- Rate-limit trip → `429` with a friendly detail; the widget already shows its generic retry
  line on any non-200.
- Missing `ADMIN_TOKEN` in prod → the sourcing endpoint 401s (fail-closed) rather than
  leaking; a deploy without the env var is a visible operator error, not a silent leak.
- Health check failing → Render holds the old deploy; no bad version goes live.

### 8. Testing

- **Offline (TDD):** URL normalizer (`postgres://`→`postgresql+psycopg://`, and a value that
  already has `+psycopg` is untouched); `enable_demo_chat=false` → `/api/chat` 404, and the
  read/proxy routes still present; rate limiter (injected clock: N ok then 429, window
  resets); `/api/sourcing-requests` → 401 without token / 200 with, and open when
  `admin_token` unset; `/healthz` → 200 with no DB.
- **Manual verification checklist (runbook):** post-restore row counts + an ANN query;
  signed `/proxy/ping` (401) and `/proxy/chat` (real reply) against the `onrender.com` URL;
  storefront widget loads and answers; `ENABLE_DEMO_CHAT=false` verified (public `/api/chat`
  → 404).

### 9. Non-goals

- Full rate-limiting / signed-`timestamp` freshness / request-size caps (a later hardening
  pass builds on the basic per-shop cap here).
- A custom `astorscientific.us` subdomain (use Render's URL; no DNS work now).
- Fixing the diverged alembic history / a proper migration baseline (the dump/restore carries
  the schema; revisit if/when schema churn resumes).
- CI/CD beyond Render's built-in GitHub auto-deploy; Move 2-B; Move 3.
