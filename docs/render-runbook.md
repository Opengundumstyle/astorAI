# Render hosting runbook

Operational reference for the Astor engine on Render. Implements sub-project #3
(`docs/superpowers/specs/2026-08-20-render-hosting-design.md`); the code it depends on
landed in `d8129a6`.

**What this replaces:** the engine, Postgres, and the public tunnel all running on the
founder's laptop. After cutover the storefront assistant answers 24/7 at a permanent URL.

**Cost:** ~$14/mo — `starter` web (~$7, always-on; `free` sleeps and would cold-start real
shoppers) + `basic-256mb` Postgres (~$7).

---

## Measured facts (captured 2026-08-21, from the live local DB)

Use these for post-restore verification, **not** the numbers in the design doc — the
pipeline has run since that was written and they have drifted.

| Table | Rows |
|---|---|
| `products` | 16,019 (all 16,019 have embeddings) |
| `equivalences` | 314,184 |
| `supplier_offers` | 15,991 |
| `protocols` | 862 |
| `protocol_material_links` | 827 |
| `sourcing_requests` | 1 |

Dump size: **95 MB**. (`pg_dump -Fc` compresses by default — the design doc's
"250–350 MB" estimate assumed uncompressed and is wrong.)

Extensions in the dump: `vector`, `pgcrypto`.

---

## Before you start — three things this runbook depends on

- **The Render database is internal-only** (`ipAllowList: []` in `render.yaml`). The
  one-time `pg_restore` and any laptop-run pipeline require temporarily adding your IP on
  the database's **Access Control** page, and removing it afterwards. An
  internet-reachable database is a visible, temporary act — never the committed default.
- **`POST /api/ingest` against the hosted engine writes junk embeddings** unless
  `EMBEDDINGS_PROVIDER` and its matching key are configured there. A configured real
  provider with a missing key now raises rather than silently degrading to `DevEmbedder`;
  that failure is intended behavior, not a bug to work around.
- **`/docs`, `/redoc`, `/openapi.json` are disabled on Render** (`ADMIN_TOKEN_REQUIRED=true`
  marks it a public host). Read the schema against a local instance instead.

---

## Step 1 — Push (DONE)

`main` is pushed to `github.com/Opengundumstyle/astorAI` at `d8129a6`. Render reads
`render.yaml` from that repo.

## Step 2 — Create the Render account and connect the Blueprint

1. Sign up at <https://render.com> and connect the GitHub account that owns the repo.
2. Add a payment method — `starter` and `basic-256mb` are paid plans, not free tier.
3. **New → Blueprint** → select `Opengundumstyle/astorAI` → Render parses `render.yaml`
   and shows two resources: `astor-engine` (web) and `astor-db` (Postgres).
4. It will prompt for the four `sync: false` secrets. Values:

   | Key | Where to get it |
   |---|---|
   | `ANTHROPIC_API_KEY` | local `.env` line 12 |
   | `SHOPIFY_CLIENT_SECRET` | local `.env` line 19 |
   | `SHOPIFY_APP_PROXY_SECRET` | local `.env` line 40 |
   | `ADMIN_TOKEN` | freshly generated — see the scratchpad file named in the session, then move it to your password manager |

   `ADMIN_TOKEN_REQUIRED=true` and `ENABLE_DEMO_CHAT=false` come from the blueprint
   itself; do not set them by hand.
5. **Apply.**

> The first deploy will fail its health check until the database finishes provisioning.
> That is expected and self-corrects.

> If Render's validator rejects `postgresMajorVersion`, delete that one line from
> `render.yaml`, push, and set the version in the dashboard instead. `pg_restore` from
> 16 into a later major works for this schema.

## Step 3 — Allow your IP, temporarily

On `astor-db` → **Access Control** → add your current IP as a `/32`. Note that you are
doing this so you remember to undo it in Step 8.

## Step 4 — Enable the extensions

Copy the **External Database URL** from the `astor-db` page.

```bash
export RENDER_DATABASE_URL='<paste the External Database URL>'
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c \
  'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;'
```

Extensions must exist **before** the restore — the dump's column types reference `vector`.

## Step 5 — Restore

The dump already exists at `/private/tmp/astor-d8129a6.dump` (regenerate with the `pg_dump`
line at the bottom of this file if it is stale).

```bash
docker exec -i astorai-db-1 pg_restore --no-owner --no-acl --no-comments \
  -d "$RENDER_DATABASE_URL" < /private/tmp/astor-d8129a6.dump
```

`pg_dump`/`pg_restore` run **inside** `astorai-db-1` so the client version always matches
the pg16 source and nothing needs installing on the host.

Errors mentioning `extension "vector" already exists` or `must be owner of extension` are
**benign** — you pre-created them in Step 4. Any error naming a *table* or *row* is not;
stop and investigate.

## Step 6 — Verify the data

