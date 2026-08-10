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


def test_apply_unknown_stays_link_out_without_serving_basis():
    """The protocols.io default: no licence, no serving_basis → content stripped."""
    row = Protocol()
    assert persistence._apply(row, _raw(license=License.UNKNOWN)) is False
    assert row.servable is False and row.serving_basis is None
    assert row.steps == []


def test_apply_serving_basis_makes_unknown_servable_and_stamps_it():
    """A commercial licence authorizes serving UNKNOWN protocols.io content: the
    row becomes servable, keeps its steps, and records WHY (the serving_basis)."""
    row = Protocol()
    servable = persistence._apply(
        row, _raw(license=License.UNKNOWN), serving_basis="commercial-licence:pio-2026")
    assert servable is True
    assert row.servable is True
    assert row.serving_basis == "commercial-licence:pio-2026"
    assert [s["text"] for s in row.steps] == ["Lyse cells"]


def test_apply_permissive_license_needs_no_basis_even_when_one_is_passed():
    """A CC-BY row is servable on its own licence; the basis column stays NULL so
    it crisply means 'why an UNKNOWN row is served', not 'which run wrote this'."""
    row = Protocol()
    persistence._apply(row, _raw(license=License.CC_BY), serving_basis="commercial-licence:pio-2026")
    assert row.servable is True and row.serving_basis is None


def test_apply_serving_basis_does_not_unlock_restricted_nc():
    """serving_basis widens only UNKNOWN (the API's 'no label'), never a licence
    that AFFIRMATIVELY forbids commercial serving like CC-BY-NC."""
    row = Protocol()
    servable = persistence._apply(
        row, _raw(license=License.CC_BY_NC), serving_basis="commercial-licence:pio-2026")
    assert servable is False and row.serving_basis is None and row.steps == []


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


def test_upsert_threads_serving_basis_to_unknown_records(store):
    """The whole point of the wiring: a protocols.io corpus (UNKNOWN licence) loaded
    under a serving_basis lands servable-with-content, not stripped to link-out."""
    _, _ = store
    session = _StubSession()
    result = persistence.upsert_protocols(
        session,
        [_raw(doi=None, source_id="p1", license=License.UNKNOWN)],
        serving_basis="commercial-licence:pio-2026",
    )
    assert (result.servable, result.link_out_only) == (1, 0)
    row = session.added[0]
    assert row.servable is True and row.serving_basis == "commercial-licence:pio-2026"
    assert [s["text"] for s in row.steps] == ["Lyse cells"]


def test_upsert_without_serving_basis_leaves_unknown_link_out(store):
    _, _ = store
    session = _StubSession()
    result = persistence.upsert_protocols(
        session, [_raw(doi=None, source_id="p1", license=License.UNKNOWN)])
    assert (result.servable, result.link_out_only) == (0, 1)
    assert session.added[0].steps == []


def test_map_from_store_recovers_peer_reviewed_from_search_pages(tmp_path):
    """The DB loader's offline bridge: details give content, saved search pages give
    peer_reviewed (absent from v4 detail payloads). map_from_store rejoins them."""
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    store = RawStore(tmp_path)
    # A saved search page carrying the quality signal, and the matching detail.
    store.write_search_page("western_blot", "western blot", 1,
                            {"items": [{"id": 500, "version_id": 1, "peer_reviewed": True,
                                        "stats": {"number_of_votes": 7}}]})
    store.write_detail("500", "1", {"id": 500, "version_id": 1, "url": "https://p.io/500",
                                    "title": "WB", "steps": [], "materials_text": ""})
    raws = harvest.map_from_store(store)
    assert len(raws) == 1
    assert raws[0].source_id == "500"
    assert raws[0].review.peer_reviewed is True  # recovered from the search page, not the detail


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


# --------------------------------------------------------------------------- #
# Source attributes and configuration
# --------------------------------------------------------------------------- #
def test_sources_declare_sweepability():
    assert ProtocolsIoSource.sweepable is False
    assert EuropePmcSource.sweepable is True


def test_licensed_flag_defaults_false():
    from astor.config import settings
    assert settings.protocols_io_licensed is False


