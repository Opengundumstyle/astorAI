# Promote → Source → Point Assistant (Move 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the storefront chat from a catalog search box into a bounded lab advisor that promotes Astor, captures unmet demand as sourcing requests, and points elsewhere generically.

**Architecture:** A new `sourcing_requests` table + repo, a request-scoped `request_context` (shop/customer_id) threaded from the verified App Proxy request through `dispatch` into the first **write** tool `flag_sourcing_request`, a `GET /api/sourcing-requests` read endpoint, and a `SYSTEM` persona rewrite. Identity is server-supplied; the model never sets it.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + PostgreSQL, Alembic, the existing Claude tool-use chat loop.

## Global Constraints

- **First write capability.** Capture happens ONLY through the agent tool (no public write endpoint). The storefront path is already App-Proxy-signature-gated.
- **Server-supplied identity.** `shop` and `customer_id` come from the verified proxy request via `request_context`; the model's `flag_sourcing_request` args for shop/customer_id are **ignored**. The model supplies only `item`, `context`, `email`.
- **Confirm-first.** The prompt only flags after the customer agrees; email is opt-in; always log the request regardless of email.
- **Two lanes in the persona:** catalog facts (products/protocols/SKUs/availability) come from tools, never invented; scientific knowledge is used freely for advice. Keep the existing anti-hallucination rules (incl. `protocols_by_material` grounding) and brevity rules.
- **"Where to buy":** name major suppliers generically (e.g. Sigma-Aldrich, Thermo Fisher) — NO fabricated competitor SKUs, NO purchase links, always after promoting Astor + offering sourcing.
- **DB reality:** local `alembic_version` is a phantom `0002_pack_size_text`, so `alembic upgrade head` does NOT run here. Create the new table in tests/dev via `Base.metadata.create_all(engine, tables=[SourcingRequest.__table__])`. The migration file is the artifact for clean deploys. DB-gated tests gate on `RUN_DB_TESTS=1`.
- **No new dependencies.** Non-goals: dashboard tile, notifications, competitor links, public write endpoint, product deep-linking (Move 3), rate-limiting.

---

### Task 1: `sourcing_requests` table + repo

**Files:**
- Modify: `src/astor/db/models.py`
- Create: `migrations/versions/0006_sourcing_requests.py`
- Modify: `src/astor/api/repo.py`
- Test: `tests/api/test_sourcing_requests_repo.py`

**Interfaces:**
- Produces: `SourcingRequest` model; `repo.create_sourcing_request(session, *, requested_item, context="", shop=None, customer_id=None, email=None) -> {"id","requested_item","status"}`; `repo.list_sourcing_requests(session, *, limit=50) -> list[{"id","requested_item","context","shop","customer_id","email","status","created_at"}]`.

- [ ] **Step 1: Write the failing DB-gated tests**

