# protocols.io Harvester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a run-ready, offline-tested harvester that discovers, review-ranks, fetches, persists, licence-gates and ranks the top-N protocols.io protocols per curated category — bulk-runnable only when two explicit locks are cleared.

**Architecture:** Add a `search()` (v3 list endpoint) method to the existing `ProtocolsIoSource` adapter, both network methods double-locked behind `allow_network` + a licensed flag. A new `harvest.py` orchestrator drives discover → shortlist → fetch → persist-json → map → gate → rank as discrete pure-where-possible stages, mirroring the existing `ingestion.py` shape. Every fetched payload is written to disk (`data/raw/protocols_io/`) so re-ranking never re-fetches, and a run manifest records the authorising `serving_basis`.

**Tech Stack:** Python 3.11, pydantic v2 (`RawProtocol` DTOs), httpx (already the client in `sources.py`), pytest. No new dependencies.

## Global Constraints

- **No new dependencies.** Use httpx + stdlib only, matching `sources.py`.
- **Double-lock every network path.** `search()` and bulk `fetch` require BOTH `allow_network=True` AND `settings.protocols_io_licensed` truthy. `fetch_one` keeps its existing single-lock (`allow_network` only) — do not change it.
- **Fail closed on licence.** The protocols.io API exposes no licence field; every record maps to `License.UNKNOWN`. `serving_basis=None` ⇒ everything is link-out. Never modify `DEFAULT_SERVE_LICENSES` or the default `license_gate` behaviour.
- **`≤ 1,000` records per run**, per the protocols.io approval email. The orchestrator enforces a hard cap and refuses to exceed it.
- **Endpoint versions are fixed:** list/search = v3 (`LIST_BASE`), detail = v4 (`BASE`). Do not "unify" them.
- **Persist before transform.** Raw JSON is written to disk before mapping; re-ranking/mapping changes must never trigger a network fetch.
- **Batch, don't one-shot.** Sequential requests with a throttle (`sleep_between`), default ≥ 1.0s, following the `--sleep` pattern in `scripts/rebuild_map.py`.
- **Idempotent.** Re-running skips ids already persisted at the same `version_id`.

---

### Task 1: `protocols_io_licensed` config flag + `sweepable` source attribute

**Files:**
- Modify: `src/astor/config.py:34` (after `protocols_io_token`)
- Modify: `src/astor/protocols/sources.py` (class attrs on both sources; `run_from_search` invariant lives in Task 6)
- Test: `tests/test_protocols.py`

**Interfaces:**
- Produces: `settings.protocols_io_licensed: bool` (default `False`); `ProtocolsIoSource.sweepable: bool = False`; `EuropePmcSource.sweepable: bool = True`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def test_sources_declare_sweepability():
    assert ProtocolsIoSource.sweepable is False
    assert EuropePmcSource.sweepable is True

def test_licensed_flag_defaults_false():
    from astor.config import settings
    assert settings.protocols_io_licensed is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py::test_sources_declare_sweepability tests/test_protocols.py::test_licensed_flag_defaults_false -v`
Expected: FAIL (`AttributeError: sweepable` / `protocols_io_licensed`)

- [ ] **Step 3: Implement**

In `src/astor/config.py`, directly below the `protocols_io_token` line:

```python
    protocols_io_licensed: bool = False  # second lock: bulk search/fetch stays off until a licence is confirmed
```

In `src/astor/protocols/sources.py`, add a class attribute to each source class:

```python
class ProtocolsIoSource:
    sweepable = False   # bulk search is the ToS-restricted act; gated + licence-locked

class EuropePmcSource:
    sweepable = True    # OA subset is explicitly provided for text mining
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py::test_sources_declare_sweepability tests/test_protocols.py::test_licensed_flag_defaults_false -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/config.py src/astor/protocols/sources.py tests/test_protocols.py
git commit -m "feat: protocols_io_licensed lock + sweepable source attribute"
```

---

### Task 2: `ProtocolsIoSource.search()` — offline mapping of a v3 list page

**Files:**
- Modify: `src/astor/protocols/sources.py` (`ProtocolsIoSource`)
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `settings.protocols_io_token`, `settings.protocols_io_licensed` (Task 1).
- Produces:
  - `ProtocolsIoSource.search(self, query, *, limit=100, page_size=50, peer_reviewed_only=False, allow_network=False) -> list[dict]` — returns **raw v3 list items** (dicts), NOT `RawProtocol`. The list item is later passed to `to_raw(payload, list_item=item)`.
  - `ProtocolsIoSource._list_items(body: dict) -> list[dict]` — pulls the item array out of a v3 response body (`body["items"]`, per verified live shape; fall back to `body.get("protocols")`).
  - `ProtocolsIoSource._next_page(body: dict) -> int | None` — reads `body["pagination"]["next_page"]`; `None` when absent/last page.

This task implements ONLY the pure body-parsing helpers + the network guard. The paged network loop is Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def _v3_list_body(items, next_page=None):
    return {"items": items, "pagination": {"next_page": next_page, "total_pages": 3}}

def test_list_items_extracts_item_array():
    src = ProtocolsIoSource()
    body = _v3_list_body([{"id": 1}, {"id": 2}])
    assert src._list_items(body) == [{"id": 1}, {"id": 2}]

def test_list_items_falls_back_to_protocols_key():
    src = ProtocolsIoSource()
    assert src._list_items({"protocols": [{"id": 9}]}) == [{"id": 9}]

def test_next_page_reads_pagination():
    src = ProtocolsIoSource()
    assert src._next_page(_v3_list_body([], next_page=2)) == 2
    assert src._next_page(_v3_list_body([], next_page=None)) is None

def test_search_is_double_locked():
    src = ProtocolsIoSource()
    with pytest.raises(RuntimeError, match="gated"):
        src.search("western blot")  # allow_network defaults False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k "list_items or next_page or search_is_double_locked" -v`
