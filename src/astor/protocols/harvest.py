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