```python
# tests/api/test_sourcing_requests_repo.py
"""DB-gated. Run: RUN_DB_TESTS=1 pytest tests/api/test_sourcing_requests_repo.py"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="needs Postgres; set RUN_DB_TESTS=1"
)

from astor.api import repo
from astor.db.base import Base, engine, session_scope
from astor.db.models import SourcingRequest

TOKEN = "srq-" + uuid.uuid4().hex[:8]


def _ensure_table():
    Base.metadata.create_all(engine, tables=[SourcingRequest.__table__])


def _cleanup():
    with session_scope() as s:
        s.query(SourcingRequest).filter(
            SourcingRequest.requested_item.like(f"{TOKEN}%")
        ).delete(synchronize_session=False)


def test_create_and_list_roundtrip():
    _ensure_table()
    try:
        with session_scope() as s:
            r = repo.create_sourcing_request(
                s, requested_item=f"{TOKEN} Anti-FLAG antibody",
                context="western blot for FLAG tag", shop="astor-dev.myshopify.com",
                customer_id="cust-1", email="lab@uni.edu")
            assert r["status"] == "new"
            assert r["id"]
        with session_scope() as s:
            got = [i for i in repo.list_sourcing_requests(s, limit=50)
                   if TOKEN in i["requested_item"]]
            assert len(got) == 1
            g = got[0]
            assert g["shop"] == "astor-dev.myshopify.com"
            assert g["customer_id"] == "cust-1"
            assert g["email"] == "lab@uni.edu"
            assert g["status"] == "new"
            assert g["created_at"]  # timestamp populated
    finally:
        _cleanup()


def test_list_is_newest_first():
    _ensure_table()
    try:
        # separate transactions so each row gets a distinct now() (Postgres now() is
        # transaction-start time — same within one transaction).
        for i in range(3):
            with session_scope() as s:
                repo.create_sourcing_request(s, requested_item=f"{TOKEN} item {i}", context="")
        with session_scope() as s:
            got = [i for i in repo.list_sourcing_requests(s, limit=50)
                   if TOKEN in i["requested_item"]]
            assert got[0]["requested_item"].endswith("item 2")
            assert got[-1]["requested_item"].endswith("item 0")
    finally:
        _cleanup()


def test_optional_fields_null_when_absent():
    _ensure_table()
    try:
        with session_scope() as s:
            repo.create_sourcing_request(s, requested_item=f"{TOKEN} minimal", context="")
        with session_scope() as s:
            g = [i for i in repo.list_sourcing_requests(s)
                 if TOKEN in i["requested_item"]][0]
            assert g["shop"] is None and g["customer_id"] is None and g["email"] is None
    finally:
        _cleanup()
```

- [ ] **Step 2: Run to verify they fail**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_sourcing_requests_repo.py -q`
Expected: FAIL (`ImportError: cannot import name 'SourcingRequest'`).

- [ ] **Step 3: Implement the model**

In `src/astor/db/models.py`, add (after an existing model class; `String`, `Text`, `text`,
`Mapped`, `mapped_column`, `_uuid_pk`, `TimestampMixin`, `Base` are all already imported/defined):

```python
class SourcingRequest(Base, TimestampMixin):
    """Customer-confirmed demand for something Astor doesn't carry, captured by the chat's
    flag_sourcing_request tool. Identity (shop/customer_id) is server-supplied from the App
    Proxy request, never model-set. Written only via the agent — no public write endpoint."""

    __tablename__ = "sourcing_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    requested_item: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    shop: Mapped[str | None] = mapped_column(String(255))
    customer_id: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="new", server_default=text("'new'"))