Expected: FAIL (`AttributeError: _list_items` etc.)

- [ ] **Step 3: Implement**

Add to `ProtocolsIoSource` (place above `fetch_one`):

```python
    @staticmethod
    def _list_items(body: dict) -> list[dict]:
        items = body.get("items")
        if items is None:
            items = body.get("protocols")
        return [it for it in (items or []) if isinstance(it, dict)]

    @staticmethod
    def _next_page(body: dict) -> int | None:
        pg = body.get("pagination") or {}
        nxt = pg.get("next_page")
        return nxt if isinstance(nxt, int) and nxt > 0 else None

    def _require_network(self, allow_network: bool) -> None:
        """Both locks. Bulk search/fetch stays off until a licence is confirmed AND
        the caller explicitly opts in — the pull is the ToS-restricted act (§3)."""
        if not allow_network:
            raise RuntimeError(
                "Network fetch is gated (allow_network=False). Bulk pulls carry "
                "licence/ToS exposure (docs/protocol-sourcing-handoff.md §3)."
            )
        if not settings.protocols_io_licensed:
            raise RuntimeError(
                "PROTOCOLS_IO_LICENSED is not set. A bulk sweep requires a confirmed "
                "protocols.io licence (docs/protocol-sourcing-handoff.md §3)."
            )
        if not settings.protocols_io_token:
            raise RuntimeError("ProtocolsIoSource needs PROTOCOLS_IO_TOKEN.")

    def search(
        self,
        query: str,
        *,
        limit: int = 100,
        page_size: int = 50,
        peer_reviewed_only: bool = False,
        allow_network: bool = False,
    ) -> list[dict]:
        """Paged v3 list search → raw list items (dicts). Double-locked.
        Implemented as a network loop in Task 3; the guard is enforced here."""
        self._require_network(allow_network)
        return self._search_network(query, limit, page_size, peer_reviewed_only)
```

Add a stub so this task's guard test passes without network:

```python
    def _search_network(self, query, limit, page_size, peer_reviewed_only):  # Task 3
        raise NotImplementedError
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k "list_items or next_page or search_is_double_locked" -v`
Expected: PASS (guard raises before `_search_network` is reached)

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/sources.py tests/test_protocols.py
git commit -m "feat: ProtocolsIoSource.search v3 body-parse helpers + double-lock guard"
```

---

### Task 3: `_search_network` — the paged httpx loop (dependency-injected client for tests)

**Files:**
- Modify: `src/astor/protocols/sources.py` (`ProtocolsIoSource`)
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `_list_items`, `_next_page` (Task 2).
- Produces: `_search_network(self, query, limit, page_size, peer_reviewed_only, *, client=None, sleep_between=1.0) -> list[dict]`. When `client` is passed (a `httpx.Client`-like object with `.get`), no real network is opened — this is the test seam. `sleep_between` throttles between pages via `time.sleep`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
class _FakeResp:
    def __init__(self, body): self._body = body
    def raise_for_status(self): pass
    def json(self): return self._body

class _FakeClient:
    """Serves canned v3 pages in order; records params seen."""
    def __init__(self, pages): self._pages = list(pages); self.calls = []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return _FakeResp(self._pages.pop(0))

def test_search_network_paginates_until_next_page_none():
    src = ProtocolsIoSource()
    pages = [
        {"items": [{"id": 1}, {"id": 2}], "pagination": {"next_page": 2}},
        {"items": [{"id": 3}], "pagination": {"next_page": None}},
    ]
    client = _FakeClient(pages)
    items = src._search_network("western blot", limit=100, page_size=50,
                                peer_reviewed_only=False, client=client, sleep_between=0)
    assert [it["id"] for it in items] == [1, 2, 3]

def test_search_network_respects_limit():
    src = ProtocolsIoSource()
    pages = [{"items": [{"id": i} for i in range(50)], "pagination": {"next_page": 2}}]
    client = _FakeClient(pages)
    items = src._search_network("x", limit=10, page_size=50,
                                peer_reviewed_only=False, client=client, sleep_between=0)
    assert len(items) == 10

def test_search_network_passes_peer_reviewed_filter():
    src = ProtocolsIoSource()
    client = _FakeClient([{"items": [], "pagination": {"next_page": None}}])
    src._search_network("x", limit=10, page_size=50,
                        peer_reviewed_only=True, client=client, sleep_between=0)
    assert client.calls[0].get("peer_reviewed") == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k search_network -v`
