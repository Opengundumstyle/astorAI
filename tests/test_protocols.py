"""Protocol ingest scaffold — offline tests (no network).

Exercise the two things the scaffold must get right: the source→RawProtocol
mapping behind the adapter boundary, and the two v1 policy steps (licence gate =
legal, review rank = selection).
"""
from __future__ import annotations

import json

import pytest

from astor.db.models import Protocol
from astor.protocols import filtering, ingestion, persistence
from astor.protocols.schemas import License, RawProtocol, RawStep, ReviewSignal
from astor.protocols.sources import EuropePmcSource, ProtocolsIoSource, for_source


def _draft(*lines: str) -> str:
    """Draft.js state, JSON-encoded into a string — how protocols.io actually
    stores step and materials prose."""
    return json.dumps(
        {"blocks": [{"key": f"k{i}", "text": t, "type": "unstyled"} for i, t in enumerate(lines)]}
    )


def _pio_payload(**over) -> dict:
    """Mirrors a REAL protocols.io v4 response (verified against protocol 321062,
    2026-07-19). Every oddity below is genuine, not invented: Draft.js-encoded
    prose, {first_name,last_name} authors, an empty `materials` with the real list
    in `materials_text`, a resolver-URL DOI with a version suffix, a null licence,
    and `number_of_forks` as an object while its siblings are ints."""
    base = {
        "id": 321062,
        "uri": "my-western-blot-j5secq6bf",
        "url": "https://www.protocols.io/view/my-western-blot-j5secq6bf",
        "title": "Western blot &amp; quantification",
        "title_html": "<p>Western blot &amp; quantification</p>",
        "authors": [{"first_name": "Mary", "last_name": "Yu"}, {"first_name": "", "last_name": ""}],
        "creator": {"first_name": "Zhile", "last_name": "Lin"},
        "doi": "dx.doi.org/10.17504/protocols.io.261ge87oog47/v3",
        "version_id": 2,
        "license": None,
        "materials": [],
        "materials_text": _draft("1.5 mL Eppendorf tubes", "Standard 96-well plate", "  "),
        "steps": [
            {"step": _draft("Place the following on ice to thaw:", "Template RNA"),
             "section": "<p>Assay set-up</p>"},
            {"step": "", "section": "<p>Cleanup &amp; disposal</p>"},   # section fallback
            {"step": "", "section": ""},                                 # dropped entirely
        ],
        "stats": {
            "number_of_views": 348,
            "number_of_votes": 2,
            "number_of_bookmarks": 0,
            "number_of_protocol_comments": 3,
            "number_of_forks": {"private": 1, "public": 2},
        },
    }
    base.update(over)
    return base


def test_to_raw_maps_protocols_io_payload():
    raw = ProtocolsIoSource().to_raw(_pio_payload())
    assert raw.source == "protocols.io"
    assert raw.source_id == "321062"
    assert raw.title == "Western blot & quantification"   # entity decoded, tags stripped
    assert raw.authors == ["Mary Yu"]                     # {first,last}; empty dropped
    assert raw.version == "2"


def test_doi_is_normalized_to_bare_identifier():
    """The resolver host and the /vN suffix both break identity: the suffix would
    make every version of one protocol dedupe as a separate work."""
    raw = ProtocolsIoSource().to_raw(_pio_payload())
    assert raw.doi == "10.17504/protocols.io.261ge87oog47"


def test_draftjs_step_and_materials_prose_is_flattened():
    raw = ProtocolsIoSource().to_raw(_pio_payload())
    assert raw.steps[0].text == "Place the following on ice to thaw:\nTemplate RNA"
    assert raw.steps[1].text == "Cleanup & disposal"      # falls back to `section`
    assert len(raw.steps) == 2                            # fully empty step dropped
    assert [m.name for m in raw.materials] == ["1.5 mL Eppendorf tubes", "Standard 96-well plate"]


def test_structured_materials_win_over_free_text():
    raw = ProtocolsIoSource().to_raw(_pio_payload(
        materials=[{"name": "PBS", "amount": "500 mL", "vendor": {"name": "Astor"}, "catalog": "PBS-1"}]
    ))
    assert [m.name for m in raw.materials] == ["PBS"]
    assert raw.materials[0].vendor == "Astor" and raw.materials[0].catalog_no == "PBS-1"


