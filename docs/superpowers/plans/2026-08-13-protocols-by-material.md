# Protocols-by-material Reverse Search (Move 2-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the assistant answer "which protocols use material X" from real data — a lexical reverse search over each protocol's extracted materials — instead of guessing.

**Architecture:** One new repo query (`protocols_by_material`) doing a normalized lexical match over the `protocols.materials` jsonb, exposed the app's usual three ways (repo → endpoint → agent tool), plus a `SYSTEM` prompt change so the agent uses it and stays honest when it's empty.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy + PostgreSQL jsonb (`jsonb_array_elements`, `regexp_replace`), the existing Claude tool-use chat loop.

## Global Constraints

- **Lexical only.** Substring match, no embeddings/semantics (that's Move 2-B). No change to `search_protocols` (title search stays; the new tool is additive).
- **Normalize both sides identically:** lowercase, collapse runs of `-`, `/`, and whitespace to a single space, strip ends. Query term and each material name get the same treatment.
- **`servable` protocols only.** Match when ANY element of `materials` has a normalized `name` containing the normalized term.
- **Order:** `rank_score` desc, then `product_count` desc (same ranking as `list_protocols`).
- **Return shape (exact):** `{"total": <int>, "protocols": [{"id": str, "title": str, "product_count": int, "matched_material": str}, ...]}`. `total` = full count of matching servable protocols; `protocols` is capped at `limit`. `matched_material` = the original (un-normalized) name of the first material that matched.
- **Blank/whitespace term (after normalization) → `{"total": 0, "protocols": []}`; never raises.**
- **No new dependencies.** No change to material→SKU linking, the 827 `protocol_material_links`, or the matcher.
- DB-touching repo tests gate on `RUN_DB_TESTS=1` (skipped otherwise), matching `tests/api/test_integration_smoke.py`. Router/tool/agent tests are offline (monkeypatch `repo`, `TestClient`).

---

### Task 1: `repo.protocols_by_material`

**Files:**
- Modify: `src/astor/api/repo.py`
- Test: `tests/api/test_protocols_by_material_repo.py`

**Interfaces:**
- Consumes: `Protocol` (has `id, title, materials (jsonb list), rank_score, servable`), `ProtocolMaterialLink` (for the `product_count` correlated count) — both already imported in `repo.py`. `session_scope` from `astor.db.base`.
- Produces: `protocols_by_material(session, material: str, *, limit: int = 10) -> dict` returning `{"total": int, "protocols": [{"id": str, "title": str, "product_count": int, "matched_material": str}]}`.

- [ ] **Step 1: Write the failing DB-gated test**

```python
# tests/api/test_protocols_by_material_repo.py
"""DB-gated: exercises the jsonb reverse search against real Postgres.
Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_protocols_by_material_repo.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from astor.api import repo
from astor.db.base import session_scope
from astor.db.models import Protocol

MARK = "pbm-test-" + uuid.uuid4().hex[:8]  # unique source tag → find + clean up


def _p(title, materials, *, servable=True, rank=0.0):
    return Protocol(
        source=MARK, source_id=uuid.uuid4().hex, source_uri="u", title=title,
        license="cc-by", servable=servable, rank_score=rank, materials=materials, steps=[],
    )


def test_finds_only_servable_protocols_that_list_the_material():
    with session_scope() as s:
        try:
            s.add_all([
                _p("Fibroblast culture", [{"name": "0.25% Trypsin-EDTA(1x) in HBSS - 100 ML"}], rank=5.0),
                _p("Cell passaging", [{"name": "Trypsin-EDTA solution (Gibco, #25200056)"}], rank=9.0),
                _p("PCR cleanup", [{"name": "Ethanol"}, {"name": "SPRI beads"}], rank=8.0),
                _p("Hidden trypsin", [{"name": "Trypsin-EDTA"}], servable=False, rank=9.9),
            ])
            s.flush()
            r = repo.protocols_by_material(s, "Trypsin-EDTA")
            titles = [p["title"] for p in r["protocols"]]
            assert r["total"] == 2
            # rank order: 9.0 before 5.0; non-servable + non-matching excluded
            assert titles == ["Cell passaging", "Fibroblast culture"]
            assert r["protocols"][0]["matched_material"] == "Trypsin-EDTA solution (Gibco, #25200056)"
            assert all("id" in p and "product_count" in p for p in r["protocols"])
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_normalization_matches_hyphen_space_slash_and_case():
    with session_scope() as s:
        try:
            s.add_all([
                _p("A", [{"name": "Trypsin/EDTA Solution"}], rank=1.0),
                _p("B", [{"name": "TRYPSIN   EDTA (1x)"}], rank=2.0),
            ])
            s.flush()
            # query with a hyphen; both slash and multi-space variants must match
            r = repo.protocols_by_material(s, "trypsin-edta")
            assert r["total"] == 2
            assert {p["title"] for p in r["protocols"]} == {"A", "B"}
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_limit_caps_rows_but_total_is_full_count():
    with session_scope() as s:
        try:
            s.add_all([_p(f"P{i}", [{"name": "Trypsin-EDTA"}], rank=float(i)) for i in range(5)])
            s.flush()
            r = repo.protocols_by_material(s, "trypsin-edta", limit=2)
            assert r["total"] == 5
            assert len(r["protocols"]) == 2
            assert [p["title"] for p in r["protocols"]] == ["P4", "P3"]  # highest rank first
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_blank_term_returns_empty_without_querying():
    with session_scope() as s:
        assert repo.protocols_by_material(s, "   ") == {"total": 0, "protocols": []}
        assert repo.protocols_by_material(s, "") == {"total": 0, "protocols": []}
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_protocols_by_material_repo.py -q`
Expected: FAIL (`AttributeError: module 'astor.api.repo' has no attribute 'protocols_by_material'`).

- [ ] **Step 3: Implement**

In `src/astor/api/repo.py`, add `import re` to the top imports and `from sqlalchemy import text` (merge into the existing `from sqlalchemy import func, select` → `from sqlalchemy import func, select, text`). Then add this function (place it near `list_protocols`):

```python
def _normalize_material(term: str) -> str:
    """Lowercase and collapse runs of hyphen/slash/whitespace to a single space."""
    return re.sub(r"[-/\s]+", " ", (term or "").lower()).strip()


def protocols_by_material(session, material: str, *, limit: int = 10) -> dict:
    """Servable protocols whose materials list a reagent matching `material`.

    Lexical reverse search: normalize the term and each material name (lowercase;
    collapse -,/,whitespace to a single space) and substring-match. Ordered by review
    rank then catalog-connectedness. `total` is the full match count; `protocols` is
    capped at `limit`. Blank term → empty, never raises.
    """
    norm = _normalize_material(material)
    if not norm:
        return {"total": 0, "protocols": []}

    # Correlated EXISTS over the jsonb materials array, normalizing each element name
    # the same way as the term. `protocols` is the Protocol table name.
    pred = text(
        "EXISTS (SELECT 1 FROM jsonb_array_elements(protocols.materials) AS elem "
        "WHERE regexp_replace(lower(elem->>'name'), '[-/[:space:]]+', ' ', 'g') "
        "LIKE :pat)"
    ).bindparams(pat=f"%{norm}%")

    link_count = (
        select(func.count(ProtocolMaterialLink.id))
        .where(ProtocolMaterialLink.protocol_id == Protocol.id)
        .correlate(Protocol)
        .scalar_subquery()
    )

    total = session.scalar(
        select(func.count(Protocol.id)).where(Protocol.servable.is_(True)).where(pred)
    ) or 0

    rows = session.execute(
        select(Protocol.id, Protocol.title, Protocol.materials, link_count.label("pc"))
        .where(Protocol.servable.is_(True)).where(pred)
        .order_by(Protocol.rank_score.desc(), link_count.desc())
        .limit(limit)
    ).all()

    protocols = []
    for pid, title, materials, pc in rows:
        matched = next(
            (m.get("name") for m in (materials or [])
             if norm in _normalize_material(m.get("name") or "")),
            "",
        )
        protocols.append({"id": str(pid), "title": title,
                          "product_count": int(pc), "matched_material": matched})
    return {"total": int(total), "protocols": protocols}
```

- [ ] **Step 4: Run to verify it passes**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_protocols_by_material_repo.py -q`
Expected: PASS (4 tests). Also confirm the normal suite still collects it as skipped:
`python -m pytest tests/api/test_protocols_by_material_repo.py -q` → 4 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/repo.py tests/api/test_protocols_by_material_repo.py
git commit -m "feat: repo.protocols_by_material — lexical reverse search over materials jsonb"
```

---

### Task 2: `GET /api/protocols/by-material` endpoint

**Files:**
- Modify: `src/astor/api/routers/protocols.py`
- Test: `tests/api/test_protocols_by_material_endpoint.py`

**Interfaces:**
- Consumes: `repo.protocols_by_material(session, material, *, limit)` (Task 1).
- Produces: `GET /api/protocols/by-material?q=<term>&limit=<n>` → the repo payload `{"total", "protocols":[...]}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_protocols_by_material_endpoint.py
from fastapi.testclient import TestClient

from astor.api import repo
from astor.api.deps import get_session
from astor.api.main import create_app


def _client(monkeypatch, fn):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    monkeypatch.setattr(repo, "protocols_by_material", fn)
    return TestClient(app)


def test_returns_payload(monkeypatch):
    def fake(session, material, *, limit):
        assert material == "trypsin" and limit == 10
        return {"total": 2, "protocols": [
            {"id": "p1", "title": "Cell passaging", "product_count": 3, "matched_material": "Trypsin-EDTA"}]}
    resp = _client(monkeypatch, fake).get("/api/protocols/by-material?q=trypsin")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert resp.json()["protocols"][0]["matched_material"] == "Trypsin-EDTA"


def test_limit_is_capped_at_50(monkeypatch):
    seen = {}
    def fake(session, material, *, limit):
        seen["limit"] = limit
        return {"total": 0, "protocols": []}
    _client(monkeypatch, fake).get("/api/protocols/by-material?q=x&limit=999")
    assert seen["limit"] == 50


def test_missing_q_returns_empty_payload(monkeypatch):
    called = {"n": 0}
    def fake(session, material, *, limit):
        called["n"] += 1
        return {"total": 0, "protocols": []}
    resp = _client(monkeypatch, fake).get("/api/protocols/by-material")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "protocols": []}
    assert called["n"] == 0  # short-circuits, never calls repo with an empty term
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/api/test_protocols_by_material_endpoint.py -q`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Implement**

In `src/astor/api/routers/protocols.py`, add the route (after `list_protocols`). `Query` is already imported:

```python
@router.get("/protocols/by-material")
def protocols_by_material(
    q: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
) -> dict:
    """Protocols that USE a given material/reagent (reverse lookup over material lists)."""
    if not q or not q.strip():
        return {"total": 0, "protocols": []}
    return repo.protocols_by_material(session, q, limit=limit)
```

Note: this route must be declared **before** `GET /protocols/{protocol_id}/materials` is not a concern (different path), but it MUST NOT be shadowed by a `/protocols/{protocol_id}` route — there is none, so `/protocols/by-material` is unambiguous. Keep it adjacent to `list_protocols`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/api/test_protocols_by_material_endpoint.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/astor/api/routers/protocols.py tests/api/test_protocols_by_material_endpoint.py
git commit -m "feat: GET /api/protocols/by-material endpoint"
```

---

### Task 3: Agent tool + prompt nudge

**Files:**
- Modify: `src/astor/chat/tools.py`
- Modify: `src/astor/chat/agent.py`
- Test: `tests/test_chat_tools.py` (append), `tests/test_chat_agent.py` (append an agent-loop wiring test)

**Interfaces:**
- Consumes: `repo.protocols_by_material` (Task 1), `ReferencedItem`, `dispatch`, `TOOL_SCHEMAS` (existing).
- Produces: tool `protocols_by_material` with input `{material: str, limit?: int}`, dispatched to a handler that returns the repo payload + one `ReferencedItem("protocol", id, title)` per result.

- [ ] **Step 1: Write the failing tool test**

Append to `tests/test_chat_tools.py`:

```python
def test_protocols_by_material_refs_protocols(monkeypatch):
    monkeypatch.setattr(repo, "protocols_by_material",
        lambda s, material, *, limit: {
            "total": 2,
            "protocols": [
                {"id": "x1", "title": "Cell passaging", "product_count": 3, "matched_material": "Trypsin-EDTA"},
                {"id": "x2", "title": "Fibroblast culture", "product_count": 1, "matched_material": "0.25% Trypsin-EDTA"},
            ]} if material == "Trypsin-EDTA" else {"total": 0, "protocols": []})
    result, items = tools.dispatch(_sess(), "protocols_by_material", {"material": "Trypsin-EDTA"})
    assert result["total"] == 2
    assert result["protocols"][0]["title"] == "Cell passaging"
    assert items == [
        tools.ReferencedItem("protocol", "x1", "Cell passaging"),
        tools.ReferencedItem("protocol", "x2", "Fibroblast culture"),
    ]


def test_protocols_by_material_empty(monkeypatch):
    monkeypatch.setattr(repo, "protocols_by_material",
        lambda s, material, *, limit: {"total": 0, "protocols": []})
    result, items = tools.dispatch(_sess(), "protocols_by_material", {"material": "unobtanium"})
    assert result == {"total": 0, "protocols": []}
    assert items == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_chat_tools.py -k protocols_by_material -q`
Expected: FAIL (`unknown tool 'protocols_by_material'` → result has `error`, assertion fails).

- [ ] **Step 3: Implement the tool**

In `src/astor/chat/tools.py`, add the handler (near the other `_...` handlers):

```python
def _protocols_by_material(session, args) -> tuple[dict, list[ReferencedItem]]:
    r = repo.protocols_by_material(session, args["material"], limit=int(args.get("limit") or 10))
    items = [ReferencedItem("protocol", p["id"], p["title"]) for p in r["protocols"]]
    return r, items
```

Register in `_HANDLERS`:

```python
    "protocols_by_material": _protocols_by_material,
```

Add to `TOOL_SCHEMAS`:

```python
    {"name": "protocols_by_material",
     "description": "Find protocols that USE a given lab material/reagent by name (reverse "
                    "lookup over each protocol's material list). Use this for 'which protocols "
                    "use X' / 'what protocols need X' questions. Returns a total count and the "
                    "top matches; each match includes the material text that matched.",
     "input_schema": {"type": "object",
                      "properties": {"material": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["material"]}},
```

- [ ] **Step 4: Run the tool tests**

Run: `python -m pytest tests/test_chat_tools.py -k protocols_by_material -q`
Expected: PASS.

- [ ] **Step 5: Add the prompt nudge**

In `src/astor/chat/agent.py`, extend the `SYSTEM` GROUNDING block. Add these bullets after the existing "Decide freely whether to go protocol-first or product-first." line (keep every existing brevity rule unchanged):

```python
    "- For a 'which/what protocols use|need|require <material or reagent>' question, call "
    "protocols_by_material with the reagent's core name (e.g. 'Trypsin-EDTA'). Lead with the "
    "count it returns ('30 protocols use Trypsin-EDTA') and name the single best match; the "
    "cards carry the rest.\n"
    "- If protocols_by_material returns total 0 (or an empty list), say plainly that no "
    "protocols in the catalog list that material. NEVER infer or guess which protocols use a "
    "material from general knowledge.\n"
```

- [ ] **Step 6: Write + run the agent-loop wiring test**

Append to `tests/test_chat_agent.py`. That file already defines the fake-client helpers at its top — `_text_block`, `_tool_block`, `_resp`, and `_FakeClient` — and imports `agent`, `tools`, `ReferencedItem`. Reuse them exactly (do NOT redefine them, do NOT make a real API call):

```python
def test_agent_loop_uses_protocols_by_material(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    calls = []
    def fake_dispatch(s, name, args):
        calls.append(name)
        if name == "protocols_by_material":
            return ({"total": 1, "protocols": [{"id": "x1", "title": "Cell passaging",
                     "product_count": 2, "matched_material": "Trypsin-EDTA"}]},
                    [ReferencedItem("protocol", "x1", "Cell passaging")])
        return ({}, [])
    monkeypatch.setattr(tools, "dispatch", fake_dispatch)

    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "protocols_by_material", {"material": "Trypsin-EDTA"})]),
        _resp("end_turn", [_text_block("30 protocols use Trypsin-EDTA. Best match: Cell passaging.")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "what protocols use trypsin-edta?"}],
                         client=client)
    assert "protocols_by_material" in calls
    assert out.items == [ReferencedItem("protocol", "x1", "Cell passaging")]