Expected: FAIL (`NotImplementedError` / signature mismatch)

- [ ] **Step 3: Implement**

Replace the `_search_network` stub. Add `import time` at the top of the module if absent.

```python
    def _search_network(
        self, query, limit, page_size, peer_reviewed_only,
        *, client=None, sleep_between: float = 1.0,
    ) -> list[dict]:
        import httpx
        own = client is None
        if own:
            client = httpx.Client(timeout=30.0)
        headers = {"Authorization": f"Bearer {settings.protocols_io_token}"}
        page_size = max(1, min(page_size, 50))
        out: list[dict] = []
        page = 1
        try:
            while len(out) < limit:
                params = {
                    "filter": "public",
                    "key": query,
                    "order_field": "relevance",
                    "page_size": page_size,
                    "page_id": page,
                }
                if peer_reviewed_only:
                    params["peer_reviewed"] = 1
                resp = client.get(
                    f"{self.LIST_BASE}/protocols",
                    params=params, headers=headers, timeout=30.0,
                )
                resp.raise_for_status()
                body = resp.json()
                items = self._list_items(body)
                if not items:
                    break
                out.extend(items)
                nxt = self._next_page(body)
                if nxt is None or nxt == page:
                    break
                page = nxt
                if sleep_between:
                    time.sleep(sleep_between)
        finally:
            if own:
                client.close()
        return out[:limit]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k search_network -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/sources.py tests/test_protocols.py
git commit -m "feat: ProtocolsIoSource._search_network paged v3 loop with test seam"
```

---

### Task 4: Category seed loader — `docs/curation/categories.csv` → queries

**Files:**
- Create: `src/astor/protocols/categories.py`
- Test: `tests/test_protocols.py`

**Interfaces:**
- Produces:
  - `SEED_SYNONYMS: dict[str, list[str]]` — hand-written query synonyms keyed by `category_id`.
  - `load_categories(csv_path: str | Path) -> list[CategorySeed]` where `CategorySeed` is a frozen dataclass `(category_id: str, category_name: str, queries: list[str])`.
  - Categories present in the CSV but absent from `SEED_SYNONYMS` fall back to `[category_name-derived query]` and are still returned.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def test_load_categories_maps_synonyms(tmp_path):
    from astor.protocols.categories import load_categories
    csv = tmp_path / "categories.csv"
    csv.write_text(
        "category_id,category_name,why_this_one,has_run_it,notes\n"
        "western_blot,Western blot / x,r,yes,n\n"
        "rt_qpcr,RT-qPCR / y,r,yes,n\n",
        encoding="utf-8",
    )
    seeds = load_categories(csv)
    ids = [s.category_id for s in seeds]
    assert ids == ["western_blot", "rt_qpcr"]
    wb = next(s for s in seeds if s.category_id == "western_blot")
    assert "western blot" in [q.lower() for q in wb.queries]
    assert len(wb.queries) >= 2  # has synonyms

def test_load_categories_unknown_id_falls_back_to_name(tmp_path):
    from astor.protocols.categories import load_categories
    csv = tmp_path / "c.csv"
    csv.write_text(
        "category_id,category_name,why_this_one,has_run_it,notes\n"
        "novel_assay,Novel Assay / z,r,no,n\n",
        encoding="utf-8",
    )
    seeds = load_categories(csv)
    assert seeds[0].queries  # non-empty fallback
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k load_categories -v`
Expected: FAIL (`ModuleNotFoundError: astor.protocols.categories`)

- [ ] **Step 3: Implement**

```python
# src/astor/protocols/categories.py
"""Seed queries per curated category (docs/curation/categories.csv).

Synonyms are hand-written because the category_id is a slug, not a search term,
and one phrasing misses protocols filed under another ('immunoblot' vs 'western
blot'). Categories without an explicit entry fall back to a name-derived query
so a newly-added row still harvests, just less precisely."""
from __future__ import annotations

import csv as _csv
from dataclasses import dataclass
from pathlib import Path

SEED_SYNONYMS: dict[str, list[str]] = {
    "western_blot": ["western blot", "immunoblot", "protein immunoblotting"],
    "rt_qpcr": ["RT-qPCR", "quantitative real-time PCR", "real-time reverse transcription PCR"],
    "elisa": ["ELISA", "enzyme-linked immunosorbent assay", "sandwich ELISA"],
    "cell_culture_transfection": ["cell culture transfection", "mammalian transfection", "lipofection"],
}


@dataclass(frozen=True)
class CategorySeed:
    category_id: str
    category_name: str
    queries: list[str]


