"""Symptom matching is deterministic: same tables, same query, same rows.

Fixtures are tiny so each assertion names the row it expects. The embedder is a
fake so the semantic fallback is tested without a network."""
from __future__ import annotations

import pytest

from astor.api import repo
from astor.curation import text, troubleshoot
from astor.curation.loader import Category, CurationTables, Role, TroubleshootingEntry


def _e(eid, cat, symptom, cause, fix_role=None, confidence="drafted"):
    return TroubleshootingEntry(eid, cat, symptom, cause, "fix", fix_role, "yes", None,
                                confidence, "model", "", "", "")


@pytest.fixture
def tables():
    cats = {c: Category(c, c) for c in ("western_blot", "rt_qpcr", "elisa", "cell_culture_transfection")}
    roles = {
        "secondary_antibody": Role("secondary_antibody", "二抗 / secondary antibody", "no", "no", ""),
        "water": Role("water", "水", "yes", "no", ""),
        "primers": Role("primers", "引物 / primers", "no", "no", ""),
    }
    entries = [
        _e("T1", "western_blot", "完全没有条带 / no bands at all", "二抗宿主不匹配 / secondary mismatch", "secondary_antibody"),
        _e("T2", "western_blot", "背景高 / high background", "封闭不足 / insufficient blocking", None),
        _e("T3", "rt_qpcr", "NTC 有信号 / signal in the no-template control", "污染 / contamination", "water"),
        _e("T4", "rt_qpcr", "熔解曲线多峰 / multiple peaks in the melt curve", "引物二聚体 / primer dimers", "primers"),
        _e("T5", "elisa", "标准曲线不线性 / standard curve non-linear", "稀释错误 / dilution error", None),
        _e("T6", "cell_culture_transfection", "转染效率低 / low transfection efficiency", "比例不对 / wrong ratio", None),
    ]
    return CurationTables(cats, roles, [], entries)


class _FakeEmbedder:
    """Maps a few known strings to fixed unit vectors so cosine is predictable."""
    _vec = {"gel looks weird": [1.0, 0.0], "smiling": [1.0, 0.0]}

    def embed(self, texts):
        return [self._vec.get(t, [0.0, 1.0]) for t in texts]


# ------------------------------------------------------------------ tokens #
def test_tokens_split_latin_words_and_cjk_bigrams():
    assert text.tokens("No bands at all") == {"no", "bands", "at", "all"}
    assert text.tokens("没有条带") == {"没有", "有条", "条带"}
    assert text.tokens("带") == {"带"}


def test_tokens_mix_scripts_and_keep_hyphenated_terms():
    assert text.tokens("RT-qPCR 熔解曲线") == {"rt-qpcr", "熔解", "解曲", "曲线"}


def test_overlap_is_fraction_of_query_tokens_found():
    assert text.overlap({"no", "bands"}, {"no", "bands", "at", "all"}) == 1.0
    assert text.overlap({"no", "bands", "xyz"}, {"no", "bands"}) == pytest.approx(2 / 3)
    assert text.overlap(set(), {"a"}) == 0.0


# ---------------------------------------------------------------- category #
def test_classify_category_by_hint_words():
    cats = ["western_blot", "rt_qpcr", "elisa", "cell_culture_transfection"]
    assert troubleshoot.classify_category("my western blot has no bands", cats) == "western_blot"
    assert troubleshoot.classify_category("qPCR 没有 Ct", cats) == "rt_qpcr"
    assert troubleshoot.classify_category("ELISA 标准曲线不好", cats) == "elisa"
    assert troubleshoot.classify_category("转染效率很低", cats) == "cell_culture_transfection"


def test_classify_category_returns_none_when_no_hint():
    assert troubleshoot.classify_category("everything is broken", ["western_blot"]) is None


# ----------------------------------------------------------------- matcher #
def test_keyword_match_with_explicit_category(tables):
    m = troubleshoot.Matcher(tables)
    hits, kind = m.search("no bands at all", category="western_blot", limit=5)
    assert kind == "keyword"
    assert [h.entry.entry_id for h in hits][0] == "T1"
    assert all(h.entry.category_id == "western_blot" for h in hits)