# --------------------------------------------------------------------------- #
# Task 2: ProtocolsIoSource.search() — offline mapping of a v3 list page
# --------------------------------------------------------------------------- #
def _v3_list_body(items, total_pages=1):
    # Mirrors the live v3 shape: next_page is a URL STRING (the loop ignores it and
    # drives pagination by total_pages instead — see ProtocolsIoSource._total_pages).
    return {"items": items, "pagination": {
        "total_pages": total_pages,
        "next_page": "https://www.protocols.io/api/v3/protocols?page_id=2",
        "current_page": 2,
    }}


def test_list_items_extracts_item_array():
    src = ProtocolsIoSource()
    body = _v3_list_body([{"id": 1}, {"id": 2}])
    assert src._list_items(body) == [{"id": 1}, {"id": 2}]


def test_list_items_falls_back_to_protocols_key():
    src = ProtocolsIoSource()
    assert src._list_items({"protocols": [{"id": 9}]}) == [{"id": 9}]


def test_total_pages_reads_pagination_and_defaults_to_one():
    src = ProtocolsIoSource()
    assert src._total_pages(_v3_list_body([], total_pages=9)) == 9
    # A URL-string next_page must NOT be mistaken for a page count; absent/invalid
    # total_pages falls back to 1 (single page) rather than looping forever.
    assert src._total_pages({"pagination": {"next_page": "http://x/page_id=2"}}) == 1
    assert src._total_pages({}) == 1


def test_search_is_double_locked():
    src = ProtocolsIoSource()
    with pytest.raises(RuntimeError, match="gated"):
        src.search("western blot")  # allow_network defaults False


def test_search_licence_lock_blocks_when_not_confirmed(monkeypatch):
    """Second lock: allow_network=True alone must not be enough — a bulk sweep
    also needs a confirmed protocols.io licence."""
    from astor.config import settings
    monkeypatch.setattr(settings, "protocols_io_licensed", False)
    src = ProtocolsIoSource()
    with pytest.raises(RuntimeError, match="PROTOCOLS_IO_LICENSED"):
        src.search("western blot", allow_network=True)


def test_search_token_lock_blocks_when_licensed_but_no_token(monkeypatch):
    """Third lock: even licensed, a missing token must still block — and no real
    network call happens, since the guard raises before httpx is touched."""
    from astor.config import settings
    monkeypatch.setattr(settings, "protocols_io_licensed", True)
    monkeypatch.setattr(settings, "protocols_io_token", None)
    src = ProtocolsIoSource()
    with pytest.raises(RuntimeError, match="TOKEN"):
        src.search("western blot", allow_network=True)


# --------------------------------------------------------------------------- #
# Task 3: ProtocolsIoSource._search_network() — paged httpx loop with test seam
# --------------------------------------------------------------------------- #
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

def test_search_network_paginates_across_pages_until_total_pages():
    """Full page + total_pages>1 must advance to page 2; a short final page ends
    it. Drives on total_pages, since live next_page is a URL string."""
    src = ProtocolsIoSource()
    pages = [
        {"items": [{"id": 1}, {"id": 2}],
         "pagination": {"total_pages": 2, "next_page": "http://x/page_id=2"}},
        {"items": [{"id": 3}],
         "pagination": {"total_pages": 2, "next_page": None}},
    ]
    client = _FakeClient(pages)
    items = src._search_network("western blot", limit=100, page_size=2,
                                peer_reviewed_only=False, client=client, sleep_between=0)
    assert [it["id"] for it in items] == [1, 2, 3]
    assert [c["page_id"] for c in client.calls] == [1, 2]  # advanced by integer counter

def test_search_network_stops_on_single_page():
    """total_pages=1 must not attempt a second request (would IndexError the fake)."""
    src = ProtocolsIoSource()
    client = _FakeClient([{"items": [{"id": 1}], "pagination": {"total_pages": 1}}])
    items = src._search_network("x", limit=100, page_size=50,
                                peer_reviewed_only=False, client=client, sleep_between=0)
    assert [it["id"] for it in items] == [1]
    assert len(client.calls) == 1

def test_search_network_respects_limit():
    src = ProtocolsIoSource()
    pages = [{"items": [{"id": i} for i in range(50)],
              "pagination": {"total_pages": 5}}]
    client = _FakeClient(pages)
    items = src._search_network("x", limit=10, page_size=50,
                                peer_reviewed_only=False, client=client, sleep_between=0)
    assert len(items) == 10

def test_search_network_passes_peer_reviewed_filter():
    src = ProtocolsIoSource()
    client = _FakeClient([{"items": [], "pagination": {"total_pages": 1}}])
    src._search_network("x", limit=10, page_size=50,
                        peer_reviewed_only=True, client=client, sleep_between=0)
    assert client.calls[0].get("peer_reviewed") == 1