```

(`default="new"` — a Python-side default — makes `row.status` readable immediately after
flush, so `create_sourcing_request` can return it without a refresh. `TimestampMixin`
supplies `created_at`/`updated_at`.)

- [ ] **Step 4: Implement the migration**

Create `migrations/versions/0006_sourcing_requests.py`:

```python
"""sourcing_requests: captured unmet demand from the chat. Additive.

Revision ID: 0006_sourcing_requests
Revises: 0005_protocol_material_links
Create Date: 2026-08-14 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_sourcing_requests"
down_revision = "0005_protocol_material_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sourcing_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("requested_item", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("shop", sa.String(length=255)),
        sa.Column("customer_id", sa.String(length=64)),
        sa.Column("email", sa.String(length=320)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'new'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sourcing_requests_created_at", "sourcing_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_sourcing_requests_created_at", table_name="sourcing_requests")
    op.drop_table("sourcing_requests")
```

- [ ] **Step 5: Implement the repo functions**

In `src/astor/api/repo.py`: add `SourcingRequest` to the `from astor.db.models import (...)`
block, then add:

```python
def create_sourcing_request(session, *, requested_item, context="", shop=None,
                            customer_id=None, email=None) -> dict:
    """Insert a captured sourcing request. Identity (shop/customer_id) is passed by the
    caller from the verified proxy request, not by the model."""
    row = SourcingRequest(
        requested_item=requested_item, context=context or "",
        shop=shop, customer_id=customer_id, email=email)
    session.add(row)
    session.flush()
    return {"id": str(row.id), "requested_item": row.requested_item, "status": row.status}


def list_sourcing_requests(session, *, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(SourcingRequest).order_by(SourcingRequest.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {"id": str(r.id), "requested_item": r.requested_item, "context": r.context,
         "shop": r.shop, "customer_id": r.customer_id, "email": r.email,
         "status": r.status, "created_at": r.created_at.isoformat()}
        for r in rows
    ]
```

- [ ] **Step 6: Run to verify they pass**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_sourcing_requests_repo.py -q`
Expected: PASS (3). Also `python -m pytest tests/api/test_sourcing_requests_repo.py -q` → 3 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/astor/db/models.py migrations/versions/0006_sourcing_requests.py src/astor/api/repo.py tests/api/test_sourcing_requests_repo.py
git commit -m "feat: sourcing_requests table + repo (captured demand)"
```

---

### Task 2: Thread `request_context` through the chat loop

**Files:**
- Modify: `src/astor/chat/tools.py` (all handler signatures + `dispatch`)
- Modify: `src/astor/chat/agent.py` (`run_chat`, `run_chat_stream`)
- Modify: `src/astor/api/shopify_proxy.py` (`verify_app_proxy` returns `customer_id`)
- Modify: `src/astor/api/routers/shopify_proxy.py` (`/proxy/chat` builds + passes `request_context`)
- Test: `tests/test_chat_agent.py` (add a forwarding test; update existing dispatch doubles)
- Test: `tests/test_chat_stream.py` (update the dispatch double)

**Interfaces:**
- Produces: `dispatch(session, name, args, request_context=None)`; every handler
  `(_search_products … _protocols_by_material)(session, args, request_context=None)`;
  `run_chat(session, messages, *, client=None, model=None, max_iters=6, request_context=None)`
  and same for `run_chat_stream`; `verify_app_proxy` returns `{"shop", "customer_id"}`.

- [ ] **Step 1: Write the failing forwarding test**

Append to `tests/test_chat_agent.py`:

```python
def test_run_chat_threads_request_context_into_dispatch(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    seen = {}
    def capture(session, name, args, request_context=None):
        seen["rc"] = request_context
        return ({}, [])
    monkeypatch.setattr(tools, "dispatch", capture)
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "search_products", {"query": "x"})]),
        _resp("end_turn", [_text_block("done")]),
    ])
    agent.run_chat(object(), [{"role": "user", "content": "x"}], client=client,
                   request_context={"shop": "astor-dev.myshopify.com", "customer_id": "c9"})
    assert seen["rc"] == {"shop": "astor-dev.myshopify.com", "customer_id": "c9"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_agent.py -k threads_request_context -q`
Expected: FAIL (`run_chat` has no `request_context` kwarg → TypeError).

- [ ] **Step 3: Implement the threading**

In `src/astor/chat/tools.py`:
- Change every handler signature from `def _name(session, args)` to
  `def _name(session, args, request_context=None)` — for all six: `_search_products`,
  `_search_protocols`, `_protocol_products`, `_product_protocols`, `_product_detail`,
  `_protocols_by_material`. Their bodies are unchanged (they ignore `request_context`).
- Change `dispatch`:

```python
def dispatch(session, name: str, args: dict, request_context: dict | None = None
             ) -> tuple[dict, list[ReferencedItem]]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}, []
    try:
        return handler(session, args, request_context)
    except Exception as exc:  # noqa: BLE001 — surface as recoverable tool error
        return {"error": f"{type(exc).__name__}: {exc}"}, []
```

In `src/astor/chat/agent.py`:
- Add `request_context: dict | None = None` to the keyword-only params of both `run_chat`
  and `run_chat_stream`.
- In each, change the dispatch call `tools.dispatch(session, block.name, block.input)` →
  `tools.dispatch(session, block.name, block.input, request_context)` (line 105 in `run_chat`,
  line 171 in `run_chat_stream`).

In `src/astor/api/shopify_proxy.py`, extend `verify_app_proxy`'s return:

```python
    return {"shop": request.query_params.get("shop"),
            "customer_id": request.query_params.get("logged_in_customer_id")}
```

In `src/astor/api/routers/shopify_proxy.py`, update the `/proxy/chat` handler to pass the
context (the `ctx` from `verify_app_proxy` already carries both keys):

```python
        reply = agent.run_chat(
            session, [m.model_dump() for m in body.messages],
            request_context={"shop": ctx["shop"], "customer_id": ctx["customer_id"]})
```

Leave `/api/chat` (`routers/chat.py`) unchanged — it calls `run_chat` without
`request_context`, so shop/customer_id default to None.

- [ ] **Step 4: Update the existing dispatch test-doubles**

The threading makes `dispatch` (and the handlers) take a trailing `request_context`. Update
every monkeypatched `tools.dispatch` double to accept it, or the loop's 4-arg call breaks:

- `tests/test_chat_agent.py:34` and `:54`: change `lambda s, name, args:` →
  `lambda s, name, args, request_context=None:`.
- `tests/test_chat_agent.py:67`: `lambda s, name, args: ({}, [])` →
  `lambda s, name, args, request_context=None: ({}, [])`.
- `tests/test_chat_agent.py:94` `def fake_dispatch(s, name, args):` →
  `def fake_dispatch(s, name, args, request_context=None):`.
- `tests/test_chat_stream.py:37` — open the file, find the `dispatch` function/lambda passed
  to `monkeypatch.setattr(tools, "dispatch", ...)` and add a trailing `request_context=None`
  parameter to its signature.

(The direct `tools.dispatch(_sess(), ...)` calls in `tests/test_chat_tools.py` need no change
— the real `dispatch` defaults `request_context=None`.)

- [ ] **Step 5: Run to verify pass + no regressions**

Run: `python -m pytest tests/test_chat_agent.py tests/test_chat_stream.py tests/test_chat_tools.py tests/api/test_chat.py tests/api/test_shopify_proxy_chat.py -q`
Expected: PASS (forwarding test green; all previously-green chat tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/astor/chat/tools.py src/astor/chat/agent.py src/astor/api/shopify_proxy.py src/astor/api/routers/shopify_proxy.py tests/test_chat_agent.py tests/test_chat_stream.py
git commit -m "refactor: thread request_context (shop/customer_id) through the chat dispatch"
```

---

### Task 3: `flag_sourcing_request` write tool

**Files:**
- Modify: `src/astor/chat/tools.py`
- Test: `tests/test_chat_tools.py` (append), `tests/test_chat_agent.py` (append)

**Interfaces:**
- Consumes: `repo.create_sourcing_request` (Task 1); `request_context` (Task 2).
- Produces: tool `flag_sourcing_request`, input `{item: str (required), context?: str, email?: str}`;
  returns `({"logged": True, "item", "status"}, [])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_tools.py`:

```python
def test_flag_sourcing_request_server_identity_wins(monkeypatch):
    captured = {}
    def fake_create(session, *, requested_item, context, shop, customer_id, email):
        captured.update(requested_item=requested_item, context=context, shop=shop,
                        customer_id=customer_id, email=email)
        return {"id": "1", "requested_item": requested_item, "status": "new"}
    monkeypatch.setattr(repo, "create_sourcing_request", fake_create)
    # model tries to spoof shop/customer_id in args; server request_context must win.
    result, items = tools.dispatch(
        _sess(), "flag_sourcing_request",
        {"item": "Anti-FLAG antibody", "context": "WB for FLAG", "email": "a@b.com",
         "shop": "EVIL", "customer_id": "EVIL"},
        request_context={"shop": "astor-dev.myshopify.com", "customer_id": "cust-9"})
    assert result == {"logged": True, "item": "Anti-FLAG antibody", "status": "new"}
    assert items == []
    assert captured["shop"] == "astor-dev.myshopify.com"   # not "EVIL"
    assert captured["customer_id"] == "cust-9"              # not "EVIL"
    assert captured["email"] == "a@b.com"
    assert captured["context"] == "WB for FLAG"


def test_flag_sourcing_request_demo_path_null_identity(monkeypatch):
    captured = {}
    def fake_create(session, *, requested_item, context, shop, customer_id, email):
        captured.update(shop=shop, customer_id=customer_id, email=email)
        return {"id": "1", "requested_item": requested_item, "status": "new"}
    monkeypatch.setattr(repo, "create_sourcing_request", fake_create)
    result, items = tools.dispatch(_sess(), "flag_sourcing_request", {"item": "X"})  # no request_context
    assert result["logged"] is True
    assert captured["shop"] is None and captured["customer_id"] is None and captured["email"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_chat_tools.py -k flag_sourcing -q`
Expected: FAIL (`unknown tool 'flag_sourcing_request'`).

- [ ] **Step 3: Implement the tool**

In `src/astor/chat/tools.py`, add the handler:

```python
def _flag_sourcing_request(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    """WRITE: capture a customer-confirmed request for something Astor doesn't carry.
    Identity (shop/customer_id) is taken from the server-supplied request_context, NEVER
    from the model's args."""
    rc = request_context or {}
    r = repo.create_sourcing_request(
        session,
        requested_item=args["item"],
        context=args.get("context", ""),
        shop=rc.get("shop"),
        customer_id=rc.get("customer_id"),
        email=args.get("email"),
    )
    return {"logged": True, "item": r["requested_item"], "status": r["status"]}, []