```bash
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c "
  SELECT 'products' t, count(*) FROM products
  UNION ALL SELECT 'equivalences', count(*) FROM equivalences
  UNION ALL SELECT 'protocols', count(*) FROM protocols
  UNION ALL SELECT 'protocol_material_links', count(*) FROM protocol_material_links
  UNION ALL SELECT 'supplier_offers', count(*) FROM supplier_offers
  UNION ALL SELECT 'sourcing_requests', count(*) FROM sourcing_requests;"
```

Must match the measured table above.

Prove the embeddings survived. The HNSW index uses `vector_cosine_ops`, so the query must
use `<=>` — a `<->` (L2) query would return rows while silently bypassing the index:

```bash
docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c "
  SELECT id, name FROM products
  WHERE embedding IS NOT NULL
  ORDER BY embedding <=> (SELECT embedding FROM products WHERE embedding IS NOT NULL LIMIT 1)
  LIMIT 5;"

docker exec -i astorai-db-1 psql "$RENDER_DATABASE_URL" -c \
  "SELECT indexname FROM pg_indexes WHERE tablename = 'products';"
```

Expect 5 rows (the first being the reference product, distance 0) and
`ix_product_embedding_hnsw` in the index list.

## Step 7 — Verify the running service

Redeploy `astor-engine` from the dashboard so it picks up the now-populated database.

```bash
BASE=https://astor-engine.onrender.com
curl -s $BASE/healthz                                                         # {"ok":true}
curl -s -o /dev/null -w 'demo chat: %{http_code}\n'  -X POST $BASE/api/chat    # 404
curl -s -o /dev/null -w 'api unauthed: %{http_code}\n' $BASE/api/stats         # 401
curl -s -o /dev/null -w 'api authed: %{http_code}\n' \
  -H "X-Admin-Token: $ADMIN_TOKEN" $BASE/api/stats                             # 200
curl -s -o /dev/null -w 'docs hidden: %{http_code}\n' $BASE/openapi.json       # 404
curl -s -o /dev/null -w 'proxy unsigned: %{http_code}\n' \
  "$BASE/proxy/ping?shop=astor-dev.myshopify.com"                              # 401
```

**All six must match.** A `200` on `/api/stats` without the header means the gate is not
live — stop and fix before repointing Shopify.

## Step 8 — Remove the IP allowance

Back to `astor-db` → **Access Control** → delete the `/32` you added in Step 3.

Re-add it temporarily whenever you run the offline pipeline from the laptop:

```bash
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.ingest_shopify
DATABASE_URL="$RENDER_DATABASE_URL" python -m scripts.match_materials --dry-run
```

Run `--dry-run` first on anything that writes — these now hit production data.

## Step 9 — Repoint Shopify

Dev Dashboard → the Astor app → **App proxy**:

- Subpath prefix `apps`, subpath `astor`
- **Proxy URL:** `https://astor-engine.onrender.com/proxy`
- Save, then **Release** the version.

Load a storefront page with the widget and send one message. Because Render serves the JS
with no interstitial, the theme snippet can revert to the plain loader:

```liquid
<script src="/apps/astor/widget.js" defer></script>
```

## Step 10 — Retire the laptop tunnel

**Only after Step 9 answers correctly:**

```bash
pkill -f 'ngrok http 8000'
pkill -f 'cloudflared tunnel'     # leftover from the pre-ngrok setup
```

Render's Postgres is now the source of truth. The local uvicorn and `astorai-db-1` can keep
running for development.

---

## Ongoing operations

**Deploys:** `git push origin main` → Render rebuilds the Dockerfile → the `/healthz` check
gates the rollout. A failing check holds the previous deploy.

**Rotating `ADMIN_TOKEN`:** update it in the `astor-engine` environment, save, let it
redeploy. Anything using the old token (currently nothing automated) breaks until updated.

**Regenerating the dump:**

```bash
docker exec astorai-db-1 pg_dump -U astor -d astor --no-owner --no-acl -Fc \
  > /private/tmp/astor-$(git rev-parse --short HEAD).dump
```

## Known gaps (tracked, not fixed)

- The `/api/*` gate runs **after** FastAPI parses the request body, so an anonymous
  `POST /api/ingest` has its upload spooled before the 401. Free I/O on a $7 instance.
  Fix is a thin `/api/`-prefix middleware, keeping the router-level dependency and its
  coverage meta-test as defence in depth.
- The `/proxy/chat` cap keys on `shop`, and there is exactly one shop — so it is a
  per-**store** cap. One visitor sending 20 messages in a minute serves `429` to every
  other shopper for the rest of that window, and 20/min sustained is ~28,800 turns/day,
  which is not a meaningful bill ceiling. Fix is keying on
  `f"{shop}:{customer_id or 'anon'}"` with the shop bucket as an outer limit, plus a daily
  counter.
- The `web/` Next.js dashboard sends no `X-Admin-Token`, so it 401s against Render. It
  still works unchanged against a local API.
- No dependency lockfile, and `fastapi>=0.110`, so every Render rebuild re-resolves the
  tree. `/healthz` catches a hard breakage; a subtle behavioral change would ship silently.
