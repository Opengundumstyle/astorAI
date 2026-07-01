# M1 Ops-First Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a functional ops-first web UI (Dashboard, Catalog Ingest+Browse, Product Detail+Landed Cost) over the existing catalog/matching/landed-cost backend, served by a new FastAPI layer, with origin-confidentiality enforced server-side by role.

**Architecture:** A new `src/astor/api/` FastAPI package wraps the existing SQLAlchemy models and functions (`ingestion.ingest`, `matcher.match_product`, `landed_cost`) — no model changes. Pure, DB-free helpers (Astor SKU derivation, role-based field gating, DTO builders) are unit-tested directly; data access is isolated in a thin `repo` layer so routers can be tested by monkeypatching the repo (no Postgres needed in CI). A separate `web/` Next.js 15 app (App Router, TypeScript, Tailwind, dark "marketplace" theme) consumes the API through a typed fetch client.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy 2.0, pgvector/Postgres, pytest + FastAPI TestClient; Next.js 15, React, TypeScript, TailwindCSS, Vitest + React Testing Library.

## Global Constraints

- Python `>=3.11`. Do not modify existing files under `src/astor/` except to add the new `api/` package and (in the dev-setup task) optional dev files. Existing tests must stay green.
- **No changes to `src/astor/db/models.py`.** The Astor SKU is *derived*, not stored, in M1.
- **Origin-confidentiality invariant (load-bearing):** for `role="buyer"`, API responses MUST structurally omit these keys: `region`, `supplier`, `brand`, `mpn`, the entire `offers` list, and the landed-cost internals `ex_works`, `tariff`, `duty_rate`, `freight`, `margin`. `role` defaults to `"ops"` (full detail). Gating happens server-side; never rely on the client to hide fields.
- **Astor SKU format:** `ASR-` + the first 6 hex characters of the product UUID, uppercased. Example: UUID `7f3a21b4-...` → `ASR-7F3A21`. This is the primary identifier shown on every screen.
- API base path is `/api`. Frontend reads the base URL from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
- Landed-cost numbers use the existing `landed_cost()` placeholder duty table — do not reimplement pricing math.
- Commit after every task with the message shown in its final step.

---

## File Structure

**Backend (`src/astor/api/`)**
- `__init__.py` — package marker.
- `skus.py` — `astor_sku(product_id) -> str`. Pure.
- `roles.py` — role constants + `gate_*` functions that strip confidential keys. Pure.
- `schemas.py` — DTO **builder functions** (ORM object → plain dict). Pure.
- `repo.py` — thin data-access functions over a `Session` (the only DB-touching module).
- `deps.py` — `get_session` FastAPI dependency.
- `seed.py` — `seed_demo(session)` for the `SEED_DEMO=1` path.
- `main.py` — `create_app()`, CORS, router mounting, optional startup seed.
- `routers/dashboard.py` — `GET /api/stats`.
- `routers/catalog.py` — `GET /api/products`, `GET /api/products/{id}`, `POST /api/ingest`.
- `routers/pricing.py` — `GET /api/products/{id}/landed-cost`.

**Backend tests (`tests/api/`)**
- `test_skus.py`, `test_roles.py`, `test_schemas.py` — pure unit tests.
- `test_dashboard.py`, `test_catalog.py`, `test_pricing.py` — router tests via monkeypatched repo + `TestClient`.

**Frontend (`web/`)**
- `lib/types.ts`, `lib/api.ts` — types + fetch client.
- `app/(shell)/layout.tsx` — sidebar shell + role toggle.
- `app/(shell)/page.tsx` — Dashboard.
- `app/(shell)/ingest/page.tsx` — Ingest + Browse.
- `app/(shell)/products/[id]/page.tsx` — Product Detail + Landed Cost.
- `components/` — `Sidebar.tsx`, `KpiCard.tsx`, `RecentEquivalences.tsx`, `ProductsTable.tsx`, `IngestForm.tsx`, `OffersTable.tsx`, `LandedCostWaterfall.tsx`, `EquivalentsPanel.tsx`, `DemoBanner.tsx`, `ConfidenceBar.tsx`, `KindBadge.tsx`.
- `components/__tests__/LandedCostWaterfall.test.tsx` — required component test.

---

## Task 1: FastAPI skeleton + health endpoint

**Files:**
- Modify: `pyproject.toml` (add `api` optional-dependency group)
- Create: `src/astor/api/__init__.py`
- Create: `src/astor/api/deps.py`
- Create: `src/astor/api/main.py`
- Create: `tests/api/__init__.py`
- Test: `tests/api/test_health.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`; dependency `get_session()` (generator yielding a `Session`); route `GET /api/health -> {"status": "ok"}`.

- [ ] **Step 1: Add the `api` dependency group**

In `pyproject.toml`, under `[project.optional-dependencies]`, add this line after the `openai` entry:

```toml
api = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "httpx>=0.27", "python-multipart>=0.0.9"]
```

