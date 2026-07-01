# AstorScientific Web App — Milestone 1 (Ops-first Frontend) — Design

**Date:** 2026-06-28
**Status:** Approved (pending spec review)
**Scope:** A functional product UI over the existing M1/M2 backend (catalog ingestion + China↔US equivalence matching + landed cost), plus a thin HTTP API layer. Ops-first, unified shell with a role toggle.

---

## 1. Goals & non-goals

### Goals
- Stand up a **functional product UI** (not just a mockup) over the existing Python backend.
- Build a **unified app shell** with a role concept (Ops / Buyer), implementing the **Ops** sections first because they map to features the backend already has.
- Ship three screens: **Dashboard**, **Catalog Ingest + Browse**, **Product Detail + Landed Cost**.
- Add a **FastAPI** HTTP layer over the existing SQLAlchemy models *without changing the models*.
- Establish, from day one, the rule that **buyers never see product origin (China/US), the source supplier's identity/brand, or the manufacturer MPN** — buyers transact against an **Astor-assigned SKU**. The API enforces this server-side by role.

### Non-goals (deferred to later milestones)
- Authentication / login / real user accounts.
- The Buyer-role screens (the shell supports the toggle and the API supports the role, but only Ops screens are built in M1).
- Order placement / PO workflow (transaction spine stays dormant).
- The equivalence **review** queue (approve/reject UI). Equivalences are shown **read-only** in M1.
- A persisted `astor_sku` column (derived deterministically in M1; persisted column is a later milestone).
- Real HS-code duty classification (landed cost keeps the M1 placeholder duty table).

---

## 2. Visual & UX direction

- **Design language:** "Modern Marketplace" — dark theme, fintech energy (Ramp/Stripe reference), gradient accents (teal `#5eead4` → blue `#3b82f6`), savings/value surfaced prominently.
- **Shell:** persistent **left sidebar** with grouped nav (Workspace sections + a Role group). Chosen over top-nav because more sections are coming (review queue, suppliers, orders) and dense data screens benefit from full vertical height.
- **Primary identifier everywhere:** the **Astor SKU** (e.g. `ASR-7F3A21`). Manufacturer brand/MPN and supplier region are secondary, internal-only detail.

---

## 3. Architecture & repo layout

Monorepo addition to the existing Python project. The frontend never talks to Postgres directly; it goes through the FastAPI layer. The API layer is read-mostly, stateless, and **reuses existing backend functions** (`ingestion.ingest`, `landed_cost`, `matcher`) rather than reimplementing logic.

```
astorAI/
├── src/astor/                      # existing — only new addition is api/
│   └── api/                        # NEW: FastAPI app over existing models
│       ├── __init__.py
│       ├── main.py                 # app factory, CORS, router mounting
│       ├── deps.py                 # DB session dependency (reuses db/base.session_scope)
│       ├── roles.py                # Role enum (ops|buyer) + field-gating helpers
│       ├── skus.py                 # derive Astor SKU from product UUID (ASR-XXXXXX)
│       ├── schemas.py              # Pydantic API DTOs (wire contract ≠ DB models)
│       └── routers/
│           ├── dashboard.py        # GET /api/stats
│           ├── catalog.py          # POST /api/ingest, GET /api/products, GET /api/products/{id}
│           └── pricing.py          # GET /api/products/{id}/landed-cost
│
├── web/                            # NEW: Next.js 15 (App Router, TypeScript, Tailwind)
│   ├── app/
│   │   ├── (shell)/layout.tsx          # sidebar shell, dark theme, role toggle
│   │   ├── (shell)/page.tsx            # Dashboard
│   │   ├── (shell)/ingest/page.tsx     # Catalog ingest + browse
│   │   └── (shell)/products/[id]/page.tsx  # Product detail + landed cost
│   ├── components/                 # KpiCard, DataTable, Sidebar, LandedCostWaterfall, …
│   ├── lib/api.ts                  # typed fetch client (reads NEXT_PUBLIC_API_URL)
│   └── lib/types.ts                # TS types mirroring api/schemas.py
│
└── docs/superpowers/specs/         # this document
```