def _fallback_query(category_name: str) -> str:
    # "Western blot / 蛋白免疫印迹" -> "Western blot"
    return category_name.split("/")[0].strip()


def load_categories(csv_path: str | Path) -> list[CategorySeed]:
    seeds: list[CategorySeed] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in _csv.DictReader(fh):
            cid = (row.get("category_id") or "").strip()
            if not cid:
                continue
            name = (row.get("category_name") or "").strip()
            queries = SEED_SYNONYMS.get(cid) or [_fallback_query(name) or cid]
            seeds.append(CategorySeed(cid, name, list(queries)))
    return seeds
```

Note `encoding="utf-8-sig"`: the real `categories.csv` begins with a BOM (verified — the header shows `﻿category_id`), which would otherwise corrupt the first column name.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k load_categories -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/categories.py tests/test_protocols.py
git commit -m "feat: category seed loader with per-category query synonyms"
```

---

### Task 5: Raw-payload persistence to disk (`data/raw/protocols_io/`)

**Files:**
- Create: `src/astor/protocols/raw_store.py`
- Test: `tests/test_protocols.py`

**Interfaces:**
- Produces:
  - `RawStore(root: str | Path)` — filesystem-backed cache.
  - `.detail_path(protocol_id, version_id) -> Path` → `<root>/details/<id>-v<version>.json`.
  - `.has_detail(protocol_id, version_id) -> bool` — the skip-if-persisted check.
  - `.write_detail(protocol_id, version_id, payload: dict) -> Path`.
  - `.read_detail(protocol_id, version_id) -> dict`.
  - `.write_search_page(category_id, query, page, body: dict) -> Path` → `<root>/searches/<category_id>/<slug(query)>-p<page>.json`.
  - `.iter_details() -> Iterator[dict]` — every persisted detail payload, for offline re-map.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def test_raw_store_detail_roundtrip_and_skip(tmp_path):
    from astor.protocols.raw_store import RawStore
    store = RawStore(tmp_path)
    assert store.has_detail("321062", "2") is False
    store.write_detail("321062", "2", {"id": 321062, "version_id": 2})
    assert store.has_detail("321062", "2") is True
    assert store.read_detail("321062", "2")["id"] == 321062

def test_raw_store_iter_details(tmp_path):
    from astor.protocols.raw_store import RawStore
    store = RawStore(tmp_path)
    store.write_detail("1", "1", {"id": 1})
    store.write_detail("2", "1", {"id": 2})
    ids = sorted(d["id"] for d in store.iter_details())
    assert ids == [1, 2]