(`httpx` is needed by FastAPI's `TestClient`; `python-multipart` by file-upload endpoints.)

- [ ] **Step 2: Install the new deps**

Run: `pip install -e '.[dev,api]'`
Expected: installs fastapi, uvicorn, httpx, python-multipart with no errors.

- [ ] **Step 3: Create the package marker and session dependency**

Create `src/astor/api/__init__.py`:

```python
"""FastAPI HTTP layer over the existing catalog/matching/pricing backend."""
```

Create `src/astor/api/deps.py`:

```python
"""FastAPI dependencies."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from astor.db.base import session_scope


def get_session() -> Iterator[Session]:
    """Yield a transactional session per request (reuses db.base.session_scope)."""
    with session_scope() as session:
        yield session
```

- [ ] **Step 4: Create the app factory**

Create `src/astor/api/main.py`:

```python
"""Application factory: CORS, routers, optional demo seed on startup."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="AstorScientific API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 5: Write the failing test**

Create `tests/api/__init__.py` (empty file).

Create `tests/api/test_health.py`:

```python
from fastapi.testclient import TestClient

from astor.api.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 6: Run the test**

Run: `pytest tests/api/test_health.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/astor/api tests/api
git commit -m "feat(api): FastAPI skeleton with health endpoint"
```

---

## Task 2: Astor SKU derivation (pure)

**Files:**
- Create: `src/astor/api/skus.py`
- Test: `tests/api/test_skus.py`

**Interfaces:**
- Produces: `astor_sku(product_id: str | uuid.UUID) -> str` returning `ASR-XXXXXX` (6 uppercase hex chars).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_skus.py`:

```python
import uuid

from astor.api.skus import astor_sku


def test_astor_sku_from_uuid_object():
    pid = uuid.UUID("7f3a21b4-0000-0000-0000-000000000000")
    assert astor_sku(pid) == "ASR-7F3A21"


def test_astor_sku_from_string_is_stable():
    s = "7f3a21b4-0000-0000-0000-000000000000"
    assert astor_sku(s) == "ASR-7F3A21"
    assert astor_sku(s) == astor_sku(s)


def test_astor_sku_uppercases_hex():
    pid = uuid.UUID("abcdef12-0000-0000-0000-000000000000")
    assert astor_sku(pid) == "ASR-ABCDEF"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_skus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'astor.api.skus'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/astor/api/skus.py`:

```python
"""Buyer-facing Astor SKU, derived from the product UUID (M1: not persisted)."""
from __future__ import annotations

import uuid


def astor_sku(product_id: str | uuid.UUID) -> str:
    """`ASR-` + first 6 hex chars of the UUID, uppercased. e.g. ASR-7F3A21."""
    hexed = uuid.UUID(str(product_id)).hex
    return f"ASR-{hexed[:6].upper()}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_skus.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/skus.py tests/api/test_skus.py
git commit -m "feat(api): derive Astor SKU from product UUID"
```

---

## Task 3: Role-based field gating (pure)

**Files:**
- Create: `src/astor/api/roles.py`
- Test: `tests/api/test_roles.py`

**Interfaces:**
- Produces:
  - `OPS = "ops"`, `BUYER = "buyer"`, `normalize_role(value: str | None) -> str` (unknown/None → `"ops"`).
  - `gate_product(d: dict, role: str) -> dict` — buyer view drops `brand`, `mpn`, `region`, `offers`.
  - `gate_detail(d: dict, role: str) -> dict` — buyer view drops `brand`, `mpn`, `offers`, and strips `brand`/`region`/`supplier` from each equivalent.
  - `gate_landed(d: dict, role: str) -> dict` — buyer view drops `ex_works`, `tariff`, `duty_rate`, `freight`, `margin`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_roles.py`:

```python
from astor.api.roles import (
    BUYER,
    OPS,
    gate_detail,
    gate_landed,
    gate_product,
    normalize_role,
)


def test_normalize_role_defaults_to_ops():
    assert normalize_role(None) == OPS
    assert normalize_role("") == OPS
    assert normalize_role("nonsense") == OPS
    assert normalize_role("buyer") == BUYER
    assert normalize_role("OPS") == OPS


def test_gate_product_ops_is_untouched():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
         "region": "CN", "offers": [1], "best_landed": 9.9}
    assert gate_product(d, OPS) == d


def test_gate_product_buyer_strips_origin_fields():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
         "region": "CN", "offers": [1], "best_landed": 9.9}
    out = gate_product(d, BUYER)
    assert out == {"astor_sku": "ASR-1", "name": "x", "best_landed": 9.9}
    for forbidden in ("brand", "mpn", "region", "offers"):
        assert forbidden not in out


def test_gate_detail_buyer_strips_offers_and_equivalent_origin():
    d = {
        "astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
        "category": "molecular_biology", "specs": {},
        "offers": [{"supplier": "S", "region": "CN", "cost": 1}],
        "equivalents": [
            {"astor_sku": "ASR-2", "name": "y", "brand": "NEB", "region": "US",
             "supplier": "NEB Inc", "confidence": 0.9, "kind": "substitute"}
        ],
    }
    out = gate_detail(d, BUYER)
    assert "offers" not in out
    assert "brand" not in out and "mpn" not in out
    eq = out["equivalents"][0]
    assert eq == {"astor_sku": "ASR-2", "name": "y", "confidence": 0.9, "kind": "substitute"}
    for forbidden in ("brand", "region", "supplier"):
        assert forbidden not in eq


def test_gate_landed_buyer_keeps_only_price():
    d = {"currency": "USD", "qty": 2, "ex_works": 16.8, "tariff": 4.2,
         "duty_rate": 0.25, "freight": 1.5, "margin": 4.5,
         "unit_price": 27.0, "line_total": 54.0}
    out = gate_landed(d, BUYER)
    assert out == {"currency": "USD", "qty": 2, "unit_price": 27.0, "line_total": 54.0}
    for forbidden in ("ex_works", "tariff", "duty_rate", "freight", "margin"):
        assert forbidden not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'astor.api.roles'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/astor/api/roles.py`:

```python
"""Server-side origin-confidentiality gating.

Buyers must never receive product origin, supplier identity, manufacturer
brand/MPN, or internal cost internals. Gating drops the keys structurally so
the confidential data never reaches the wire.
"""
from __future__ import annotations

OPS = "ops"
BUYER = "buyer"

_PRODUCT_CONFIDENTIAL = ("brand", "mpn", "region", "offers")
_DETAIL_CONFIDENTIAL = ("brand", "mpn", "offers")
_EQUIVALENT_CONFIDENTIAL = ("brand", "region", "supplier")
_LANDED_CONFIDENTIAL = ("ex_works", "tariff", "duty_rate", "freight", "margin")


def normalize_role(value: str | None) -> str:
    return BUYER if (value or "").strip().lower() == BUYER else OPS


def _drop(d: dict, keys: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


def gate_product(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    return _drop(d, _PRODUCT_CONFIDENTIAL)


def gate_detail(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    out = _drop(d, _DETAIL_CONFIDENTIAL)
    out["equivalents"] = [_drop(e, _EQUIVALENT_CONFIDENTIAL) for e in d.get("equivalents", [])]
    return out


def gate_landed(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    return _drop(d, _LANDED_CONFIDENTIAL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_roles.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/roles.py tests/api/test_roles.py
git commit -m "feat(api): role-based origin-confidentiality gating"
```

---

## Task 4: DTO builder functions (pure)

**Files:**
- Create: `src/astor/api/schemas.py`
- Test: `tests/api/test_schemas.py`

**Interfaces:**
- Produces builder functions (each returns a plain dict, OPS-shape — gating applies later):
  - `product_summary(product, offer_count: int, best_landed: float | None) -> dict` → keys: `id, astor_sku, name, category, brand, mpn, region, offer_count, best_landed`.
  - `offer_out(offer) -> dict` → keys: `supplier, region, supplier_sku, pack_size, cost, currency, stock, lead_time_days`. (Reads `offer.supplier.name` / `offer.supplier.region`.)
  - `equivalent_out(product, confidence: float, kind: str) -> dict` → keys: `id, astor_sku, name, brand, region, supplier, confidence, kind`. (`region`/`supplier` are best-effort: `None` when unknown.)
  - `product_detail(product, offers: list, equivalents: list[tuple]) -> dict` → keys: `id, astor_sku, name, category, brand, mpn, specs, offers, equivalents`. `equivalents` is a list of `(product, confidence, kind)` tuples.
  - `stats_out(products, offers, exact, substitute, suppliers, avg_savings) -> dict`.

The builders read these attributes off the passed objects: `product.id, .name, .category, .brand, .mpn, .specs`; `offer.supplier_sku, .pack_size, .cost, .currency, .stock, .lead_time_days, .supplier.name, .supplier.region`. Tests pass `types.SimpleNamespace` stand-ins, so no DB is required.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_schemas.py`:

```python
import uuid
from types import SimpleNamespace

from astor.api import schemas

PID = uuid.UUID("7f3a21b4-0000-0000-0000-000000000000")


def _product():
    return SimpleNamespace(
        id=PID, name="2x Taq Master Mix", category="molecular_biology",
        brand="Vazyme", mpn="P112", specs={"volume": "5 mL"},
    )


def _offer():
    return SimpleNamespace(
        supplier_sku="VZ-EX-001", pack_size="5 mL", cost=120, currency="CNY",
        stock=200, lead_time_days=7,
        supplier=SimpleNamespace(name="Sample CN", region="CN"),
    )


def test_product_summary_has_astor_sku_and_no_db_needed():
    d = schemas.product_summary(_product(), offer_count=3, best_landed=24.8)
    assert d["astor_sku"] == "ASR-7F3A21"
    assert d["name"] == "2x Taq Master Mix"
    assert d["brand"] == "Vazyme"
    assert d["mpn"] == "P112"
    assert d["region"] is None  # summary has no single region; offers carry it
    assert d["offer_count"] == 3
    assert d["best_landed"] == 24.8


def test_offer_out_reads_supplier_identity():
    d = schemas.offer_out(_offer())
    assert d["supplier"] == "Sample CN"
    assert d["region"] == "CN"
    assert d["cost"] == 120.0
    assert d["currency"] == "CNY"
    assert d["lead_time_days"] == 7


def test_product_detail_bundles_offers_and_equivalents():
    eq_product = SimpleNamespace(
        id=uuid.UUID("abcdef12-0000-0000-0000-000000000000"),
        name="NEB Taq", category="molecular_biology", brand="NEB", mpn="M0480",
        specs={},
    )
    d = schemas.product_detail(_product(), [_offer()], [(eq_product, 0.86, "substitute")])
    assert d["astor_sku"] == "ASR-7F3A21"
    assert d["specs"] == {"volume": "5 mL"}
    assert d["offers"][0]["supplier"] == "Sample CN"
    eq = d["equivalents"][0]
    assert eq["astor_sku"] == "ASR-ABCDEF"
    assert eq["brand"] == "NEB"
    assert eq["confidence"] == 0.86
    assert eq["kind"] == "substitute"


def test_stats_out_shape():
    d = schemas.stats_out(products=10, offers=20, exact=3, substitute=5,
                          suppliers=2, avg_savings=0.38)
    assert d == {"products": 10, "offers": 20,
                 "equivalences": {"exact": 3, "substitute": 5, "total": 8},
                 "suppliers": 2, "avg_savings": 0.38}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'astor.api.schemas'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/astor/api/schemas.py`:

```python
"""DTO builder functions: ORM object -> plain dict (OPS shape).

Builders never touch the database; they only read attributes. Role gating
(roles.py) is applied by the routers after building.
"""
from __future__ import annotations

from astor.api.skus import astor_sku


def product_summary(product, offer_count: int, best_landed: float | None) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "category": product.category,
        "brand": product.brand,
        "mpn": product.mpn,
        "region": None,  # a product spans suppliers; region lives on offers
        "offer_count": offer_count,
        "best_landed": best_landed,
    }