# --------------------------------------------------------------------------- #
# Task 4: Category seed loader
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Task 5: Raw-payload persistence to disk
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Task 6: Shortlist stage + run_from_search sweepable invariant
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Task 7: Serving-basis gate override + run manifest
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Task 8: run_harvest orchestrator (offline-drivable) + hard cap
# --------------------------------------------------------------------------- #
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

def test_run_harvest_cap_breaks_across_categories(tmp_path):
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest
    # Category 1 alone (5 candidates) exceeds cap=3; category 2's ids (>=100) must
    # never be reached — proves the outer-loop break, not just the inner one.
    seeds = [
        CategorySeed("c1", "C1", ["q1"]),
        CategorySeed("c2", "C2", ["q2"]),
    ]
    catalog1 = {i: {"id": i, "version_id": 1, "stats": {"number_of_votes": i}} for i in range(5)}
    catalog2 = {i: {"id": i, "version_id": 1, "stats": {"number_of_votes": i}} for i in range(100, 105)}
    def fake_search(q):
        return list(catalog1.values()) if q == "q1" else list(catalog2.values())
    fetched_pids = []
    def fake_fetch(pid):
        fetched_pids.append(pid)
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}
    store = RawStore(tmp_path)
    _, _, m = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=3,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0)
    assert m.fetched == 3
    assert all(pid < 100 for pid in fetched_pids)  # category 2 never fetched

def test_run_harvest_throttles_live_fetches_only(tmp_path, monkeypatch):
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest
    seeds = [CategorySeed("c", "C", ["q"])]
    catalog = {i: {"id": i, "version_id": 1, "stats": {"number_of_votes": i}} for i in range(3)}
    def fake_search(q): return list(catalog.values())
    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}
    sleep_calls = []
    monkeypatch.setattr("astor.protocols.harvest.time.sleep", lambda s: sleep_calls.append(s))
    store = RawStore(tmp_path)
    _, _, m = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=1.0)
    assert m.fetched == 3
    assert sleep_calls == [1.0, 1.0, 1.0]  # once per live fetch
    # idempotent re-run: everything cached, no network, so no sleep calls at all
    sleep_calls.clear()
    _, _, m2 = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=1.0)
    assert m2.fetched == 0
    assert sleep_calls == []


# --------------------------------------------------------------------------- #
# Final-review fixes: orchestrator double-lock, map-stage error isolation,
# persisted search pages, and a written run manifest.
# --------------------------------------------------------------------------- #
def test_run_harvest_enforces_licence_lock_before_any_fetching(tmp_path, monkeypatch):
    """The double lock must be asserted by the orchestrator itself, not just by
    the default search_fn/fetch_fn — an injected fake must not be able to bypass
    it. Nothing should be fetched once the lock trips."""
    from astor.config import settings
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    monkeypatch.setattr(settings, "protocols_io_licensed", False)
    seeds = [CategorySeed("c", "C", ["q"])]
    store = RawStore(tmp_path)

    fetch_calls = []

    def fake_search(q):
        return [{"id": 1, "version_id": 1, "stats": {}}]

    def fake_fetch(pid):
        fetch_calls.append(pid)
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    with pytest.raises(RuntimeError, match="PROTOCOLS_IO_LICENSED"):
        harvest.run_harvest(
            seeds, source=None, store=store, n_per_category=10, cap=1000,
            allow_network=True, search_fn=fake_search, fetch_fn=fake_fetch,
            sleep_between=0,
        )
    assert fetch_calls == []


def test_run_harvest_offline_default_is_unaffected_by_the_lock(tmp_path):
    """Existing offline tests pass allow_network defaulting False, so the new
    top-of-function lock check must be a no-op for them."""
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("c", "C", ["q"])]
    store = RawStore(tmp_path)

    def fake_search(q):
        return [{"id": 1, "version_id": 1, "stats": {}}]

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    _, _, m = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
    )
    assert m.fetched == 1