def test_keyword_match_in_chinese(tables):
    m = troubleshoot.Matcher(tables)
    hits, kind = m.search("熔解曲线有好几个峰", category=None, limit=5)
    assert kind == "keyword"
    assert hits[0].entry.entry_id == "T4"


def test_category_omitted_is_classified_from_symptom(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("western blot 完全没有条带", category=None, limit=5)
    assert hits[0].entry.entry_id == "T1"
    assert all(h.entry.category_id == "western_blot" for h in hits)


def test_unknown_category_is_ignored_not_fatal(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("no bands", category="flow_cytometry", limit=5)
    assert hits[0].entry.entry_id == "T1"


def test_limit_and_ordering(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("no bands high background", category="western_blot", limit=1)
    assert len(hits) == 1
    assert hits[0].score >= troubleshoot.KEYWORD_FLOOR


def test_semantic_fallback_when_keywords_miss(tables):
    m = troubleshoot.Matcher(tables, embedder=_FakeEmbedder())
    # give T2 a symptom the fake embedder recognises
    m._doc_vectors[1] = [1.0, 0.0]
    hits, kind = m.search("gel looks weird", category="western_blot", limit=3)
    assert kind == "semantic"
    assert hits[0].entry.entry_id == "T2"


def test_keyword_only_when_no_embedder(tables):
    m = troubleshoot.Matcher(tables, embedder=None)
    hits, kind = m.search("gel looks weird", category="western_blot", limit=3)
    assert hits == [] and kind == "keyword"


def test_embedder_failure_at_build_degrades_to_keyword(tables):
    class Boom:
        def embed(self, texts): raise RuntimeError("no key")
    m = troubleshoot.Matcher(tables, embedder=Boom())
    hits, kind = m.search("no bands", category="western_blot", limit=3)
    assert kind == "keyword" and hits[0].entry.entry_id == "T1"


# ---------------------------------------------------------------- resolve #
def test_resolve_products_searches_by_role_description(monkeypatch, tables):
    calls = []
    def fake_list(session, q, category, page, page_size, **kw):
        calls.append(q)
        return [{"id": "p1", "name": "Goat anti-Rabbit IgG HRP", "brand": "X", "category": "antibodies",
                 "astor_sku": "A1", "mpn": None, "region": None, "offer_count": 1, "best_landed": None}], 1
    monkeypatch.setattr(repo, "list_products", fake_list)
    entry = tables.troubleshooting[0]  # T1, secondary_antibody
    products, owns = troubleshoot.resolve_products(object(), entry, tables)
    assert calls == ["secondary antibody"]
    assert products[0]["id"] == "p1" and owns == "no"


def test_resolve_skips_search_when_lab_owns_role(monkeypatch, tables):
    def must_not(*a, **k):
        raise AssertionError("must not search")
    monkeypatch.setattr(repo, "list_products", must_not)
    entry = tables.troubleshooting[2]  # T3, water
    products, owns = troubleshoot.resolve_products(object(), entry, tables)
    assert products == [] and owns == "yes"


def test_resolve_without_fix_role_returns_nothing(tables):
    entry = tables.troubleshooting[1]  # T2, no fix_role
    assert troubleshoot.resolve_products(object(), entry, tables) == ([], None)


def test_classify_category_tie_returns_none_instead_of_guessing():
    # '曲线' is a bigram of the elisa hint 标准曲线 and of 熔解曲线; '熔解' is an
    # rt_qpcr hint. One hit each is a tie, and a tie must not depend on set order.
    cats = ["elisa", "rt_qpcr"]
    assert troubleshoot.classify_category("曲线 熔解", cats) is None
    assert troubleshoot.classify_category("熔解曲线多峰", cats) is None
    assert troubleshoot.classify_category("qPCR 熔解曲线多峰", cats) == "rt_qpcr"