def test_object_valued_fork_count_does_not_break_mapping():
    """`number_of_forks` is an object while every sibling counter is an int —
    this crashed the original mapping outright."""
    review = ProtocolsIoSource().to_raw(_pio_payload()).review
    assert review.forks == 3            # private + public
    assert review.views == 348 and review.votes == 2 and review.comments == 3


def test_protocols_io_exposes_no_star_rating():
    """v4 has no rating/ratings_count; ranking must fall back to engagement."""
    review = ProtocolsIoSource().to_raw(_pio_payload()).review
    assert review.rating is None and review.ratings_count is None
    assert review.rank_score > 0


def test_null_license_fails_closed_and_visibility_is_not_a_grant():
    """The live payload returned license=null on a `public: true` protocol.
    Public visibility is not a copyright grant — this must not become servable."""
    raw = ProtocolsIoSource().to_raw(_pio_payload(license=None, public=True))
    assert raw.license is License.UNKNOWN
    assert raw.license.redistributable is False


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
    cc_by = {"title": "CC-BY"}
    payloads = [
        _pio_payload(id=1, title="popular", license=cc_by,
                     stats={"number_of_views": 9000, "number_of_bookmarks": 40}),
        _pio_payload(id=2, title="niche", license=cc_by,
                     stats={"number_of_views": 12, "number_of_bookmarks": 0}),
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
    with pytest.raises(ValueError, match="v1 sources"):
        for_source("addgene")


# --------------------------------------------------------------------------- #
# Europe PMC adapter (the permitted ingest lane)
# --------------------------------------------------------------------------- #
def _epmc_payload(**over) -> dict:
    base = {
        "id": "38000001",
        "source": "MED",
        "pmid": "38000001",
        "pmcid": "PMC10000001",
        "doi": "10.1234/example.2026.001",
        "title": "A robust western blot protocol for low-abundance targets.",
        "authorList": {"author": [{"fullName": "Yu M"}, {"fullName": "Lin Z"}, {}]},
        "authorString": "Yu M, Lin Z.",
        "license": "cc by",
        "isOpenAccess": "Y",
        "citedByCount": 412,
        "abstractText": "We describe ...",
    }
    base.update(over)
    return base


def test_epmc_to_raw_maps_core_fields():
    raw = EuropePmcSource().to_raw(_epmc_payload())
    assert raw.source == "europepmc"
    assert raw.source_id == "38000001"
    assert raw.doi == "10.1234/example.2026.001"
    assert raw.title.endswith("low-abundance targets")   # trailing period stripped
    assert raw.authors == ["Yu M", "Lin Z"]              # empty author dropped
    assert raw.license is License.CC_BY
    assert raw.review.citations == 412
    assert raw.source_uri == "https://europepmc.org/article/MED/38000001"


def test_epmc_falls_back_to_author_string():
    payload = _epmc_payload()
    del payload["authorList"]
    assert EuropePmcSource().to_raw(payload).authors == ["Yu M", "Lin Z."]


def test_epmc_yields_no_steps_by_design():
    """Europe PMC returns articles, not step-structured protocols. The adapter must
    NOT invent steps — extraction is a separate stage."""
    raw = EuropePmcSource().to_raw(_epmc_payload())
    assert raw.steps == [] and raw.materials == []


def test_epmc_share_alike_is_not_servable():
    """CC-BY-SA permits commercial use but propagates share-alike onto derivatives,
    so it must not be servable by default (fail closed pending counsel)."""
    raw = EuropePmcSource().to_raw(_epmc_payload(license="cc by-sa"))
    assert raw.license is License.CC_BY_SA
    assert raw.license.redistributable is False


def test_epmc_nc_variants_map_and_fail_closed():
    src = EuropePmcSource()
    for value, expected in [
        ("cc by-nc", License.CC_BY_NC),
        ("cc by-nc-sa", License.CC_BY_NC_SA),
        ("cc by-nc-nd", License.CC_BY_NC_ND),
        ("some-new-thing", License.UNKNOWN),
    ]:
        lic = src.to_raw(_epmc_payload(license=value)).license
        assert lic is expected and lic.redistributable is False


def test_citations_outrank_engagement_signals():
    """Citation count is the signal §4 proxied for; when present it wins."""
    cited = ReviewSignal(citations=400)
    engaged = ReviewSignal(bookmarks=1, views=10)
    assert cited.rank_score > engaged.rank_score


def test_for_source_resolves_europepmc():
    assert isinstance(for_source("europepmc"), EuropePmcSource)


# --------------------------------------------------------------------------- #
# Persistence: identity/dedupe (PI-4) and the licence gate at rest (PI-5)
# --------------------------------------------------------------------------- #
def _raw(doi=None, source_id="1", license=License.CC_BY, citations=None, **over) -> RawProtocol:
    return RawProtocol(
        source="europepmc", source_id=source_id, source_uri="u", title="t",
        doi=doi, license=license,
        steps=[RawStep(number=1, text="Lyse cells")],
        review=ReviewSignal(citations=citations),
        **over,
    )


def test_dedupe_by_doi_keeps_highest_ranked():
    low = _raw(doi="10.1/x", source_id="a", citations=1)
    high = _raw(doi="10.1/x", source_id="b", citations=900)
    kept, dropped = persistence.dedupe_by_doi([low, high])
    assert dropped == 1
    assert [p.source_id for p in kept] == ["b"]


def test_dedupe_by_doi_is_case_insensitive():
    kept, dropped = persistence.dedupe_by_doi([_raw(doi="10.1/X"), _raw(doi="10.1/x", source_id="2")])
    assert dropped == 1 and len(kept) == 1


def test_dedupe_passes_through_doi_less_records():
    """No DOI means no cross-source identity — those dedupe on (source, source_id)
    downstream and must not be collapsed into each other here."""
    kept, dropped = persistence.dedupe_by_doi([_raw(source_id="1"), _raw(source_id="2")])
    assert dropped == 0 and len(kept) == 2


def test_apply_strips_content_for_non_servable_but_keeps_attribution():
    row = Protocol()
    servable = persistence._apply(row, _raw(doi="10.1/x", license=License.CC_BY_NC))
    assert servable is False
    assert row.servable is False
    assert row.steps == [] and row.materials == []      # PI-5: content withheld
    assert row.doi == "10.1/x" and row.source_uri == "u"  # PI-5: attribution kept
    assert row.license == "CC-BY-NC"                     # PI-2: licence on the row


def test_apply_keeps_content_for_servable_license():
    row = Protocol()
    assert persistence._apply(row, _raw(license=License.CC_BY)) is True
    assert [s["text"] for s in row.steps] == ["Lyse cells"]


class _StubSession:
    """Stands in for a Session. `_find_existing` is patched out, so the SQL identity
    lookup is NOT covered here — that needs the compose Postgres (see note below)."""

    def __init__(self):
        self.added: list[Protocol] = []
        self.flushed = 0

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed += 1


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for the DB's identity resolution."""
    rows: dict[tuple, Protocol] = {}

    def fake_find(session, p):
        return rows.get(("doi", p.doi)) or rows.get(("sid", p.source, p.source_id))

    def remember(row):
        if row.doi:
            rows[("doi", row.doi)] = row
        rows[("sid", row.source, row.source_id)] = row

    monkeypatch.setattr(persistence, "_find_existing", fake_find)
    return rows, remember


def test_upsert_creates_then_updates_on_rerun(store, monkeypatch):
    rows, remember = store
    session = _StubSession()

    first = persistence.upsert_protocols(session, [_raw(doi="10.1/x")])
    assert (first.created, first.updated) == (1, 0)
    remember(session.added[0])

    # PI-4: re-running the identical batch must not create a second row.
    second = persistence.upsert_protocols(session, [_raw(doi="10.1/x")])
    assert (second.created, second.updated) == (0, 1)
    assert len(session.added) == 1


def test_upsert_counts_gate_outcome_and_batch_dedupe(store):
    _, _ = store
    session = _StubSession()
    result = persistence.upsert_protocols(session, [
        _raw(doi="10.1/a", source_id="a", license=License.CC_BY, citations=5),
        _raw(doi="10.1/a", source_id="a2", license=License.CC_BY, citations=99),  # dup DOI
        _raw(doi="10.1/b", source_id="b", license=License.ALL_RIGHTS_RESERVED),
    ])
    assert result.deduped_in_batch == 1
    assert result.written == 2
    assert (result.servable, result.link_out_only) == (1, 1)


# --------------------------------------------------------------------------- #
# Verified against apidoc.protocols.io (2026-07-19)
# --------------------------------------------------------------------------- #
def test_peer_reviewed_is_mapped():
    src = ProtocolsIoSource()
    assert src.to_raw(_pio_payload(peer_reviewed=1)).review.peer_reviewed is True
    assert src.to_raw(_pio_payload(peer_reviewed=0)).review.peer_reviewed is False
    assert src.to_raw(_pio_payload(peer_reviewed=None)).review.peer_reviewed is None


def test_peer_reviewed_outranks_a_more_popular_unreviewed_protocol():
    """Quality beats popularity: a journal-reviewed protocol must not be buried
    by an unreviewed one with a bigger view count."""
    popular = RawProtocol(source="s", source_id="popular", source_uri="u", title="p",
                          review=ReviewSignal(views=100_000, peer_reviewed=False))
    reviewed = RawProtocol(source="s", source_id="reviewed", source_uri="u", title="r",
                           review=ReviewSignal(views=3, peer_reviewed=True))
    ranked = filtering.rank_by_review([popular, reviewed])
    assert [p.source_id for p in ranked] == ["reviewed", "popular"]


def test_rank_falls_back_to_score_when_peer_review_is_equal():
    lo = RawProtocol(source="s", source_id="lo", source_uri="u", title="l",
                     review=ReviewSignal(views=10, peer_reviewed=True))
    hi = RawProtocol(source="s", source_id="hi", source_uri="u", title="h",
                     review=ReviewSignal(views=900, peer_reviewed=True))
    assert [p.source_id for p in filtering.rank_by_review([lo, hi])] == ["hi", "lo"]


def test_protocols_io_endpoint_versions_are_split():
    """Get-protocol is documented at v4, list/search at v3. Collapsing them to one
    version breaks one of the two -- the v4 listing route rejects documented params."""
    src = ProtocolsIoSource()
    assert src.BASE.endswith("/v4")
    assert src.LIST_BASE.endswith("/v3")


def test_adapter_exposes_no_search_method():
    """Sweeping is the contractually restricted act (ToS 4.A.xi), so the gated
    adapter must not offer a sweep affordance at all."""
    assert not hasattr(ProtocolsIoSource(), "search")


def test_docs_author_shape_still_maps():
    """The docs' example uses {'name': ...} while the LIVE payload uses
    {first_name, last_name}. Both must map -- the docs lag the API."""
    raw = ProtocolsIoSource().to_raw(_pio_payload(authors=[{"name": "Celina Gomez"}]))
    assert raw.authors == ["Celina Gomez"]


def test_peer_reviewed_is_recovered_from_the_list_item():
    """v4 get-protocol always returns peer_reviewed=null; only the v3 list carries
    it. Without the overlay our best quality signal is silently always None."""
    src = ProtocolsIoSource()
    v4_payload = _pio_payload(peer_reviewed=None)
    assert src.to_raw(v4_payload).review.peer_reviewed is None
    assert src.to_raw(v4_payload, list_item={"peer_reviewed": True}).review.peer_reviewed is True


def test_payload_peer_reviewed_wins_over_list_item():
    src = ProtocolsIoSource()
    raw = src.to_raw(_pio_payload(peer_reviewed=0), list_item={"peer_reviewed": True})
    assert raw.review.peer_reviewed is False


def test_structured_material_maps_vendor_and_catalog_number():
    """The commercially load-bearing mapping: vendor + sku become (brand, mpn),
    which is exactly the Product dedupe key."""
    raw = ProtocolsIoSource().to_raw(_pio_payload(materials=[
        {"name": "RNeasy® Mini Kit", "sku": "74104", "vendor": {"name": "Qiagen"}},
    ]))
    m = raw.materials[0]
    assert (m.name, m.vendor, m.catalog_no) == ("RNeasy® Mini Kit", "Qiagen", "74104")