def test_run_harvest_skips_corrupt_cached_detail_and_keeps_going(tmp_path):
    """A corrupt cached detail file must not abort the whole run — it should be
    skipped and counted as an error, while other records still produce output."""
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("c", "C", ["q"])]
    catalog = {
        1: {"id": 1, "version_id": 1, "stats": {"number_of_votes": 5}},
        2: {"id": 2, "version_id": 1, "stats": {"number_of_votes": 1}},
    }

    def fake_search(q):
        return list(catalog.values())

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    # Pre-seed a corrupt cache entry for id=1 so has_detail() is True but
    # read_detail() blows up.
    detail_path = store.detail_path("1", "1")
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text("{not valid json", encoding="utf-8")

    _, _, m = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch,
        sleep_between=0,
    )
    assert m.errors == 1
    assert m.skipped_cached == 0       # the corrupt entry was not counted as a hit
    assert m.fetched == 1              # id=2 still fetched
    assert m.link_out == 1             # id=2 still mapped through (UNKNOWN -> link-out)


def test_map_gate_rank_skips_records_that_fail_to_map():
    """A single malformed detail record must not abort mapping for the rest."""
    from astor.protocols.harvest import map_gate_rank

    details = [_detail(1), "not-a-dict-payload", _detail(2)]
    servable, link_out = map_gate_rank(
        details, {}, serving_basis="commercial-licence:pio-2026")
    assert len(servable) == 2


def test_run_harvest_persists_search_pages(tmp_path):
    """M4: each search query's raw items must be written to disk, not just held
    in memory, so a later map-stage fix can re-read from persisted JSON."""
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("western_blot", "Western blot", ["western blot"])]

    def fake_search(q):
        return [{"id": 1, "version_id": 1, "stats": {}}]

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
    )
    page_path = store.root / "searches" / "western_blot" / "western-blot-p1.json"
    assert page_path.exists()
    body = json.loads(page_path.read_text(encoding="utf-8"))
    assert body["items"][0]["id"] == 1


def test_run_harvest_writes_manifest_json(tmp_path):
    """M5: the run manifest must be written to <store.root>/manifest.json and
    round-trip the same counts as the returned HarvestManifest."""
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("c", "C", ["q"])]
    catalog = {
        1: {"id": 1, "version_id": 1, "stats": {"number_of_votes": 5}},
        2: {"id": 2, "version_id": 1, "stats": {"number_of_votes": 1}},
    }

    def fake_search(q):
        return list(catalog.values())

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    _, _, manifest = harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        serving_basis="commercial-licence:pio-2026",
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
        stamp="2026-08-09T00:00:00+00:00",
    )
    manifest_path = store.root / "manifest.json"
    assert manifest_path.exists()
    body = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert body["fetched"] == manifest.fetched
    assert body["servable"] == manifest.servable
    assert body["link_out"] == manifest.link_out
    assert body["errors"] == manifest.errors
    assert body["shortlisted"] == manifest.shortlisted
    assert body["skipped_cached"] == manifest.skipped_cached
    assert body["cap"] == manifest.cap
    assert body["serving_basis"] == "commercial-licence:pio-2026"
    assert body["stamp"] == "2026-08-09T00:00:00+00:00"


def test_run_harvest_custom_manifest_name(tmp_path):
    """Per-category runs share one corpus dir but each writes its own manifest,
    so provenance is not overwritten. Default name stays manifest.json."""
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("western_blot", "Western blot", ["western blot"])]

    def fake_search(q):
        return [{"id": 1, "version_id": 1, "stats": {"number_of_votes": 2}}]

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=100,
        serving_basis=None, search_fn=fake_search, fetch_fn=fake_fetch,
        sleep_between=0, manifest_name="manifest-western_blot.json",
    )
    assert (store.root / "manifest-western_blot.json").exists()
    assert not (store.root / "manifest.json").exists()  # default name not used


def test_run_harvest_manifest_omits_stamp_when_not_given(tmp_path):
    from astor.protocols.categories import CategorySeed
    from astor.protocols.raw_store import RawStore
    from astor.protocols import harvest

    seeds = [CategorySeed("c", "C", ["q"])]

    def fake_search(q):
        return [{"id": 1, "version_id": 1, "stats": {}}]

    def fake_fetch(pid):
        return {"id": pid, "version_id": 1, "url": "u", "title": "t",
                "steps": [], "materials_text": ""}

    store = RawStore(tmp_path)
    harvest.run_harvest(
        seeds, source=None, store=store, n_per_category=10, cap=1000,
        search_fn=fake_search, fetch_fn=fake_fetch, sleep_between=0,
    )
    body = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert "stamp" not in body
