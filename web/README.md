# AstorScientific Web (M1 Ops UI)

Next.js 15 frontend for the M1 ops-first UI. Talks to the FastAPI layer in `src/astor/api`.

## Run it locally

1. **Start Postgres + pgvector** (repo root):
   ```bash
   docker compose up -d
   ```
2. **Apply the schema** (repo root, with the Python env active):
   ```bash
   alembic upgrade head
   ```
3. **Start the API with demo data**:
   ```bash
   SEED_DEMO=1 uvicorn astor.api.main:app --reload --port 8000
   ```
4. **Start the web app** (`web/`):
   ```bash
   npm install
   NEXT_PUBLIC_DEMO=1 npm run dev
   ```
5. Open http://localhost:3000.

## Notes
- The role toggle (Ops/Buyer) lives in the sidebar. M1 builds Ops views; the API
  strips origin/supplier/brand for the Buyer role server-side.
- Demo equivalence scores use the offline DevEmbedder and are not meaningful.
  Set `EMBEDDINGS_PROVIDER=voyage` + a key for real matches.