def offer_out(offer) -> dict:
    return {
        "supplier": offer.supplier.name,
        "region": offer.supplier.region,
        "supplier_sku": offer.supplier_sku,
        "pack_size": offer.pack_size,
        "cost": float(offer.cost),
        "currency": offer.currency,
        "stock": offer.stock,
        "lead_time_days": offer.lead_time_days,
    }


def equivalent_out(product, confidence: float, kind: str) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "brand": product.brand,
        "region": None,
        "supplier": None,
        "confidence": round(float(confidence), 4),
        "kind": kind,
    }


def product_detail(product, offers: list, equivalents: list) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "category": product.category,
        "brand": product.brand,
        "mpn": product.mpn,
        "specs": product.specs or {},
        "offers": [offer_out(o) for o in offers],
        "equivalents": [equivalent_out(p, c, k) for (p, c, k) in equivalents],
    }


def stats_out(*, products: int, offers: int, exact: int, substitute: int,
              suppliers: int, avg_savings: float) -> dict:
    return {
        "products": products,
        "offers": offers,
        "equivalences": {"exact": exact, "substitute": substitute,
                         "total": exact + substitute},
        "suppliers": suppliers,
        "avg_savings": avg_savings,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_schemas.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/schemas.py tests/api/test_schemas.py
git commit -m "feat(api): pure DTO builder functions"
```

---

## Task 5: Repo data-access layer

**Files:**
- Create: `src/astor/api/repo.py`

**Interfaces:**
- Produces (all take a `Session` as first arg):
  - `get_stats(session) -> dict` — returns the `stats_out(...)` dict.
  - `list_products(session, q: str | None, category: str | None, page: int, page_size: int) -> tuple[list[dict], int]` — returns `(summaries, total_count)`; each summary is `product_summary(...)`.
  - `get_product_detail(session, product_id: str) -> dict | None` — returns `product_detail(...)` or `None` if not found.
  - `landed_for_product(session, product_id: str, qty: int) -> dict | None` — landed cost over the cheapest offer; `None` if product/offer missing.
  - `run_ingest(session, path: Path, supplier: str, region: str, tier: str, run_match: bool) -> dict` — calls `ingestion.ingest` + optional matcher; returns `{"extracted", "products", "offers", "equivalences_written"}`.

This module touches the DB and pgvector, so it is exercised by the integration smoke test in Task 10 (skipped without Postgres). No standalone unit test here — routers are tested by monkeypatching these functions in Tasks 6–9.

- [ ] **Step 1: Create the repo module**

Create `src/astor/api/repo.py`:

```python
"""Thin data-access layer. The only module that issues queries / mutations.

Routers depend on these functions and are tested by monkeypatching them, so no
Postgres is needed in unit tests. This module itself is covered by the
DB-gated smoke test.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from astor.api import schemas
from astor.catalog import matcher
from astor.catalog.embeddings import get_embedder
from astor.catalog.ingestion import ingest
from astor.db.models import Equivalence, Product, Supplier, SupplierOffer
from astor.pricing.landed_cost import landed_cost


def _offer_count_map(session, product_ids: list[str]) -> dict[str, int]:
    if not product_ids:
        return {}
    rows = session.execute(
        select(SupplierOffer.product_id, func.count(SupplierOffer.id))
        .where(SupplierOffer.product_id.in_(product_ids))
        .group_by(SupplierOffer.product_id)
    ).all()
    return {str(pid): n for pid, n in rows}


def _cheapest_offer(session, product_id: str):
    return session.scalar(
        select(SupplierOffer)
        .where(SupplierOffer.product_id == product_id)
        .order_by(SupplierOffer.cost.asc())
        .limit(1)
    )


def _best_landed(session, product_id: str, category: str) -> float | None:
    offer = _cheapest_offer(session, product_id)
    if offer is None:
        return None
    bd = landed_cost(supplier_cost=float(offer.cost), currency=offer.currency,
                     category=category, qty=1)
    return bd["unit_price"]


def get_stats(session) -> dict:
    products = session.scalar(select(func.count(Product.id))) or 0
    offers = session.scalar(select(func.count(SupplierOffer.id))) or 0
    suppliers = session.scalar(select(func.count(Supplier.id))) or 0
    exact = session.scalar(
        select(func.count(Equivalence.id)).where(Equivalence.kind == "exact")) or 0
    substitute = session.scalar(
        select(func.count(Equivalence.id)).where(Equivalence.kind == "substitute")) or 0

    # Avg savings = mean over products of (1 - best_landed / proxy_us_list).
    # Proxy US list is the dearest USD-equivalent offer; placeholder until real
    # list prices exist. Returns 0.0 when not computable.
    avg_savings = 0.0
    return schemas.stats_out(products=products, offers=offers, exact=exact,
                             substitute=substitute, suppliers=suppliers,
                             avg_savings=round(avg_savings, 4))


def list_products(session, q, category, page, page_size) -> tuple[list[dict], int]:
    stmt = select(Product)
    count_stmt = select(func.count(Product.id))
    if q:
        like = f"%{q}%"
        cond = Product.name.ilike(like) | Product.brand.ilike(like) | Product.mpn.ilike(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if category:
        stmt = stmt.where(Product.category == category)
        count_stmt = count_stmt.where(Product.category == category)

    total = session.scalar(count_stmt) or 0
    rows = session.scalars(
        stmt.order_by(Product.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    ids = [str(p.id) for p in rows]
    counts = _offer_count_map(session, ids)
    summaries = [
        schemas.product_summary(
            p, offer_count=counts.get(str(p.id), 0),
            best_landed=_best_landed(session, str(p.id), p.category),
        )
        for p in rows
    ]
    return summaries, total


def get_product_detail(session, product_id: str) -> dict | None:
    product = session.get(Product, product_id)
    if product is None:
        return None
    offers = session.scalars(
        select(SupplierOffer).where(SupplierOffer.product_id == product_id)
    ).all()
    eq_rows = session.execute(
        select(Equivalence, Product)
        .join(Product, Product.id == Equivalence.equivalent_id)
        .where(Equivalence.product_id == product_id)
        .order_by(Equivalence.confidence.desc())
    ).all()
    equivalents = [(prod, eq.confidence, eq.kind) for eq, prod in eq_rows]
    return schemas.product_detail(product, offers, equivalents)


def landed_for_product(session, product_id: str, qty: int) -> dict | None:
    product = session.get(Product, product_id)
    if product is None:
        return None
    offer = _cheapest_offer(session, product_id)
    if offer is None:
        return None
    return landed_cost(supplier_cost=float(offer.cost), currency=offer.currency,
                       category=product.category, qty=qty)


def run_ingest(session, path: Path, supplier: str, region: str, tier: str,
               run_match: bool) -> dict:
    result = ingest(session, path, supplier, region, tier)
    written = 0
    if run_match:
        embedder = get_embedder()
        for pid in result.products_to_match:
            written += len(matcher.match_product(session, pid, embedder))
    return {
        "extracted": result.extracted,
        "products": result.products_upserted,
        "offers": result.offers_upserted,
        "equivalences_written": written,
    }
```

- [ ] **Step 2: Import-smoke check**

Run: `python -c "from astor.api import repo; print('ok')"`
Expected: prints `ok` (module imports cleanly).

- [ ] **Step 3: Commit**

```bash
git add src/astor/api/repo.py
git commit -m "feat(api): repo data-access layer over existing backend"
```

---

## Task 6: Dashboard router

**Files:**
- Create: `src/astor/api/routers/__init__.py`
- Create: `src/astor/api/routers/dashboard.py`
- Modify: `src/astor/api/main.py` (mount the router)
- Test: `tests/api/test_dashboard.py`

**Interfaces:**
- Consumes: `repo.get_stats`, `deps.get_session`.
- Produces: `GET /api/stats -> dict` (the `stats_out` shape).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_dashboard.py`:

```python
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

STATS = {"products": 10, "offers": 20,
         "equivalences": {"exact": 3, "substitute": 5, "total": 8},
         "suppliers": 2, "avg_savings": 0.38}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "get_stats", lambda session: STATS)
    return TestClient(app)


def test_stats_endpoint(monkeypatch):
    resp = _client(monkeypatch).get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == STATS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_dashboard.py -v`
Expected: FAIL (404 — route not mounted yet).

- [ ] **Step 3: Create the router**

Create `src/astor/api/routers/__init__.py` (empty file).

Create `src/astor/api/routers/dashboard.py`:

```python
"""Dashboard stats."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from astor.api import repo
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    return repo.get_stats(session)
```

- [ ] **Step 4: Mount the router**

In `src/astor/api/main.py`, add the import and registration. Replace the `create_app` body so it includes the router (add these two lines: the import at top, and `app.include_router(...)` before `return app`):

At the top of the file, after the existing imports, add:

```python
from astor.api.routers import dashboard
```

Inside `create_app()`, immediately before `return app`, add:

```python
    app.include_router(dashboard.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/api/test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/astor/api/routers src/astor/api/main.py tests/api/test_dashboard.py
git commit -m "feat(api): dashboard stats endpoint"
```

---

## Task 7: Catalog router — products list & detail (with role gating)

**Files:**
- Create: `src/astor/api/routers/catalog.py`
- Modify: `src/astor/api/main.py` (mount the router)
- Test: `tests/api/test_catalog.py`

**Interfaces:**
- Consumes: `repo.list_products`, `repo.get_product_detail`, `roles.gate_product`, `roles.gate_detail`, `deps.get_session`.
- Produces:
  - `GET /api/products?q=&category=&page=1&page_size=20&role=ops -> {"items": [...], "total": int, "page": int, "page_size": int}` (each item gated by role).
  - `GET /api/products/{id}?role=ops -> dict` (gated) or `404`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_catalog.py`:

```python
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

SUMMARY = {"id": "1", "astor_sku": "ASR-7F3A21", "name": "Taq",
           "category": "molecular_biology", "brand": "Vazyme", "mpn": "P112",
           "region": None, "offer_count": 2, "best_landed": 24.8}

DETAIL = {"id": "1", "astor_sku": "ASR-7F3A21", "name": "Taq",
          "category": "molecular_biology", "brand": "Vazyme", "mpn": "P112",
          "specs": {"volume": "5 mL"},
          "offers": [{"supplier": "Sample CN", "region": "CN", "cost": 120.0,
                      "currency": "CNY", "supplier_sku": "VZ-EX-001",
                      "pack_size": "5 mL", "stock": 200, "lead_time_days": 7}],
          "equivalents": [{"id": "2", "astor_sku": "ASR-ABCDEF", "name": "NEB Taq",
                           "brand": "NEB", "region": None, "supplier": None,
                           "confidence": 0.86, "kind": "substitute"}]}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "list_products",
                        lambda s, q, category, page, page_size: ([SUMMARY], 1))
    monkeypatch.setattr(repo, "get_product_detail",
                        lambda s, pid: DETAIL if pid == "1" else None)
    return TestClient(app)


def test_products_list_ops_includes_brand(monkeypatch):
    resp = _client(monkeypatch).get("/api/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["brand"] == "Vazyme"


def test_products_list_buyer_hides_origin(monkeypatch):
    resp = _client(monkeypatch).get("/api/products?role=buyer")
    item = resp.json()["items"][0]
    assert item["astor_sku"] == "ASR-7F3A21"
    for forbidden in ("brand", "mpn", "region"):
        assert forbidden not in item


def test_product_detail_ops(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1")
    assert resp.status_code == 200
    assert resp.json()["offers"][0]["supplier"] == "Sample CN"


def test_product_detail_buyer_strips_offers_and_equivalent_brand(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1?role=buyer")
    body = resp.json()
    assert "offers" not in body
    assert "brand" not in body
    assert "brand" not in body["equivalents"][0]


def test_product_detail_404(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/missing")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_catalog.py -v`
Expected: FAIL (404 — routes not mounted).

- [ ] **Step 3: Create the router**

Create `src/astor/api/routers/catalog.py`:

```python
"""Catalog: product list, product detail, ingest."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from astor.api import repo, roles
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/products")
def products(
    q: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    items, total = repo.list_products(session, q, category, page, page_size)
    return {
        "items": [roles.gate_product(i, role) for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/products/{product_id}")
def product_detail(
    product_id: str,
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    detail = repo.get_product_detail(session, product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return roles.gate_detail(detail, role)
```

- [ ] **Step 4: Mount the router**

In `src/astor/api/main.py`, add to the routers import line so it reads:

```python
from astor.api.routers import catalog, dashboard
```

Inside `create_app()`, before `return app`, add:

```python
    app.include_router(catalog.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/api/test_catalog.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/astor/api/routers/catalog.py src/astor/api/main.py tests/api/test_catalog.py
git commit -m "feat(api): product list & detail with role gating"
```

---

## Task 8: Pricing router — landed cost (with role gating)

**Files:**
- Create: `src/astor/api/routers/pricing.py`
- Modify: `src/astor/api/main.py` (mount the router)
- Test: `tests/api/test_pricing.py`

**Interfaces:**
- Consumes: `repo.landed_for_product`, `roles.gate_landed`, `deps.get_session`.
- Produces: `GET /api/products/{id}/landed-cost?qty=1&role=ops -> dict` (gated) or `404`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_pricing.py`:

```python
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

BREAKDOWN = {"currency": "USD", "qty": 1, "ex_works": 16.8, "tariff": 4.2,
             "duty_rate": 0.25, "freight": 1.5, "margin": 4.5,
             "unit_price": 27.0, "line_total": 27.0}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(repo, "landed_for_product",
                        lambda s, pid, qty: BREAKDOWN if pid == "1" else None)
    return TestClient(app)


def test_landed_cost_ops_full_breakdown(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1/landed-cost")
    assert resp.status_code == 200
    assert resp.json()["ex_works"] == 16.8


def test_landed_cost_buyer_price_only(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/1/landed-cost?role=buyer")
    body = resp.json()
    assert body["unit_price"] == 27.0
    for forbidden in ("ex_works", "tariff", "duty_rate", "freight", "margin"):
        assert forbidden not in body


def test_landed_cost_404(monkeypatch):
    resp = _client(monkeypatch).get("/api/products/missing/landed-cost")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_pricing.py -v`
Expected: FAIL (404 — route not mounted).

- [ ] **Step 3: Create the router**

Create `src/astor/api/routers/pricing.py`:

```python
"""Landed-cost breakdown for a product (cheapest offer)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from astor.api import repo, roles
from astor.api.deps import get_session

router = APIRouter(prefix="/api", tags=["pricing"])


@router.get("/products/{product_id}/landed-cost")
def landed_cost_endpoint(
    product_id: str,
    qty: int = Query(1, ge=1),
    role: str = "ops",
    session: Session = Depends(get_session),
) -> dict:
    bd = repo.landed_for_product(session, product_id, qty)
    if bd is None:
        raise HTTPException(status_code=404, detail="no priceable offer for product")
    return roles.gate_landed(bd, role)
```

- [ ] **Step 4: Mount the router**

In `src/astor/api/main.py`, update the routers import to:

```python
from astor.api.routers import catalog, dashboard, pricing
```

Inside `create_app()`, before `return app`, add:

```python
    app.include_router(pricing.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/api/test_pricing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/astor/api/routers/pricing.py src/astor/api/main.py tests/api/test_pricing.py
git commit -m "feat(api): landed-cost endpoint with role gating"
```

---

## Task 9: Ingest endpoint (multipart upload)

**Files:**
- Modify: `src/astor/api/routers/catalog.py` (add the ingest route)
- Test: `tests/api/test_ingest.py`

**Interfaces:**
- Consumes: `repo.run_ingest`, `deps.get_session`.
- Produces: `POST /api/ingest` (multipart form: `file`, `supplier`, `region`, `tier`, `run_match`) → `{"extracted", "products", "offers", "equivalences_written"}`. Rejects non-CSV/XLSX with `422`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_ingest.py`:

```python
import io

from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app

RESULT = {"extracted": 3, "products": 3, "offers": 3, "equivalences_written": 2}


def _client(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # repo is monkeypatched, session unused
    monkeypatch.setattr(
        repo, "run_ingest",
        lambda s, path, supplier, region, tier, run_match: RESULT)
    return TestClient(app)


def test_ingest_csv_returns_summary(monkeypatch):
    files = {"file": ("supplier.csv", io.BytesIO(b"supplier_sku,name\nX,Y\n"), "text/csv")}
    data = {"supplier": "Sample CN", "region": "CN", "tier": "public", "run_match": "true"}
    resp = _client(monkeypatch).post("/api/ingest", files=files, data=data)
    assert resp.status_code == 200
    assert resp.json() == RESULT


def test_ingest_rejects_bad_extension(monkeypatch):
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    data = {"supplier": "Sample CN", "region": "CN", "tier": "public", "run_match": "false"}
    resp = _client(monkeypatch).post("/api/ingest", files=files, data=data)
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_ingest.py -v`
Expected: FAIL (404 — route does not exist).

- [ ] **Step 3: Add the ingest route**

In `src/astor/api/routers/catalog.py`, update the imports at the top to:

```python
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from astor.api import repo, roles
from astor.api.deps import get_session
```

Then append this route to the end of the file:

```python
_ALLOWED_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xlsm"}


@router.post("/ingest")
def ingest_catalog(
    file: UploadFile = File(...),
    supplier: str = Form(...),
    region: str = Form("CN"),
    tier: str = Form("public"),
    run_match: bool = Form(True),
    session: Session = Depends(get_session),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type '{suffix}'; expected CSV or XLSX",
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
    try:
        return repo.run_ingest(session, tmp_path, supplier, region, tier, run_match)
    finally:
        tmp_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_ingest.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full backend suite**

Run: `pytest -v`
Expected: all existing + new API tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/astor/api/routers/catalog.py tests/api/test_ingest.py
git commit -m "feat(api): catalog ingest upload endpoint"
```

---

## Task 10: Demo seed + dev setup + integration smoke test

**Files:**
- Create: `src/astor/api/seed.py`
- Modify: `src/astor/api/main.py` (seed on startup when `SEED_DEMO=1`)
- Create: `docker-compose.yml`
- Create: `tests/api/test_integration_smoke.py`
- Modify: `.env.example` (document `SEED_DEMO`)

**Interfaces:**
- Consumes: `repo.run_ingest`.
- Produces: `seed_demo(session) -> dict` (ingests `data/sample_supplier_cn.csv` with matching on).

- [ ] **Step 1: Create the seed helper**

Create `src/astor/api/seed.py`:

```python
"""Demo seed: ingest the sample CN catalog with the offline DevEmbedder.

Requires a running Postgres + pgvector (the matcher uses vector ops). It only
removes the need for embedding-provider API keys, not the need for a database.
"""
from __future__ import annotations

import logging
from pathlib import Path

from astor.api import repo

log = logging.getLogger(__name__)

_SAMPLE = Path("data/sample_supplier_cn.csv")


def seed_demo(session) -> dict:
    if not _SAMPLE.exists():
        log.warning("demo seed skipped: %s not found", _SAMPLE)
        return {"extracted": 0, "products": 0, "offers": 0, "equivalences_written": 0}
    result = repo.run_ingest(session, _SAMPLE, "Sample CN", "CN", "public", run_match=True)
    log.info("demo seed: %s", result)
    return result
```

- [ ] **Step 2: Wire startup seeding**

In `src/astor/api/main.py`, add `import os` near the top if not already present, then inside `create_app()` — after the routers are included and before `return app` — add:

```python
    if os.getenv("SEED_DEMO") == "1":
        from astor.api.seed import seed_demo
        from astor.db.base import session_scope

        @app.on_event("startup")
        def _seed() -> None:
            with session_scope() as session:
                seed_demo(session)
```

- [ ] **Step 3: Create a dev Postgres compose file**

Create `docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: astor
      POSTGRES_PASSWORD: astor
      POSTGRES_DB: astor
    ports:
      - "5432:5432"
    volumes:
      - astor_pgdata:/var/lib/postgresql/data

volumes:
  astor_pgdata:
```

- [ ] **Step 4: Document SEED_DEMO**

In `.env.example`, add this line at the end:

```bash
SEED_DEMO=0   # set to 1 to auto-ingest data/sample_supplier_cn.csv on API startup
```

- [ ] **Step 5: Write a DB-gated smoke test**

Create `tests/api/test_integration_smoke.py`:

```python
"""End-to-end smoke test. Skipped unless a real Postgres+pgvector is reachable.

Run locally with:  docker compose up -d  &&  alembic upgrade head  &&  pytest
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres+pgvector; set RUN_DB_TESTS=1 to run",
)


def test_seed_then_stats_and_detail():
    from astor.api import repo
    from astor.api.seed import seed_demo
    from astor.db.base import session_scope

    with session_scope() as session:
        seed_demo(session)
        stats = repo.get_stats(session)
        assert stats["products"] >= 1
        items, total = repo.list_products(session, None, None, 1, 20)
        assert total >= 1
        detail = repo.get_product_detail(session, items[0]["id"])
        assert detail is not None and detail["astor_sku"].startswith("ASR-")
```

- [ ] **Step 6: Verify the smoke test skips cleanly (no DB)**

Run: `pytest tests/api/test_integration_smoke.py -v`
Expected: 1 skipped (reason: needs Postgres+pgvector).

- [ ] **Step 7: Commit**

```bash
git add src/astor/api/seed.py src/astor/api/main.py docker-compose.yml .env.example tests/api/test_integration_smoke.py
git commit -m "feat(api): demo seed, dev compose, integration smoke test"
```

---

## Task 11: Scaffold the Next.js app

**Files:**
- Create: `web/` (Next.js project) + `web/.env.local`
- Modify: `.gitignore` (ignore `web/node_modules`, `web/.next`)

**Interfaces:**
- Produces: a running Next.js 15 app at `web/` with Tailwind, the dark theme tokens, and Vitest configured.

- [ ] **Step 1: Scaffold the project (non-interactive)**

Run:
```bash
npx create-next-app@latest web --typescript --tailwind --eslint --app --no-src-dir --import-alias "@/*" --use-npm
```
Expected: `web/` created with `app/`, `package.json`, `tailwind.config.ts`.

- [ ] **Step 2: Add Vitest + Testing Library**

Run:
```bash
cd web && npm install -D vitest @vitejs/plugin-react @testing-library/react @testing-library/jest-dom jsdom && cd ..
```
Expected: dev dependencies installed.

- [ ] **Step 3: Configure Vitest**

Create `web/vitest.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
});
```

Create `web/vitest.setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

In `web/package.json`, add a `"test": "vitest run"` entry to the `"scripts"` object.

- [ ] **Step 4: Set the dark marketplace theme tokens**

Replace the contents of `web/app/globals.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg: #0d1322;
  --bg-elev: #0a0e1a;
  --panel: rgba(255, 255, 255, 0.04);
  --border: rgba(255, 255, 255, 0.08);
  --text: #f1f5f9;
  --muted: #7c8aa5;
  --teal: #5eead4;
  --blue: #3b82f6;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
}

.gradient-accent {
  background: linear-gradient(135deg, var(--teal), var(--blue));
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
}
```

- [ ] **Step 5: Configure the API base URL**

Create `web/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 6: Ignore build artifacts**

Append to the repo-root `.gitignore`:

```
web/node_modules
web/.next
```

- [ ] **Step 7: Verify it builds**

Run: `cd web && npm run build && cd ..`
Expected: build completes successfully.

- [ ] **Step 8: Commit**

```bash
git add web .gitignore
git commit -m "chore(web): scaffold Next.js app with dark theme + vitest"
```

---

## Task 12: API client + shared types

**Files:**
- Create: `web/lib/types.ts`
- Create: `web/lib/api.ts`

**Interfaces:**
- Produces TypeScript types (`Stats`, `ProductSummary`, `ProductDetail`, `Offer`, `Equivalent`, `LandedCost`, `IngestResult`, `ProductsPage`, `Role`) and an `api` client with `getStats`, `listProducts`, `getProduct`, `getLandedCost`, `ingest`.

- [ ] **Step 1: Define the shared types**

Create `web/lib/types.ts`:

```typescript
export type Role = "ops" | "buyer";

export interface Stats {
  products: number;
  offers: number;
  equivalences: { exact: number; substitute: number; total: number };
  suppliers: number;
  avg_savings: number;
}

export interface ProductSummary {
  id: string;
  astor_sku: string;
  name: string;
  category: string;
  brand?: string | null;
  mpn?: string | null;
  region?: string | null;
  offer_count: number;
  best_landed: number | null;
}

export interface Offer {
  supplier: string;
  region: string;
  supplier_sku: string;
  pack_size: string | null;
  cost: number;
  currency: string;
  stock: number | null;
  lead_time_days: number | null;
}

export interface Equivalent {
  id: string;
  astor_sku: string;
  name: string;
  brand?: string | null;
  confidence: number;
  kind: "exact" | "substitute";
}

export interface ProductDetail {
  id: string;
  astor_sku: string;
  name: string;
  category: string;
  brand?: string | null;
  mpn?: string | null;
  specs: Record<string, unknown>;
  offers?: Offer[];
  equivalents: Equivalent[];
}

export interface LandedCost {
  currency: string;
  qty: number;
  ex_works?: number;
  tariff?: number;
  duty_rate?: number;
  freight?: number;
  margin?: number;
  unit_price: number;
  line_total: number;
}

export interface IngestResult {
  extracted: number;
  products: number;
  offers: number;
  equivalences_written: number;
}

export interface ProductsPage {
  items: ProductSummary[];
  total: number;
  page: number;
  page_size: number;
}
```

- [ ] **Step 2: Write the fetch client**

Create `web/lib/api.ts`:

```typescript
import type {
  IngestResult,
  LandedCost,
  ProductDetail,
  ProductsPage,
  Role,
  Stats,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getStats: () => get<Stats>("/api/stats"),

  listProducts: (opts: {
    q?: string;
    category?: string;
    page?: number;
    role?: Role;
  } = {}) => {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.category) p.set("category", opts.category);
    p.set("page", String(opts.page ?? 1));
    p.set("role", opts.role ?? "ops");
    return get<ProductsPage>(`/api/products?${p.toString()}`);
  },

  getProduct: (id: string, role: Role = "ops") =>
    get<ProductDetail>(`/api/products/${id}?role=${role}`),

  getLandedCost: (id: string, qty: number, role: Role = "ops") =>
    get<LandedCost>(`/api/products/${id}/landed-cost?qty=${qty}&role=${role}`),

  ingest: async (form: FormData): Promise<IngestResult> => {
    const res = await fetch(`${BASE}/api/ingest`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Ingest failed: ${res.status}`);
    }
    return res.json() as Promise<IngestResult>;
  },
};
```

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add web/lib
git commit -m "feat(web): typed API client and shared types"
```

---

## Task 13: App shell — sidebar + role toggle

**Files:**
- Create: `web/components/Sidebar.tsx`
- Create: `web/app/(shell)/layout.tsx`
- Modify: `web/app/layout.tsx` (ensure it just renders children + globals)
- Delete: `web/app/page.tsx` (the scaffold home; replaced by the shell dashboard in Task 14)

**Interfaces:**
- Consumes: nothing from the API.
- Produces: a persistent sidebar layout wrapping all `(shell)` routes; a client-side role toggle that stores the selected role in `localStorage` under key `astor-role` (default `ops`).

- [ ] **Step 1: Build the sidebar**

Create `web/components/Sidebar.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { Role } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard", icon: "◧" },
  { href: "/ingest", label: "Ingest & Browse", icon: "⇪" },
];

export function Sidebar() {
  const pathname = usePathname();
  const [role, setRole] = useState<Role>("ops");

  useEffect(() => {
    const saved = window.localStorage.getItem("astor-role") as Role | null;
    if (saved) setRole(saved);
  }, []);

  function pick(next: Role) {
    setRole(next);
    window.localStorage.setItem("astor-role", next);
  }

  return (
    <aside
      className="flex w-56 flex-col gap-6 p-4"
      style={{ background: "var(--bg-elev)", borderRight: "1px solid var(--border)" }}
    >
      <div className="flex items-center gap-2">
        <div className="gradient-accent h-5 w-5 rounded-md" />
        <span className="font-bold">
          Astor<span style={{ color: "var(--teal)" }}>Scientific</span>
        </span>
      </div>

      <nav className="flex flex-col gap-1">
        <div className="px-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          Workspace
        </div>
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-2 text-sm"
              style={{
                color: active ? "var(--teal)" : "#aeb8cc",
                background: active ? "rgba(94,234,212,0.1)" : "transparent",
              }}
            >
              <span className="mr-2">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto">
        <div className="mb-2 px-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          Role
        </div>
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "rgba(255,255,255,0.06)" }}>
          {(["ops", "buyer"] as Role[]).map((r) => (
            <button
              key={r}
              onClick={() => pick(r)}
              className="flex-1 rounded-md px-2 py-1 text-xs capitalize"
              style={{
                color: role === r ? "#0d1322" : "#aeb8cc",
                background: role === r ? "var(--teal)" : "transparent",
                fontWeight: role === r ? 700 : 400,
              }}
            >
              {r}
            </button>
          ))}
        </div>
        <p className="mt-2 px-1 text-[10px]" style={{ color: "var(--muted)" }}>
          M1 builds the Ops views. Buyer hides origin & supplier.
        </p>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Create the shell layout**

Create `web/app/(shell)/layout.tsx`:

```tsx
import { Sidebar } from "@/components/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-auto p-8">{children}</main>
    </div>
  );
}
```

- [ ] **Step 3: Simplify the root layout**

Ensure `web/app/layout.tsx` imports `./globals.css` and renders `{children}` inside `<body>` (remove the scaffold's font boilerplate if it complicates things — keep it minimal):

```tsx
import "./globals.css";

export const metadata = { title: "AstorScientific", description: "AI-native procurement" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 4: Remove the scaffold home page**

Run: `rm web/app/page.tsx`
Expected: file removed (the dashboard at `(shell)/page.tsx` becomes `/` in Task 14).

- [ ] **Step 5: Type-check**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: no type errors.

- [ ] **Step 6: Commit**

```bash
git add web/components/Sidebar.tsx "web/app/(shell)/layout.tsx" web/app/layout.tsx
git commit -m "feat(web): app shell with sidebar and role toggle"
```

---

## Task 14: Dashboard page

**Files:**
- Create: `web/components/KpiCard.tsx`
- Create: `web/components/ConfidenceBar.tsx`
- Create: `web/components/KindBadge.tsx`
- Create: `web/app/(shell)/page.tsx`

**Interfaces:**
- Consumes: `api.getStats`.
- Produces: the Dashboard route at `/` — hero strip, KPI cards, and an empty-state-aware layout.

- [ ] **Step 1: Build the KPI card**

Create `web/components/KpiCard.tsx`:

```tsx
export function KpiCard({
  label,
  value,
  accent = false,
  sub,
}: {
  label: string;
  value: string | number;
  accent?: boolean;
  sub?: string;
}) {
  return (
    <div
      className="card p-4"
      style={accent ? { background: "rgba(94,234,212,0.07)", borderColor: "rgba(94,234,212,0.2)" } : undefined}
    >
      <div className="text-[11px] uppercase tracking-wide" style={{ color: accent ? "var(--teal)" : "var(--muted)" }}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-extrabold">{value}</div>
      {sub && <div className="mt-1 text-xs" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Build the confidence bar and kind badge**

Create `web/components/ConfidenceBar.tsx`:

```tsx
export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded" style={{ background: "rgba(255,255,255,0.08)" }}>
        <div className="gradient-accent h-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-bold" style={{ color: "var(--teal)" }}>{value.toFixed(2)}</span>
    </div>
  );
}
```

Create `web/components/KindBadge.tsx`:

```tsx
export function KindBadge({ kind }: { kind: "exact" | "substitute" }) {
  const isExact = kind === "exact";
  return (
    <span
      className="rounded px-2 py-0.5 text-[10px] font-bold uppercase"
      style={{
        color: isExact ? "#0d1322" : "var(--teal)",
        background: isExact ? "var(--teal)" : "rgba(94,234,212,0.12)",
      }}
    >
      {kind}
    </span>
  );
}
```

- [ ] **Step 3: Build the Dashboard page**

Create `web/app/(shell)/page.tsx`:

```tsx
import { KpiCard } from "@/components/KpiCard";
import { api } from "@/lib/api";
import type { Stats } from "@/lib/types";

export default async function DashboardPage() {
  let stats: Stats | null = null;
  let error: string | null = null;
  try {
    stats = await api.getStats();
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load stats";
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6">
        <h1 className="text-xl font-bold">China↔US sourcing, priced end to end.</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          Catalog health &amp; sourcing overview
        </p>
      </section>

      {error && (
        <div className="card p-4" style={{ borderColor: "#7f1d1d", color: "#fca5a5" }}>
          {error} — is the API running on {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}?
        </div>
      )}

      {stats && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label="Products" value={stats.products} />
          <KpiCard label="Offers" value={stats.offers} />
          <KpiCard
            label="Equivalences"
            value={stats.equivalences.total}
            sub={`${stats.equivalences.exact} exact · ${stats.equivalences.substitute} sub`}
          />
          <KpiCard label="Avg savings" value={`${Math.round(stats.avg_savings * 100)}%`} accent />
        </section>
      )}

      {stats && stats.products === 0 && (
        <div className="card p-6 text-sm" style={{ color: "var(--muted)" }}>
          No products yet. Head to <a href="/ingest" style={{ color: "var(--teal)" }}>Ingest &amp; Browse</a> to load a supplier catalog.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add web/components/KpiCard.tsx web/components/ConfidenceBar.tsx web/components/KindBadge.tsx "web/app/(shell)/page.tsx"
git commit -m "feat(web): dashboard page with KPI cards"
```

---

## Task 15: LandedCostWaterfall component + test

**Files:**
- Create: `web/components/LandedCostWaterfall.tsx`
- Test: `web/components/__tests__/LandedCostWaterfall.test.tsx`

**Interfaces:**
- Consumes: a `LandedCost` object (props).
- Produces: a presentational waterfall. Renders the ops breakdown rows (ex-works → tariff → freight → margin → unit price) when present, and always renders `unit_price` and `line_total`. Buyer mode (internals absent) shows price only.

- [ ] **Step 1: Write the failing component test**

Create `web/components/__tests__/LandedCostWaterfall.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LandedCostWaterfall } from "../LandedCostWaterfall";
import type { LandedCost } from "@/lib/types";

const OPS: LandedCost = {
  currency: "USD", qty: 2, ex_works: 16.8, tariff: 4.2, duty_rate: 0.25,
  freight: 1.5, margin: 4.5, unit_price: 27.0, line_total: 54.0,
};

const BUYER: LandedCost = { currency: "USD", qty: 2, unit_price: 27.0, line_total: 54.0 };

describe("LandedCostWaterfall", () => {
  it("renders the full ops breakdown including duty rate", () => {
    render(<LandedCostWaterfall data={OPS} />);
    expect(screen.getByText(/ex-works/i)).toBeInTheDocument();
    expect(screen.getByText(/tariff/i)).toBeInTheDocument();
    expect(screen.getByText(/25%/)).toBeInTheDocument(); // duty_rate formatted
    expect(screen.getByText("$27.00")).toBeInTheDocument(); // unit price
    expect(screen.getByText("$54.00")).toBeInTheDocument(); // line total
  });

  it("hides internal rows in buyer mode (price only)", () => {
    render(<LandedCostWaterfall data={BUYER} />);
    expect(screen.queryByText(/ex-works/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/tariff/i)).not.toBeInTheDocument();
    expect(screen.getByText("$27.00")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- LandedCostWaterfall && cd ..`
Expected: FAIL (cannot find `../LandedCostWaterfall`).

- [ ] **Step 3: Build the component**

Create `web/components/LandedCostWaterfall.tsx`:

```tsx
import type { LandedCost } from "@/lib/types";

function money(n: number) {
  return `$${n.toFixed(2)}`;
}

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div
      className="flex items-center justify-between px-3 py-2 text-sm"
      style={{ borderTop: "1px solid var(--border)", fontWeight: strong ? 700 : 400 }}
    >
      <span style={{ color: strong ? "var(--text)" : "var(--muted)" }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function LandedCostWaterfall({ data }: { data: LandedCost }) {
  const hasInternals = data.ex_works !== undefined;
  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 text-[11px] uppercase tracking-wide" style={{ color: "var(--teal)" }}>
        Landed cost ({data.currency})
      </div>
      {hasInternals && (
        <>
          <Row label="Ex-works" value={money(data.ex_works!)} />
          <Row
            label={`Tariff (${Math.round((data.duty_rate ?? 0) * 100)}%)`}
            value={money(data.tariff ?? 0)}
          />
          <Row label="Freight" value={money(data.freight ?? 0)} />
          <Row label="Margin" value={money(data.margin ?? 0)} />
        </>
      )}
      <Row label="Unit price" value={money(data.unit_price)} strong />
      <Row label={`Line total (qty ${data.qty})`} value={money(data.line_total)} strong />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- LandedCostWaterfall && cd ..`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add web/components/LandedCostWaterfall.tsx web/components/__tests__/LandedCostWaterfall.test.tsx
git commit -m "feat(web): landed-cost waterfall component with test"
```

---

## Task 16: Product detail page

**Files:**
- Create: `web/components/OffersTable.tsx`
- Create: `web/components/EquivalentsPanel.tsx`
- Create: `web/components/LandedCostPanel.tsx`
- Create: `web/app/(shell)/products/[id]/page.tsx`

**Interfaces:**
- Consumes: `api.getProduct`, `api.getLandedCost`.
- Produces: the route `/products/[id]` — header (Astor SKU primary), offers table (ops), landed-cost panel with a qty selector, equivalents panel.

- [ ] **Step 1: Build the offers table**

Create `web/components/OffersTable.tsx`:

```tsx
import type { Offer } from "@/lib/types";

export function OffersTable({ offers }: { offers: Offer[] }) {
  if (offers.length === 0) {
    return <p className="text-sm" style={{ color: "var(--muted)" }}>No supplier offers.</p>;
  }
  return (
    <div className="card overflow-hidden">
      <div className="grid grid-cols-6 px-3 py-2 text-[10px] uppercase tracking-wide" style={{ color: "var(--muted)" }}>
        <span>Supplier</span><span>Region</span><span>SKU</span><span>Pack</span><span>Cost</span><span>Lead</span>
      </div>
      {offers.map((o) => (
        <div key={o.supplier_sku} className="grid grid-cols-6 px-3 py-2 text-sm" style={{ borderTop: "1px solid var(--border)" }}>
          <span>{o.supplier}</span>
          <span>{o.region}</span>
          <span>{o.supplier_sku}</span>
          <span>{o.pack_size ?? "—"}</span>
          <span>{o.cost} {o.currency}</span>
          <span>{o.lead_time_days != null ? `${o.lead_time_days}d` : "—"}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Build the equivalents panel**

Create `web/components/EquivalentsPanel.tsx`:

```tsx
import Link from "next/link";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { KindBadge } from "@/components/KindBadge";
import type { Equivalent } from "@/lib/types";

export function EquivalentsPanel({ items }: { items: Equivalent[] }) {
  if (items.length === 0) {
    return <p className="text-sm" style={{ color: "var(--muted)" }}>No equivalents found.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((e) => (
        <Link key={e.id} href={`/products/${e.id}`} className="card flex items-center justify-between p-3">
          <div>
            <div className="text-sm font-semibold">{e.name}</div>
            <div className="text-xs" style={{ color: "var(--muted)" }}>
              {e.astor_sku}{e.brand ? ` · ${e.brand}` : ""}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <ConfidenceBar value={e.confidence} />
            <KindBadge kind={e.kind} />
          </div>
        </Link>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Build the landed-cost panel (client, qty selector)**

Create `web/components/LandedCostPanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { LandedCostWaterfall } from "@/components/LandedCostWaterfall";
import { api } from "@/lib/api";
import type { LandedCost } from "@/lib/types";

export function LandedCostPanel({ productId }: { productId: string }) {
  const [qty, setQty] = useState(1);
  const [data, setData] = useState<LandedCost | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getLandedCost(productId, qty)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [productId, qty]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <label style={{ color: "var(--muted)" }}>Qty</label>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Math.max(1, Number(e.target.value)))}
          className="w-20 rounded-md px-2 py-1"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }}
        />
      </div>
      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}
      {data && <LandedCostWaterfall data={data} />}
    </div>
  );
}
```

- [ ] **Step 4: Build the product detail page**

Create `web/app/(shell)/products/[id]/page.tsx`:

```tsx
import { EquivalentsPanel } from "@/components/EquivalentsPanel";
import { LandedCostPanel } from "@/components/LandedCostPanel";
import { OffersTable } from "@/components/OffersTable";
import { api } from "@/lib/api";

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = await api.getProduct(id);

  return (
    <div className="flex flex-col gap-6">
      <header className="card p-6">
        <div className="text-xs uppercase tracking-wide" style={{ color: "var(--teal)" }}>
          {product.astor_sku}
        </div>
        <h1 className="mt-1 text-xl font-bold">{product.name}</h1>
        <div className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          {product.category}
          {product.brand ? ` · ${product.brand}` : ""}
          {product.mpn ? ` · ${product.mpn}` : ""}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(product.specs).map(([k, v]) => (
            <span key={k} className="rounded px-2 py-1 text-xs" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
              {k}: {String(v)}
            </span>
          ))}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Landed cost</h2>
          <LandedCostPanel productId={product.id} />
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Equivalents</h2>
          <EquivalentsPanel items={product.equivalents} />
        </section>
      </div>

      {product.offers && (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Supplier offers</h2>
          <OffersTable offers={product.offers} />
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Type-check**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: no type errors.

- [ ] **Step 6: Commit**

```bash
git add web/components/OffersTable.tsx web/components/EquivalentsPanel.tsx web/components/LandedCostPanel.tsx "web/app/(shell)/products/[id]/page.tsx"
git commit -m "feat(web): product detail page with landed cost & equivalents"
```

---

## Task 17: Ingest & Browse page

**Files:**
- Create: `web/components/IngestForm.tsx`
- Create: `web/components/ProductsTable.tsx`
- Create: `web/app/(shell)/ingest/page.tsx`

**Interfaces:**
- Consumes: `api.ingest`, `api.listProducts`.
- Produces: the route `/ingest` — an ingest form (supplier/region/tier/file/run-match) showing the result summary, plus a searchable products table.

- [ ] **Step 1: Build the ingest form (client)**

Create `web/components/IngestForm.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { IngestResult } from "@/lib/types";

const INPUT = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  color: "var(--text)",
} as const;

export function IngestForm() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData(e.currentTarget);
      form.set("run_match", form.get("run_match") ? "true" : "false");
      setResult(await api.ingest(form));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card flex flex-col gap-3 p-5">
      <h2 className="text-sm font-semibold">Ingest supplier catalog</h2>
      <div className="grid grid-cols-2 gap-3">
        <input name="supplier" placeholder="Supplier name" required className="rounded-md px-3 py-2 text-sm" style={INPUT} />
        <select name="region" className="rounded-md px-3 py-2 text-sm" style={INPUT} defaultValue="CN">
          <option value="CN">CN</option>
          <option value="US">US</option>
          <option value="OTHER">OTHER</option>
        </select>
        <select name="tier" className="rounded-md px-3 py-2 text-sm" style={INPUT} defaultValue="public">
          <option value="public">public</option>
          <option value="authorized">authorized</option>
          <option value="deep">deep</option>
        </select>
        <input name="file" type="file" accept=".csv,.tsv,.xlsx,.xlsm" required className="text-sm" />
      </div>
      <label className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
        <input name="run_match" type="checkbox" defaultChecked /> Run matcher on new products
      </label>
      <button
        type="submit"
        disabled={busy}
        className="gradient-accent w-fit rounded-lg px-4 py-2 text-sm font-bold"
        style={{ color: "#0d1322", opacity: busy ? 0.6 : 1 }}
      >
        {busy ? "Ingesting…" : "Ingest"}
      </button>

      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}
      {result && (
        <p className="text-sm" style={{ color: "var(--teal)" }}>
          extracted {result.extracted} · products {result.products} · offers {result.offers} · equivalences {result.equivalences_written}
        </p>
      )}
    </form>
  );
}
```

- [ ] **Step 2: Build the products table (client, searchable)**

Create `web/components/ProductsTable.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProductSummary } from "@/lib/types";

export function ProductsTable() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const t = setTimeout(() => {
      api
        .listProducts({ q, page: 1 })
        .then((page) => {
          if (!active) return;
          setItems(page.items);
          setTotal(page.total);
        })
        .catch((e) => active && setError(e.message));
    }, 250);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [q]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Products <span style={{ color: "var(--muted)" }}>({total})</span></h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name / brand / MPN"
          className="rounded-md px-3 py-1.5 text-sm"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }}
        />
      </div>

      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}

      <div className="card overflow-hidden">
        <div className="grid grid-cols-5 px-3 py-2 text-[10px] uppercase tracking-wide" style={{ color: "var(--muted)" }}>
          <span>Astor SKU</span><span>Name</span><span>Category</span><span>Offers</span><span>Best landed</span>
        </div>
        {items.map((p) => (
          <Link key={p.id} href={`/products/${p.id}`} className="grid grid-cols-5 px-3 py-2 text-sm" style={{ borderTop: "1px solid var(--border)" }}>
            <span style={{ color: "var(--teal)" }}>{p.astor_sku}</span>
            <span>{p.name}</span>
            <span>{p.category}</span>
            <span>{p.offer_count}</span>
            <span>{p.best_landed != null ? `$${p.best_landed.toFixed(2)}` : "—"}</span>
          </Link>
        ))}
        {items.length === 0 && !error && (
          <div className="px-3 py-4 text-sm" style={{ color: "var(--muted)", borderTop: "1px solid var(--border)" }}>
            No products match.
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build the page**

Create `web/app/(shell)/ingest/page.tsx`:

```tsx
import { IngestForm } from "@/components/IngestForm";
import { ProductsTable } from "@/components/ProductsTable";

export default function IngestPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-bold">Ingest &amp; Browse</h1>
      <IngestForm />
      <ProductsTable />
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add web/components/IngestForm.tsx web/components/ProductsTable.tsx "web/app/(shell)/ingest/page.tsx"
git commit -m "feat(web): ingest & browse page"
```

---

## Task 18: Demo banner + README run guide

**Files:**
- Create: `web/components/DemoBanner.tsx`
- Modify: `web/app/(shell)/layout.tsx` (render the banner)
- Create: `web/README.md`

**Interfaces:**
- Produces: a small banner reminding that demo data uses the non-semantic DevEmbedder; a README documenting how to run API + web together.

- [ ] **Step 1: Build the banner**

Create `web/components/DemoBanner.tsx`:

```tsx
export function DemoBanner() {
  if (process.env.NEXT_PUBLIC_DEMO !== "1") return null;
  return (
    <div className="px-8 py-2 text-center text-xs" style={{ background: "rgba(94,234,212,0.1)", color: "var(--teal)" }}>
      Demo data — equivalence scores use the offline DevEmbedder and are not semantically meaningful.
    </div>
  );
}
```

- [ ] **Step 2: Render the banner in the shell**

In `web/app/(shell)/layout.tsx`, import and render the banner above `<main>`. Replace the file with:

```tsx
import { DemoBanner } from "@/components/DemoBanner";
import { Sidebar } from "@/components/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col">
        <DemoBanner />
        <main className="flex-1 overflow-auto p-8">{children}</main>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write the run guide**

Create `web/README.md`:

```markdown
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
```

- [ ] **Step 4: Run the web test suite + type-check**

Run: `cd web && npm test && npx tsc --noEmit && cd ..`
Expected: tests PASS, no type errors.

- [ ] **Step 5: Final full backend suite**

Run: `pytest -v`
Expected: all PASS (DB smoke test skipped).

- [ ] **Step 6: Commit**

```bash
git add web/components/DemoBanner.tsx "web/app/(shell)/layout.tsx" web/README.md
git commit -m "feat(web): demo banner and run guide"
```

---

## Done

At this point you have:
- A FastAPI layer (`src/astor/api`) with health, stats, products list/detail, landed-cost, and ingest endpoints — all role-gated, all unit-tested without a database.
- The origin-confidentiality invariant locked by `tests/api/test_roles.py` + the buyer-mode router tests.
- A Next.js ops UI (Dashboard, Ingest & Browse, Product Detail + Landed Cost) on the dark marketplace theme with the Astor SKU as the primary identifier.
- A one-command demo path (`docker compose up -d` → `alembic upgrade head` → `SEED_DEMO=1 uvicorn …` → `npm run dev`).

**Deferred to later milestones (per spec §12):** persisted `astor_sku` column, buyer-role screens, the equivalence review queue, real HS-code duty classification, and auth/tenancy.
```
