"""protocols.io harvester orchestrator: discover -> shortlist -> fetch -> persist
-> map -> gate -> rank. Bulk-runnable only under the double lock (allow_network +
protocols_io_licensed). Stages are separable so the offline path (Task 8) drives
shortlist + map from persisted JSON with no network."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from astor.config import settings
from astor.protocols.filtering import DEFAULT_SERVE_LICENSES, license_gate, rank_by_review
from astor.protocols.schemas import License, RawProtocol, ReviewSignal
from astor.protocols.sources import ProtocolsIoSource, _as_int

log = logging.getLogger(__name__)


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
        try:
            li = list_items_by_id.get(d.get("id")) if isinstance(d, dict) else None
            raws.append(source.to_raw(d, list_item=li))
        except Exception as exc:  # noqa: BLE001 — one bad record must not abort the run
            log.warning("map (to_raw) failed for %r: %s", d, exc)
            continue
    allow = DEFAULT_SERVE_LICENSES
    if serving_basis:
        # The licence lives in the CONTRACT, not the payload: an authorised run
        # may serve records the API labels UNKNOWN. Widen the allow-set for THIS
        # run only; the module default is never mutated.
        allow = DEFAULT_SERVE_LICENSES | {License.UNKNOWN}
    servable, link_out = license_gate(raws, allow=allow)
    return rank_by_review(servable), link_out


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
    stamp: str | None = None,
    manifest_name: str = "manifest.json",
):
    """Discover -> shortlist -> fetch(persist, skip cached) -> map -> gate -> rank.

    `search_fn`/`fetch_fn` default to the live double-locked source methods; tests
    inject fakes for a fully offline run. `cap` is a hard ceiling on total detail
    fetches across all categories (the ≤1,000 licence limit).

    The double lock is asserted here too, at the top, independent of whatever
    `search_fn`/`fetch_fn` a caller injects — `ProtocolsIoSource._require_network`
    only protects the default fns; an orchestrator that trusted injected fns to
    self-police the licence would be bypassable. `stamp` is an optional caller-
    supplied timestamp (e.g. from the CLI) recorded in the written manifest; it is
    never generated here so the orchestrator stays pure and easy to test.
    """
    if allow_network and not settings.protocols_io_licensed:
        raise RuntimeError(
            "PROTOCOLS_IO_LICENSED is not set. A bulk harvest requires a confirmed "
            "protocols.io licence (docs/protocol-sourcing-handoff.md §3)."
        )

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
            items = search_fn(q)
            store.write_search_page(seed.category_id, q, 1, {"items": items})
            candidates.extend(items)
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
                try:
                    payload = store.read_detail(str(pid), ver)
                except Exception as exc:  # noqa: BLE001 — a corrupt cache entry must not abort the run
                    manifest.errors += 1
                    log.warning("cached read failed for %s v%s: %s", pid, ver, exc)
                    continue
                manifest.skipped_cached += 1
                details.append(payload)
                continue
            try:
                payload = fetch_fn(pid)
                if not isinstance(payload, dict):
                    raise TypeError(
                        f"fetch_fn returned non-dict payload for {pid}: {type(payload)!r}")
            except Exception as exc:  # noqa: BLE001 — one bad record must not abort the run
                manifest.errors += 1
                log.warning("fetch failed for %s: %s", pid, exc)
                continue
            store.write_detail(str(pid), ver, payload)
            details.append(payload)
            manifest.fetched += 1
            if sleep_between:
                time.sleep(sleep_between)
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

    manifest_body = {
        "serving_basis": manifest.serving_basis,
        "cap": manifest.cap,
        "queries": manifest.queries,
        "shortlisted": manifest.shortlisted,
        "fetched": manifest.fetched,
        "skipped_cached": manifest.skipped_cached,
        "servable": manifest.servable,
        "link_out": manifest.link_out,
        "errors": manifest.errors,
    }
    if stamp is not None:
        manifest_body["stamp"] = stamp
    store.write_manifest(manifest_body, manifest_name)

    return servable, link_out, manifest