**API DTO separation:** API response models live in `api/schemas.py`, separate from DB models, so the wire contract is stable and so role-based field gating is explicit (a buyer DTO literally has no `region`/`brand`/`mpn` fields).

---

## 4. The role rule (origin confidentiality) — central invariant

This is a hard product invariant, enforced **server-side**, never by client-side hiding.

| Field | Ops view | Buyer view |
|---|---|---|
| Astor SKU | shown | shown (primary identifier) |
| Product name (Astor canonical) | shown | shown |
| Astor specs | shown | shown |
| Landed price / breakdown | shown (full breakdown incl. ex-works, tariff, margin) | shown (price only; internal margin/ex-works hidden) |
| Region / country of origin (CN/US) | shown | **never** |
| Supplier identity | shown | **never** |
| Manufacturer brand | shown | **never** |
| Manufacturer MPN | shown | **never** |
| Equivalences | cross-region matches w/ brand + confidence | reframed as origin-agnostic "alternatives" (deferred build) |

Implementation: each router takes a `role` (M1: from a query param / header, default `ops`; later: from auth). `api/roles.py` provides a gating function that builds the correct DTO. Buyer DTOs omit the confidential fields **structurally** — they are not present in the serialized object at all.

**M1 reality:** every screen we build is the **Ops** view, so all of these fields are shown. The buyer gating is built and unit-tested now so the contract can't regress, even though no buyer screen ships in M1.

---

## 5. Astor SKU

- **M1:** derived deterministically from the product UUID — `ASR-` + a short uppercase base32/hex slug of the UUID (stable, collision-checked within the dataset). Lives in `api/skus.py`. No schema change.
- **Later:** a persisted, human-curated `astor_sku` column on `Product`. The derivation function is the single source of truth until then, so swapping to a column is a one-line change in the DTO builder.

---

## 6. Screens

### 6.1 Dashboard (`/`)
- Hero strip with the internal Ops framing: *"China↔US sourcing, priced end to end."* (Ops-only language; the future buyer dashboard reframes as origin-agnostic "best price, guaranteed availability.")
- KPI cards from `GET /api/stats`: total **products**, **offers**, **equivalences** (split exact / substitute), **suppliers**, headline **avg landed-cost savings vs US list**.
- "Recent equivalences" preview table: product → matched product, confidence bar, kind badge. (Ops view shows brands; this table is Ops-only.)
- Empty-state aware: empty DB → zeroed cards + CTA to Ingest.

### 6.2 Catalog Ingest + Browse (`/ingest`)
- **Ingest panel:** supplier name, region (CN/US), tier (public/authorized/deep), file picker (CSV/XLSX). Submit → `POST /api/ingest` (multipart). Shows the returned `IngestResult` summary (`extracted / products_upserted / offers_upserted`). A "run matcher" toggle triggers equivalence matching on the new products.
- **Products table:** searchable + paginated via `GET /api/products?q=&category=&page=&page_size=`. Columns: Astor SKU, name, category, # offers, best landed price. (Ops view also shows brand/MPN/region columns.) Row click → product detail.

### 6.3 Product Detail + Landed Cost (`/products/[id]`)
- **Header:** Astor SKU (primary), Astor canonical name, category, specs as key/value chips. (Ops also shows brand/MPN.)
- **Supplier offers** table (Ops-only detail): supplier, region, SKU, pack size, cost + currency, stock, lead time.
- **Landed-cost breakdown** — visual waterfall from the `landed_cost()` dict: ex-works → tariff (label shows duty rate) → freight → margin → **unit price**; a qty input re-fetches `line_total`. This is the centerpiece. (Buyer view would show only the final unit price.)
- **Equivalents** panel: matched products with confidence + kind, each linking to its own detail page. (Ops view; shows brands.)

---

## 7. API endpoints

