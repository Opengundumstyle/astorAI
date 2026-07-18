"""Two pure steps that encode the v1 policy decisions (ARCHITECTURE.md §4 override):

  * `license_gate` — the LEGAL enforcement point. Only licences we may serve pass;
    everything else is dropped (or kept as link-out only). Fail closed.
  * `rank_by_review` / `top_by_review` — the SELECTION policy Mary + Zhile agreed:
    prioritise protocols with the highest review/engagement signal.

Kept separate because they answer different questions: the gate is 'may we serve
this at all?', the rank is 'which of the allowed ones do we surface first?'.
"""
from __future__ import annotations

from collections.abc import Iterable

from astor.protocols.schemas import License, RawProtocol

# Licences we may store-and-serve by default. Anything else is link-out only.
DEFAULT_SERVE_LICENSES = frozenset({License.CC0, License.CC_BY})


def license_gate(
    protocols: Iterable[RawProtocol],
    allow: frozenset[License] = DEFAULT_SERVE_LICENSES,
) -> tuple[list[RawProtocol], list[RawProtocol]]:
    """Split into (servable, link_out_only). Servable = licence in `allow`.
    Everything else — including UNKNOWN — falls to link-out, never served content.
    Returning both lists (not silently dropping) keeps the link-out set usable and
    makes the drop count observable."""
    servable, link_out = [], []
    for p in protocols:
        (servable if p.license in allow else link_out).append(p)
    return servable, link_out


def rank_by_review(protocols: Iterable[RawProtocol]) -> list[RawProtocol]:
    """Highest review first. Stable: ties keep input order."""
    return sorted(protocols, key=lambda p: p.review.rank_score, reverse=True)


def top_by_review(protocols: Iterable[RawProtocol], n: int) -> list[RawProtocol]:
    return rank_by_review(protocols)[:n]