```

Run: `python -m pytest tests/test_chat_agent.py -k protocols_by_material -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/astor/chat/tools.py src/astor/chat/agent.py tests/test_chat_tools.py tests/test_chat_agent.py
git commit -m "feat: protocols_by_material chat tool + grounded prompt for reverse-material questions"
```

---

### Task 4: Full-suite regression

**Files:**
- Test: full `pytest`, both with and without the DB gate

- [ ] **Step 1: Offline suite (DB test skipped)**

Run: `python -m pytest -q`
Expected: PASS, with the new repo test collected as skipped; no regressions.

- [ ] **Step 2: DB-gated repo test against local Postgres**

Run: `RUN_DB_TESTS=1 python -m pytest tests/api/test_protocols_by_material_repo.py -q`
Expected: PASS (4 tests) — the jsonb reverse search works against real Postgres.

- [ ] **Step 3: Commit (only if a fix was needed)**

```bash
git commit -am "test: protocols-by-material suite green"
```

---

## Self-Review

**Spec coverage:**
- `protocols_by_material` repo: normalized lexical match over `materials` jsonb, servable-only, EXISTS over `jsonb_array_elements`, rank order, `{total, protocols:[{id,title,product_count,matched_material}]}`, blank→empty → Task 1. ✓
- `GET /api/protocols/by-material?q=&limit=` (default 10, cap 50, missing q → empty 200) → Task 2. ✓
- Agent tool `protocols_by_material` (dispatch + TOOL_SCHEMAS, protocol ReferencedItems) → Task 3. ✓
- SYSTEM prompt nudge (use it for which-protocols-use-X; empty → say so, never guess) → Task 3. ✓
- Offline tests at endpoint/tool/agent-loop + DB-gated repo test → Tasks 1–3. ✓
- Non-goals (semantic, matcher/links untouched, no browse UI) → nothing in the plan touches them. ✓

**Placeholder scan:** none — every code step is complete. Task 3 Step 6 tells the implementer to follow `test_chat.py`'s existing fake-client construction rather than inventing an API; that file is the source of truth for the pattern (verified it uses fake clients, no network).

**Type consistency:** repo returns `{"total", "protocols":[{"id","title","product_count","matched_material"}]}` — consumed identically by the endpoint (Task 2), the tool handler (Task 3, iterates `p["id"]`/`p["title"]`), and asserted the same way in every test. Tool name `protocols_by_material` and input key `material` match across `_HANDLERS`, `TOOL_SCHEMAS`, dispatch tests, and the agent-loop test. `_normalize_material` is used on both the term and each material name (Task 1), so the tests' hyphen/slash/space variants match.

**Verified against code:** `list_protocols` uses the same `link_count` correlated subquery + `rank_score` ordering (`repo.py`); `Protocol` columns `materials (jsonb)`, `rank_score`, `servable`, `title`, required `source/source_id/source_uri/license` (`db/models.py`); DB-gate pattern `RUN_DB_TESTS=1` + `session_scope` (`tests/api/test_integration_smoke.py`); tool test pattern monkeypatches `repo` + `tools.dispatch` (`tests/test_chat_tools.py`); router test pattern `dependency_overrides[get_session]` + `TestClient` (`tests/api/test_protocols_list.py`).

**One caveat for the implementer:** the repo query is Postgres-specific (`jsonb_array_elements`, `regexp_replace` with the POSIX `[:space:]` class) — it cannot run on SQLite, which is why its test is DB-gated. The offline layers (endpoint, tool, agent) all monkeypatch `repo.protocols_by_material`, so the full suite stays green without a database; the real query is proven only under `RUN_DB_TESTS=1`. Run that locally before merge.