```

Register in `_HANDLERS`:

```python
    "flag_sourcing_request": _flag_sourcing_request,
```

Add to `TOOL_SCHEMAS`:

```python
    {"name": "flag_sourcing_request",
     "description": "Log a customer-confirmed request for a product/reagent Astor does NOT "
                    "currently carry, so the team can look into sourcing it. Call this ONLY "
                    "after the customer agrees to be flagged. Provide `item` (what they want) "
                    "and `context` (their need in brief); include `email` only if the customer "
                    "offers one for follow-up. Do NOT pass shop or customer identity — the "
                    "server attaches that.",
     "input_schema": {"type": "object",
                      "properties": {"item": {"type": "string"},
                                     "context": {"type": "string"},
                                     "email": {"type": "string"}},
                      "required": ["item"]}},
```

- [ ] **Step 4: Run the tool tests**

Run: `python -m pytest tests/test_chat_tools.py -k flag_sourcing -q`
Expected: PASS.

- [ ] **Step 5: Write + run the agent-loop test**

Append to `tests/test_chat_agent.py` (reuse `_FakeClient`/`_tool_block`/`_resp`; monkeypatch
the repo so no DB is needed):

```python
def test_agent_loop_flags_sourcing_with_server_identity(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    from astor.api import repo
    captured = {}
    monkeypatch.setattr(repo, "create_sourcing_request",
        lambda s, *, requested_item, context, shop, customer_id, email: (
            captured.update(item=requested_item, shop=shop, customer_id=customer_id)
            or {"id": "1", "requested_item": requested_item, "status": "new"}))
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "flag_sourcing_request",
                                       {"item": "Anti-FLAG antibody", "context": "WB"})]),
        _resp("end_turn", [_text_block("Logged — we'll look into sourcing it.")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "can you get anti-FLAG?"}],
                         client=client,
                         request_context={"shop": "astor-dev.myshopify.com", "customer_id": "c9"})
    assert captured["item"] == "Anti-FLAG antibody"
    assert captured["shop"] == "astor-dev.myshopify.com"
    assert captured["customer_id"] == "c9"
    assert out.reply.startswith("Logged")