All under `/api`. `role` defaults to `ops` in M1.

| Method | Endpoint | Backend it calls | Returns |
|---|---|---|---|
| `GET` | `/api/stats` | aggregate queries over models | counts (products, offers, equivalences split by kind, suppliers) + avg savings |
| `POST` | `/api/ingest` (multipart: file, supplier, region, tier, run_match) | saves upload to a temp path → `ingestion.ingest()` → optional `matcher` | `IngestResult` summary |
| `GET` | `/api/products?q=&category=&page=&page_size=&role=` | `Product` + offer-count subquery | paginated product list (DTO gated by role) |
| `GET` | `/api/products/{id}?role=` | `Product`, `SupplierOffer`, `Equivalence` | product + offers + equivalents (gated by role) |
| `GET` | `/api/products/{id}/landed-cost?qty=&role=` | best (or per-) offer → `landed_cost()` | breakdown dict (margin/ex-works stripped for buyer) |

**Error contract:** structured JSON `{ "detail": str, "code": str }`. Ingest validation errors (bad file type, missing required columns) return 422 with actionable messages. Frontend renders inline error states + a toast.

**Data flow example (product detail):** Next.js Server Component → `lib/api.ts` fetch → FastAPI router → `session_scope` → SQLAlchemy query / `landed_cost()` → role-gated Pydantic DTO → JSON → server-rendered.

---

## 8. Dev mode & data

- Real path: PostgreSQL + pgvector (per existing `.env` / `alembic` setup), `EMBEDDINGS_PROVIDER=dev` works offline.
- **`SEED_DEMO=1` mode:** a startup helper ingests the sample data (`data/sample_supplier_cn.csv` + the eval set) using the offline `DevEmbedder`, so all three screens are clickable locally **without API keys**. Switching to real Postgres + a real embedder is a config flip, no code change.
- The `DevEmbedder` caveat (non-semantic) is surfaced in the UI as a small "demo data" banner so nobody mistakes demo numbers for real matches.

---

## 9. Error handling

- API: structured errors (§7). 404 for unknown product id. 422 for bad ingest input. 500s logged, generic message to client.
- Frontend: per-screen error boundaries; inline empty/error/loading states for tables; ingest form shows row-level/field-level validation feedback.

---

## 10. Testing

- **API:** `pytest` with FastAPI `TestClient` against a seeded/in-memory dataset — endpoint happy paths + the **role-gating tests** (assert buyer DTOs structurally omit region/brand/mpn/supplier and internal cost fields). These role tests are the most important: they lock the origin-confidentiality invariant.
- **Frontend:** a focused component test for the `LandedCostWaterfall` (the breakdown math/display) and the Astor-SKU rendering.
- **Existing tests untouched:** the M1/M2 pure-logic and eval tests stay green. We add, we don't modify.

---

## 11. Build sequence (for the implementation plan)

1. **API skeleton:** `api/main.py`, `deps.py`, `skus.py`, `roles.py`, `schemas.py`; wire `session_scope`; `GET /api/stats`.
2. **Catalog API:** `GET /api/products`, `GET /api/products/{id}` with role gating + Astor SKU.
3. **Pricing API:** `GET /api/products/{id}/landed-cost`.
4. **Ingest API:** `POST /api/ingest` (multipart) + optional matcher.
5. **Role-gating tests** (lock the invariant early).
6. **Next.js shell:** sidebar layout, dark theme, role toggle, `lib/api.ts`.
7. **Dashboard** screen.
8. **Ingest + Browse** screen.
9. **Product Detail + Landed Cost** screen.
10. **SEED_DEMO** path + demo banner.

---

## 12. Open items / future milestones
- Persisted `astor_sku` column + curation workflow.
- Buyer-role screens (origin-agnostic catalog, alternatives, cart/PO).
- Equivalence review queue (approve/reject, the human-in-the-loop).
- Real HS-code duty classification replacing the placeholder table.
- Auth / tenancy enforcement on the API.