def test_raw_store_search_page_path_is_slugged(tmp_path):
    from astor.protocols.raw_store import RawStore
    store = RawStore(tmp_path)
    p = store.write_search_page("western_blot", "western blot", 1, {"items": []})
    assert p.exists()
    assert "western-blot-p1.json" in str(p)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k raw_store -v`
Expected: FAIL (`ModuleNotFoundError: astor.protocols.raw_store`)

- [ ] **Step 3: Implement**

```python
# src/astor/protocols/raw_store.py
"""Filesystem cache of raw protocols.io payloads.

Persist-before-transform: every fetched page/detail is written here first, so
re-ranking or a mapping fix re-reads from disk and never re-hits the API. The
detail filename carries the version, which is what makes skip-if-persisted
version-aware — an updated protocol is a new file, a re-run of the same version
is a no-op."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "q"


class RawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def detail_path(self, protocol_id: str, version_id: str) -> Path:
        return self.root / "details" / f"{protocol_id}-v{version_id}.json"

    def has_detail(self, protocol_id: str, version_id: str) -> bool:
        return self.detail_path(protocol_id, version_id).exists()

    def write_detail(self, protocol_id: str, version_id: str, payload: dict) -> Path:
        path = self.detail_path(protocol_id, version_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def read_detail(self, protocol_id: str, version_id: str) -> dict:
        return json.loads(self.detail_path(protocol_id, version_id).read_text(encoding="utf-8"))

    def write_search_page(self, category_id: str, query: str, page: int, body: dict) -> Path:
        path = self.root / "searches" / category_id / f"{_slug(query)}-p{page}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    def iter_details(self) -> Iterator[dict]:
        details = self.root / "details"
        if not details.exists():
            return
        for path in sorted(details.glob("*.json")):
            yield json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k raw_store -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/raw_store.py tests/test_protocols.py
git commit -m "feat: RawStore filesystem cache for protocols.io payloads"
```

---

### Task 6: Shortlist logic + `run_from_search` sweepable invariant

**Files:**
- Create: `src/astor/protocols/harvest.py` (shortlist stage only; orchestrator wiring in Task 7)
- Modify: `src/astor/protocols/ingestion.py:85` (`hasattr` → `sweepable`)
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `ReviewSignal`, `rank_by_review` (`filtering`), `_as_int`/stats shape from `sources.py`.
- Produces:
  - `review_from_list_item(item: dict) -> ReviewSignal` — builds a `ReviewSignal` from a v3 list item's `stats` + `peer_reviewed`.
  - `shortlist(items: list[dict], n: int) -> list[dict]` — dedupe by `id` (keep highest `version_id`), rank by `(peer_reviewed, rank_score)` descending, take top-n. Returns the raw list items.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def _li(id, ver, peer=False, votes=0, views=0):
    return {"id": id, "version_id": ver, "peer_reviewed": peer,
            "stats": {"number_of_votes": votes, "number_of_views": views}}

def test_shortlist_dedupes_by_id_keeping_latest_version():
    from astor.protocols.harvest import shortlist
    items = [_li(1, 1, votes=5), _li(1, 3, votes=5), _li(2, 1, votes=1)]
    out = shortlist(items, n=10)
    v_for_1 = next(it["version_id"] for it in out if it["id"] == 1)
    assert v_for_1 == 3
    assert len(out) == 2

def test_shortlist_peer_reviewed_outranks_popularity():
    from astor.protocols.harvest import shortlist
    items = [_li(1, 1, peer=False, votes=999), _li(2, 1, peer=True, votes=0)]
    out = shortlist(items, n=10)
    assert out[0]["id"] == 2  # peer-reviewed first despite fewer votes

def test_shortlist_takes_top_n():
    from astor.protocols.harvest import shortlist
    items = [_li(i, 1, votes=i) for i in range(20)]
    assert len(shortlist(items, n=5)) == 5

def test_run_from_search_rejects_unsweepable_source():
    with pytest.raises(RuntimeError, match="cannot be swept"):
        ingestion.run_from_search(source_name="protocols.io")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k "shortlist or rejects_unsweepable" -v`
Expected: FAIL (`ModuleNotFoundError: harvest` / `run_from_search` still uses `hasattr`)

- [ ] **Step 3: Implement**

Create `src/astor/protocols/harvest.py` with the shortlist stage:

```python
"""protocols.io harvester orchestrator: discover -> shortlist -> fetch -> persist
-> map -> gate -> rank. Bulk-runnable only under the double lock (allow_network +
protocols_io_licensed). Stages are separable so the offline path (Task 8) drives
shortlist + map from persisted JSON with no network."""
from __future__ import annotations

from astor.protocols.filtering import rank_by_review
from astor.protocols.schemas import ReviewSignal
from astor.protocols.sources import _as_int


def review_from_list_item(item: dict) -> ReviewSignal:
    stats = item.get("stats") or {}
    peer = item.get("peer_reviewed")
    return ReviewSignal(
        peer_reviewed=bool(peer) if peer is not None else None,
        views=_as_int(stats.get("number_of_views")),
        votes=_as_int(stats.get("number_of_votes")),
        bookmarks=_as_int(stats.get("number_of_bookmarks")),
        forks=_as_int(stats.get("number_of_forks")),
        comments=_as_int(stats.get("number_of_protocol_comments"))
        or _as_int(stats.get("number_of_comments")),
    )


def _version_key(item: dict) -> int:
    try:
        return int(item.get("version_id") or 0)
    except (TypeError, ValueError):
        return 0


def shortlist(items: list[dict], n: int) -> list[dict]:
    best: dict[object, dict] = {}
    for it in items:
        pid = it.get("id")
        if pid is None:
            continue
        cur = best.get(pid)
        if cur is None or _version_key(it) > _version_key(cur):
            best[pid] = it
    ranked = sorted(
        best.values(),
        key=lambda it: (
            bool(review_from_list_item(it).peer_reviewed),
            review_from_list_item(it).rank_score,
        ),
        reverse=True,
    )
    return ranked[:n]
```

In `src/astor/protocols/ingestion.py`, replace the `hasattr` guard:

```python
    source = for_source(source_name)
    if not getattr(source, "sweepable", False):
        raise RuntimeError(
            f"Source {source_name!r} is not sweepable: it cannot be swept. "
            "Gated sources are driven from explicit identifiers or saved payloads."
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k "shortlist or rejects_unsweepable" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/harvest.py src/astor/protocols/ingestion.py tests/test_protocols.py
git commit -m "feat: shortlist stage + sweepable invariant in run_from_search"
```

---

### Task 7: Serving-basis gate override + run manifest

**Files:**
- Modify: `src/astor/protocols/harvest.py`
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `license_gate` (`filtering`), `to_raw` with `list_item` (`sources.py`), `rank_by_review`.
- Produces:
  - `HarvestManifest` dataclass: `serving_basis: str | None`, `queries: list[str]`, `shortlisted: int`, `fetched: int`, `servable: int`, `link_out: int`, `skipped_cached: int`, `errors: int`, `cap: int`.
  - `map_gate_rank(details: list[dict], list_items_by_id: dict, serving_basis: str | None) -> tuple[list[RawProtocol], list[RawProtocol]]` — maps each detail via `to_raw(detail, list_item=...)`, applies the gate. With `serving_basis` set, records are stamped (`fetched_at` provenance retained; `serving_basis` recorded on the manifest) and the gate `allow` set is widened to include their licence; with `None`, the default fail-closed gate runs and everything lands in link-out.

Design decision: since the API has no per-record licence, "widening" under a licence means passing `allow=DEFAULT_SERVE_LICENSES | {License.UNKNOWN}` **only when `serving_basis` is set**. `DEFAULT_SERVE_LICENSES` itself is never mutated.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def _detail(id, ver=1):
    return {"id": id, "version_id": ver, "url": f"https://p.io/{id}",
            "title": "T", "steps": [], "materials_text": ""}

def test_map_gate_rank_link_out_mode_serves_nothing():
    from astor.protocols.harvest import map_gate_rank
    servable, link_out = map_gate_rank([_detail(1)], {}, serving_basis=None)
    assert servable == []
    assert len(link_out) == 1

def test_map_gate_rank_licensed_mode_serves_unknown_license():
    from astor.protocols.harvest import map_gate_rank
    servable, link_out = map_gate_rank(
        [_detail(1)], {}, serving_basis="commercial-licence:pio-2026")
    assert len(servable) == 1
    assert link_out == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k map_gate_rank -v`
Expected: FAIL (`ImportError: map_gate_rank`)

- [ ] **Step 3: Implement**

Append to `harvest.py`:

```python
from dataclasses import dataclass, field

from astor.protocols.filtering import DEFAULT_SERVE_LICENSES, license_gate
from astor.protocols.schemas import License, RawProtocol
from astor.protocols.sources import ProtocolsIoSource


@dataclass
class HarvestManifest:
    serving_basis: str | None = None
    cap: int = 1000
    queries: list[str] = field(default_factory=list)
    shortlisted: int = 0
    fetched: int = 0
    skipped_cached: int = 0
    servable: int = 0
    link_out: int = 0
    errors: int = 0


def map_gate_rank(
    details: list[dict],
    list_items_by_id: dict,
    serving_basis: str | None,
) -> tuple[list[RawProtocol], list[RawProtocol]]:
    source = ProtocolsIoSource()
    raws: list[RawProtocol] = []
    for d in details:
        li = list_items_by_id.get(d.get("id"))
        raws.append(source.to_raw(d, list_item=li))
    allow = DEFAULT_SERVE_LICENSES
    if serving_basis:
        # The licence lives in the CONTRACT, not the payload: an authorised run
        # may serve records the API labels UNKNOWN. Widen the allow-set for THIS
        # run only; the module default is never mutated.
        allow = DEFAULT_SERVE_LICENSES | {License.UNKNOWN}
    servable, link_out = license_gate(raws, allow=allow)
    return rank_by_review(servable), link_out
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k map_gate_rank -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/harvest.py tests/test_protocols.py
git commit -m "feat: serving-basis gate override + harvest manifest"
```

---

### Task 8: `run_harvest` orchestrator (offline-drivable) + hard cap

**Files:**
- Modify: `src/astor/protocols/harvest.py`
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `run_harvest(seeds, *, source, store, n_per_category, cap=1000, serving_basis=None, allow_network=False, sleep_between=1.0, search_fn=None, fetch_fn=None) -> tuple[list[RawProtocol], list[RawProtocol], HarvestManifest]`.
  - `search_fn(query) -> list[dict]` and `fetch_fn(protocol_id) -> dict` are injectable seams; when `None`, real network methods are used (double-locked). Tests inject fakes ⇒ fully offline.
  - Hard cap: total details fetched never exceeds `cap`; raises `ValueError` if `n_per_category * len(seeds) `is configured such that a single category alone would exceed `cap`... (see enforcement below — cap is enforced on cumulative fetches, categories processed in order, stop when cap hit).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocols.py
def test_run_harvest_offline_end_to_end(tmp_path):
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("western_blot", "Western blot", ["western blot"])]
    catalog = {
        10: {"id": 10, "version_id": 2, "peer_reviewed": True,
             "stats": {"number_of_votes": 3}},
        11: {"id": 11, "version_id": 1, "peer_reviewed": False,
             "stats": {"number_of_votes": 99}},
    }
    def fake_search(query):
        return list(catalog.values())
    def fake_fetch(pid):
        it = catalog[pid]
        return {"id": pid, "version_id": it["version_id"], "url": f"https://p.io/{pid}",
                "title": f"P{pid}", "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    servable, link_out, manifest = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis="commercial-licence:pio-2026",
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
    )
    assert manifest.shortlisted == 2
    assert manifest.fetched == 2
    assert manifest.servable == 2      # licensed mode serves UNKNOWN
    # peer-reviewed id=10 ranks first despite id=11 having more votes
    assert servable[0].source_id == "10"
    # re-run is idempotent: everything already cached
    _, _, m2 = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis="commercial-licence:pio-2026",
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
    )
    assert m2.skipped_cached == 2
    assert m2.fetched == 0

def test_run_harvest_enforces_cap(tmp_path):
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest
    seeds = [CategorySeed("c", "C", ["q"])]
    catalog = {i: {"id": i, "version_id": 1, "stats": {"number_of_votes": i}} for i in range(50)}
    def fake_search(q): return list(catalog.values())
    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}
    store = RawStore(tmp_path)
    _, _, m = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=50, cap=10,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0)
    assert m.fetched == 10  # stopped at cap
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_protocols.py -k run_harvest -v`
Expected: FAIL (`AttributeError: run_harvest`)

- [ ] **Step 3: Implement**

Append to `harvest.py`:

```python
import logging

log = logging.getLogger(__name__)


def run_harvest(
    seeds,
    *,
    source,
    store,
    n_per_category: int,
    cap: int = 1000,
    serving_basis: str | None = None,
    allow_network: bool = False,
    sleep_between: float = 1.0,
    search_fn=None,
    fetch_fn=None,
):
    """Discover -> shortlist -> fetch(persist, skip cached) -> map -> gate -> rank.

    `search_fn`/`fetch_fn` default to the live double-locked source methods; tests
    inject fakes for a fully offline run. `cap` is a hard ceiling on total detail
    fetches across all categories (the ≤1,000 licence limit)."""
    if search_fn is None:
        search_fn = lambda q: source.search(  # noqa: E731
            q, limit=n_per_category * 5, allow_network=allow_network)
    if fetch_fn is None:
        fetch_fn = lambda pid: source.fetch_one(  # noqa: E731
            str(pid), allow_network=allow_network).raw

    manifest = HarvestManifest(serving_basis=serving_basis, cap=cap)
    list_items_by_id: dict = {}
    details: list[dict] = []

    for seed in seeds:
        manifest.queries.extend(seed.queries)
        candidates: list[dict] = []
        for q in seed.queries:
            candidates.extend(search_fn(q))
        picks = shortlist(candidates, n_per_category)
        manifest.shortlisted += len(picks)

        for it in picks:
            if manifest.fetched >= cap:
                log.warning("harvest cap %d reached; stopping", cap)
                break
            pid = it.get("id")
            list_items_by_id[pid] = it
            ver = str(_version_key(it))
            if store.has_detail(str(pid), ver):
                manifest.skipped_cached += 1
                details.append(store.read_detail(str(pid), ver))
                continue
            try:
                payload = fetch_fn(pid)
            except Exception as exc:  # noqa: BLE001 — one bad record must not abort the run
                manifest.errors += 1
                log.warning("fetch failed for %s: %s", pid, exc)
                continue
            store.write_detail(str(pid), ver, payload)
            details.append(payload)
            manifest.fetched += 1
        if manifest.fetched >= cap:
            break

    servable, link_out = map_gate_rank(details, list_items_by_id, serving_basis)
    manifest.servable = len(servable)
    manifest.link_out = len(link_out)
    log.info(
        "harvest done: shortlisted=%d fetched=%d cached=%d servable=%d link_out=%d errors=%d basis=%s",
        manifest.shortlisted, manifest.fetched, manifest.skipped_cached,
        manifest.servable, manifest.link_out, manifest.errors, serving_basis,
    )
    return servable, link_out, manifest
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_protocols.py -k run_harvest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/astor/protocols/harvest.py tests/test_protocols.py
git commit -m "feat: run_harvest orchestrator, offline-drivable, hard ≤1000 cap"
```

---

### Task 9: CLI runner `scripts/harvest_protocols.py`

**Files:**
- Create: `scripts/harvest_protocols.py`
- Test: manual (documented dry-run); no unit test (thin argparse shell over tested `run_harvest`).

**Interfaces:**
- Consumes: `run_harvest`, `load_categories`, `RawStore`, `settings`.
- Produces: a CLI. `--dry-run` (default) prints the shortlist plan without network. `--live` flips `allow_network=True` (still blocked unless `protocols_io_licensed`). `--n-per-category` (default 100), `--cap` (default 1000), `--serving-basis`, `--sleep` (default 1.0), `--categories` (path, default `docs/curation/categories.csv`).

- [ ] **Step 1: Implement the CLI**

```python
"""Harvest top-review protocols.io protocols per curated category.