```

Run: `python -m pytest tests/test_chat_agent.py -k flags_sourcing -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/astor/chat/tools.py tests/test_chat_tools.py tests/test_chat_agent.py
git commit -m "feat: flag_sourcing_request write tool (server-supplied identity)"
```

---

### Task 4: `GET /api/sourcing-requests` read endpoint

**Files:**
- Modify: `src/astor/api/routers/dashboard.py`
- Test: `tests/api/test_sourcing_requests_endpoint.py`

**Interfaces:**
- Consumes: `repo.list_sourcing_requests(session, *, limit)` (Task 1).
- Produces: `GET /api/sourcing-requests?limit=` → `{"items": [...], "count": n}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_sourcing_requests_endpoint.py
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, fn):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "list_sourcing_requests", fn)
    return TestClient(app)


def test_lists_requests(monkeypatch):
    def fake(session, *, limit):
        assert limit == 50
        return [{"id": "1", "requested_item": "Anti-FLAG antibody", "context": "WB",
                 "shop": "astor-dev.myshopify.com", "customer_id": "c9",
                 "email": None, "status": "new", "created_at": "2026-08-14T00:00:00+00:00"}]
    resp = _client(monkeypatch, fake).get("/api/sourcing-requests")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["items"][0]["requested_item"] == "Anti-FLAG antibody"


