"""Protocol ingest scaffold — offline tests (no network).

Exercise the two things the scaffold must get right: the source→RawProtocol
mapping behind the adapter boundary, and the two v1 policy steps (licence gate =
legal, review rank = selection).
"""
from __future__ import annotations

import pytest

from astor.protocols import filtering, ingestion
from astor.protocols.schemas import License, RawProtocol, ReviewSignal
from astor.protocols.sources import ProtocolsIoSource, for_source


def _pio_payload(**over) -> dict:
    base = {
        "id": 123,
        "uri": "my-western-blot",
        "url": "https://www.protocols.io/view/my-western-blot",
        "title": "  My Western Blot  ",
        "authors": [{"name": "Mary Yu"}, {"name": ""}],
        "doi": "10.17504/protocols.io.abc",
        "version_id": 2,
        "license": {"title": "CC-BY"},
        "materials": [
            {"name": "PBS", "amount": "500 mL", "vendor": {"name": "Astor"}, "catalog": "PBS-1"},
            {"name": "", "amount": "ignored — no name"},
        ],
        "steps": [{"title": "Lyse cells"}, {"description": "Load gel"}, {"title": ""}],
        "stats": {"average_rating": 4.6, "number_of_ratings": 120, "number_of_views": 3000},
    }
    base.update(over)
    return base


def test_to_raw_maps_protocols_io_payload():
    raw = ProtocolsIoSource().to_raw(_pio_payload())
    assert raw.source == "protocols.io"
    assert raw.source_id == "123"
    assert raw.title == "My Western Blot"          # trimmed
    assert raw.authors == ["Mary Yu"]              # empty author dropped
    assert raw.license is License.CC_BY
    assert [m.name for m in raw.materials] == ["PBS"]  # unnamed material dropped
    assert raw.materials[0].vendor == "Astor"
    assert [s.text for s in raw.steps] == ["Lyse cells", "Load gel"]  # empty step dropped
    assert raw.review.rating == 4.6 and raw.review.ratings_count == 120


def test_unknown_license_maps_to_unknown_and_fails_closed():
    raw = ProtocolsIoSource().to_raw(_pio_payload(license={"title": "weird-new-license"}))
    assert raw.license is License.UNKNOWN
    assert raw.license.redistributable is False


def test_license_gate_splits_servable_from_link_out():
    servable = RawProtocol(source="protocols.io", source_id="1", source_uri="u", title="ok",
                           license=License.CC_BY)
    nc = RawProtocol(source="protocols.io", source_id="2", source_uri="u", title="nc",
                     license=License.CC_BY_NC)
    unknown = RawProtocol(source="protocols.io", source_id="3", source_uri="u", title="?",
                          license=License.UNKNOWN)
    ok, link_out = filtering.license_gate([servable, nc, unknown])
    assert [p.source_id for p in ok] == ["1"]
    assert {p.source_id for p in link_out} == {"2", "3"}


def test_rank_by_review_orders_highest_first():
    low = RawProtocol(source="s", source_id="low", source_uri="u", title="low",
                      review=ReviewSignal(rating=4.6, ratings_count=1))
    high = RawProtocol(source="s", source_id="high", source_uri="u", title="high",
                       review=ReviewSignal(rating=4.6, ratings_count=500))
    ranked = filtering.rank_by_review([low, high])
    assert [p.source_id for p in ranked] == ["high", "low"]  # volume breaks the tie


def test_engagement_fallback_when_no_rating():
    r = ReviewSignal(bookmarks=10, forks=2, views=1000)
    assert r.rank_score == pytest.approx(10 * 3 + 2 * 2 + 1000 * 0.01)


def test_run_from_payloads_end_to_end():
    payloads = [
        _pio_payload(id=1, title="popular", stats={"average_rating": 4.9, "number_of_ratings": 300}),
        _pio_payload(id=2, title="niche", stats={"average_rating": 4.9, "number_of_ratings": 3}),
        _pio_payload(id=3, title="restricted", license={"title": "all rights reserved"}),
    ]
    result = ingestion.run_from_payloads(payloads)
    assert result.fetched == 3
    assert result.servable == 2          # ARR dropped to link-out
    assert result.link_out_only == 1
    assert [p.title for p in result.ranked] == ["popular", "niche"]  # review-ranked


def test_live_fetch_is_gated_by_default():
    with pytest.raises(RuntimeError, match="gated"):
        ProtocolsIoSource().fetch_one("my-western-blot")


def test_for_source_rejects_unknown_source():
    with pytest.raises(ValueError, match="protocols.io only"):
        for_source("addgene")