SAFETY: live network requires BOTH --live AND settings.protocols_io_licensed=1
(the double lock). Default is --dry-run, which resolves categories and prints the
plan without any network. The ≤1,000 cap matches the protocols.io approval.

Usage:
    python -m scripts.harvest_protocols                       # dry-run plan
    python -m scripts.harvest_protocols --live \\
        --serving-basis "commercial-licence:pio-2026" \\
        --n-per-category 50 --cap 200                          # bounded sample sweep
"""
from __future__ import annotations

import argparse
from pathlib import Path

from astor.config import settings
from astor.protocols import harvest
from astor.protocols.categories import load_categories
from astor.protocols.raw_store import RawStore
from astor.protocols.sources import ProtocolsIoSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", default="docs/curation/categories.csv")
    ap.add_argument("--n-per-category", type=int, default=100)
    ap.add_argument("--cap", type=int, default=1000)
    ap.add_argument("--serving-basis", default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--out", default="data/raw/protocols_io")
    ap.add_argument("--live", action="store_true", help="enable network (needs licence lock)")
    args = ap.parse_args()

    seeds = load_categories(args.categories)
    print(f"categories: {[s.category_id for s in seeds]}")
    for s in seeds:
        print(f"  {s.category_id}: {s.queries}")

    if not args.live:
        print("\n[dry-run] no network. Re-run with --live once licensed.")
        return

    if not settings.protocols_io_licensed:
        raise SystemExit(
            "--live requires PROTOCOLS_IO_LICENSED=1 (confirmed licence). Aborting."
        )

    store = RawStore(args.out)
    servable, link_out, manifest = harvest.run_harvest(
        seeds, source=ProtocolsIoSource(), store=store,
        n_per_category=args.n_per_category, cap=args.cap,
        serving_basis=args.serving_basis, allow_network=True,
        sleep_between=args.sleep,
    )
    print(
        f"\nfetched={manifest.fetched} cached={manifest.skipped_cached} "
        f"servable={manifest.servable} link_out={manifest.link_out} "
        f"errors={manifest.errors} basis={manifest.serving_basis}"
    )
    print(f"payloads written under {args.out}/details/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the dry-run works**

Run: `python -m scripts.harvest_protocols`
Expected: prints the four categories with their synonyms, then the `[dry-run]` notice. No network.

- [ ] **Step 3: Verify the live lock holds without the flag**

Run: `python -m scripts.harvest_protocols --live` (with `PROTOCOLS_IO_LICENSED` unset)
Expected: exits with the "requires PROTOCOLS_IO_LICENSED=1" message. No network.

- [ ] **Step 4: Commit**

```bash
git add scripts/harvest_protocols.py
git commit -m "feat: harvest_protocols CLI (dry-run default, double-locked --live)"
```

---

### Task 10: Full-suite regression + `.gitignore` for raw payloads

**Files:**
- Modify: `.gitignore`
- Test: full `pytest`

**Interfaces:** none — this task proves the whole suite is green and keeps fetched payloads out of git.

- [ ] **Step 1: Ignore fetched payloads**

Add to `.gitignore`:

```
# Raw protocols.io payloads — fetched artifacts, not source
/data/raw/protocols_io/
```

- [ ] **Step 2: Run the full protocol suite**

Run: `pytest tests/test_protocols.py -v`
Expected: PASS (all prior tests + every test added in Tasks 1–8)

- [ ] **Step 3: Run the entire test suite**

Run: `pytest -q`
Expected: PASS, no regressions in `test_protocol_extraction.py` or elsewhere.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore fetched protocols.io payloads; harvester suite green"
```

---

## Self-Review

**Spec coverage:**
- `search()` v3, double-locked → Tasks 2, 3. ✓
- `harvest.py` discover→shortlist→fetch→persist→map→gate→rank → Tasks 5–8. ✓
- `serving_basis` provenance + widened gate → Task 7. ✓
- `sweepable` invariant replacing `hasattr` → Task 6. ✓
- Categories from `categories.csv` with synonyms → Task 4. ✓
- Top-N per category (default 100), ≤1000 cap → Tasks 6, 8. ✓
- Skip-if-persisted idempotence → Tasks 5, 8. ✓
- Throttle / batch-don't-one-shot → Tasks 3, 8 (`sleep_between`). ✓
- Offline fixture tests → every task uses injected fakes / tmp_path; no test touches the network. ✓
- Error handling (skip bad record, don't abort) → Task 8. ✓
- Endpoint version split preserved → constraint honoured; `LIST_BASE` used in Task 3. ✓

**Deferred (per spec non-goals), intentionally not in this plan:** §9 transforms, Europe PMC changes, LLM materials extraction, DB upsert wiring (persistence.py already exists and is tested; wiring `run_harvest` output into `upsert_protocols` is a one-liner left for the live-run session so the offline build stays DB-free).

**Placeholder scan:** no TBD/TODO; every code step is complete. One prose ellipsis in Task 8's interface note describes cap enforcement, which the code block then implements concretely (cumulative stop, not a raise). ✓

**Type consistency:** `shortlist`/`review_from_list_item`/`map_gate_rank`/`run_harvest`/`HarvestManifest`/`RawStore`/`CategorySeed`/`load_categories` names are consistent across tasks. `search()` returns `list[dict]`; `to_raw(payload, list_item=...)` matches the existing signature in `sources.py:149`. ✓

**Verified against live API this session:** token auth works; v4 detail fetch of id `321062` returns 200 with full mapping; v4 rejects slug ids ("invalid protocol uri") — hence Task 8 fetches by numeric `id` from list items, not by slug.