def test_limit_capped_at_200(monkeypatch):
    seen = {}
    def fake(session, *, limit):
        seen["limit"] = limit
        return []
    _client(monkeypatch, fake).get("/api/sourcing-requests?limit=9999")
    assert seen["limit"] == 200
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/api/test_sourcing_requests_endpoint.py -q`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Implement**

In `src/astor/api/routers/dashboard.py`, add `Query` to the fastapi import
(`from fastapi import APIRouter, Depends, Query`) and add the route:

```python
@router.get("/sourcing-requests")
def sourcing_requests(
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    """Captured sourcing requests for the team, newest first."""
    items = repo.list_sourcing_requests(session, limit=min(limit, 200))
    return {"items": items, "count": len(items)}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/api/test_sourcing_requests_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/dashboard.py tests/api/test_sourcing_requests_endpoint.py
git commit -m "feat: GET /api/sourcing-requests read endpoint"
```

---

### Task 5: Persona reshape — `SYSTEM` prompt

**Files:**
- Modify: `src/astor/chat/agent.py`

**Interfaces:** none new — replaces the `SYSTEM` string. Must keep the tool names referenced
(`protocols_by_material`, `flag_sourcing_request`) exactly.

- [ ] **Step 1: Replace the `SYSTEM` string**

In `src/astor/chat/agent.py`, replace the entire `SYSTEM = ( ... )` assignment with:

```python
SYSTEM = (
    "You are Astor Scientific's lab assistant — a knowledgeable procurement partner, not a "
    "search box. Customers describe experiments or product needs; you advise like a lab "
    "colleague AND connect them to what Astor sells.\n\n"
    "TWO LANES:\n"
    "- CATALOG FACTS (which products/protocols exist, SKUs, what Astor carries, availability): "
    "use the tools. NEVER invent a product, SKU, or protocol — only cite ones a tool returned "
    "this turn.\n"
    "- SCIENTIFIC KNOWLEDGE (how a technique works, buffer/reagent choices, troubleshooting, "
    "experimental design): use your own expertise freely to actually help. Answer the science "
    "even when the exact item isn't in the catalog.\n\n"
    "PROMOTE -> SOURCE -> POINT (every request):\n"
    "1. PROMOTE: search the catalog first; if Astor carries it, lead with that product/protocol.\n"
    "2. SOURCE: if we don't carry it, still answer the science, then offer to flag it — e.g. "
    "'We don't stock that yet — want our team to look into sourcing it?' Only after the "
    "customer agrees, call flag_sourcing_request with `item` and a brief `context`. If they "
    "offer an email for follow-up, pass it as `email`; never require it. Do NOT pass shop or "
    "customer identity — the server attaches that. Never flag without the customer's yes.\n"
    "3. POINT: only if they ask where else to get it, you may name major suppliers generically "
    "(e.g. 'the big suppliers like Sigma-Aldrich or Thermo Fisher usually carry this'). Never "
    "invent a specific competitor SKU or link, and keep this secondary to promoting Astor and "
    "offering to source it.\n\n"
    "GROUNDING SPECIFICS:\n"
    "- For a 'which/what protocols use|need|require <material or reagent>' question, call "
    "protocols_by_material with the reagent's core name and lead with the count it returns; "
    "if it returns 0, say plainly none in the catalog list it — never guess which protocols "
    "use it from general knowledge.\n"
    "- A technique for a SPECIFIC target (e.g. 'Western blot for phospho-ERK') is the standard "
    "technique plus a target-specific reagent — present the general protocol confidently and "
    "note the reagent to add; offer to find it. Do not say 'no protocol exists'.\n\n"
    "STYLE — keep it tight but warm:\n"
    "- Lead with a direct answer; aim for 2-5 sentences; never a wall of text.\n"
    "- Recommend the single best-matching product/protocol, not a list. The UI renders "
    "clickable cards for what you reference — name at most 1-3 items and let the cards carry "
    "the rest; never paste ids or long bulleted dumps.\n"
    "- End by moving the conversation forward (a next step or a focused question) — don't dead-end "
    "with 'search elsewhere'. If the request is genuinely vague, ask ONE clarifying question."
)
```

- [ ] **Step 2: Run to verify no regression**

Run: `python -m pytest tests/test_chat_agent.py tests/api/test_chat.py -q`
Expected: PASS — the prompt is a plain string swap; the agent-loop tests (which inject a
fake client) don't depend on the prompt text, so they stay green.

- [ ] **Step 3: Commit**

```bash
git add src/astor/chat/agent.py
git commit -m "feat: promote-source-point persona (advise freely, ground catalog facts)"
```

---

### Task 6: Full-suite regression

**Files:**
- Test: full `pytest`, both gates

- [ ] **Step 1: Offline suite**

Run: `python -m pytest -q`
Expected: PASS; the new DB-gated repo test collected as skipped; no regressions across the
chat/tool/endpoint tests.

- [ ] **Step 2: DB-gated repo test**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_sourcing_requests_repo.py -q`
Expected: PASS (3) — the table is created via `Base.metadata.create_all` and the create/list
roundtrip works against real Postgres.

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: promote-source-point suite green"
```

---

## Self-Review

**Spec coverage:**
- `sourcing_requests` model + migration + repo create/list → Task 1. ✓
- `request_context` threading (dispatch + all handlers + run_chat/run_chat_stream; `/proxy/chat`
  fills shop+customer_id via `verify_app_proxy`; `/api/chat` passes none) → Task 2. ✓
- `flag_sourcing_request` write tool, model supplies item/context/email, server identity
  overrides model, ack + no items, registered → Task 3. ✓
- `GET /api/sourcing-requests` (default 50, cap 200) → Task 4. ✓
- Persona reshape (two lanes, promote→source→point, confirm-first, optional email, generic
  suppliers, softened dead-end, kept anti-hallucination + brevity) → Task 5. ✓
- Offline tests repo(DB-gated)/tool(identity-override)/agent-loop/endpoint → Tasks 1,3,4. ✓
- Non-goals (no dashboard tile / notifications / competitor links / public write / deep-link /
  rate-limit) → nothing in the plan adds them. ✓

**Placeholder scan:** none — every code step is complete. Task 2 Step 4 names the exact test
doubles to update (grep-confirmed lines) rather than hand-waving "fix the tests".

**Type consistency:** `create_sourcing_request(*, requested_item, context, shop, customer_id,
email)` and its `{id,requested_item,status}` return are consumed identically by the tool
(Task 3) and asserted the same in every test; `list_sourcing_requests` `{...,created_at}`
shape matches the endpoint test (Task 4) and repo test (Task 1); `dispatch(session, name,
args, request_context=None)` signature is defined in Task 2 and used by Task 3's tests and
the forwarding test; `verify_app_proxy` now returns `{"shop","customer_id"}`, consumed by
`/proxy/chat` in Task 2. Tool name `flag_sourcing_request` + arg key `item` consistent across
handler, schema, and all tests.

**Verified against code:** `TimestampMixin`/`_uuid_pk`/`Base` + `String`/`Text`/`text`
imports (`db/models.py`); migration head `0005_protocol_material_links` for `down_revision`
(`migrations/versions/`); phantom `alembic_version=0002_pack_size_text` → `create_all` path;
handler signatures + `dispatch` + the 5 `tools.dispatch` test doubles (grep-enumerated);
`run_chat`/`run_chat_stream` dispatch call sites (agent.py:105,171); `verify_app_proxy` return
(shopify_proxy.py); dashboard router pattern (`routers/dashboard.py`); tool/endpoint/agent
test patterns (`tests/test_chat_tools.py`, `tests/test_chat_agent.py`,
`tests/api/test_protocols_list.py`).

**One caveat for the implementer:** Postgres `now()` is transaction-start time, constant
within a transaction — so the newest-first repo test inserts its three rows in **separate**
`session_scope()` blocks to get distinct timestamps; don't collapse them into one transaction
or the ordering assertion becomes non-deterministic. And do not run `alembic upgrade head`
locally (the phantom revision breaks the chain) — the migration is the deploy artifact; tests
create the table via `Base.metadata.create_all`.
