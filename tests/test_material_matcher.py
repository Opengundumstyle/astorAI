import types

import pytest

from astor.db.models import ProtocolMaterialLink
from astor.protocols import material_matcher as mm

def test_link_table_shape():
    t = ProtocolMaterialLink.__table__
    assert t.name == "protocol_material_links"
    cols = set(t.columns.keys())
    assert {"protocol_id", "product_id", "material_name",
            "confidence", "kind", "method", "reviewed"} <= cols
    constraints = {c.name for c in t.constraints}
    assert "uq_protocol_material_link" in constraints


def _prod(id, name, brand=None, mpn=None, category="", specs=None):
    return types.SimpleNamespace(id=id, name=name, brand=brand, mpn=mpn,
                                 category=category, specs=specs or {})

def _protocol(materials):
    return types.SimpleNamespace(id="proto-1", materials=materials)

class _Emb:
    def embed(self, texts): return [[0.0] for _ in texts]

def test_exact_catalog_match_wins_without_embedding():
    called = {"embed": 0}
    class _E:
        def embed(self, texts): called["embed"] += 1; return [[0.0]]
    p = _protocol([{"name": "RNeasy Mini Kit", "vendor": "Qiagen", "catalog_no": "74104"}])
    exact = lambda s, v, c: _prod("prod-9", "RNeasy", brand="Qiagen", mpn="74104")
    out = mm.match_protocol_materials(None, p, _E(),
                                      exact_lookup=exact, ann_candidates=lambda *a: [])
    assert len(out) == 1
    assert (out[0].product_id, out[0].kind, out[0].method) == ("prod-9", "exact", "catalog")
    assert out[0].confidence == 1.0
    assert called["embed"] == 0            # exact path never embeds

def test_semantic_keeps_best_confident_candidate():
    p = _protocol([{"name": "TRIzol Reagent"}])
    # two neighbours: one clearly exact-level (dist 0.02 -> sim 0.98), one below substitute
    cands = [(_prod("hi", "TRIzol"), 0.02), (_prod("lo", "something else"), 0.5)]
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cands)
    assert len(out) == 1
    assert out[0].product_id == "hi"
    assert out[0].method == "vector+rules"
    assert out[0].kind in ("exact", "substitute")

def test_semantic_drops_when_all_below_threshold():
    p = _protocol([{"name": "obscure thing"}])
    cands = [(_prod("x", "unrelated"), 0.6), (_prod("y", "also unrelated"), 0.7)]
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cands)
    assert out == []

def test_brand_mpn_agreement_bonus_lifts_to_exact():
    # A semantic candidate that also shares brand+mpn gets +0.50 -> exact.
    p = _protocol([{"name": "Widget", "vendor": "Acme", "catalog_no": "W-1"}])
    # exact_lookup misses (e.g. product brand cased differently), but ANN surfaces it
    cand = [(_prod("z", "Widget", brand="Acme", mpn="W-1"), 0.55)]  # sim 0.45 + 0.50 = 0.95
    out = mm.match_protocol_materials(None, p, _Emb(),
                                      exact_lookup=lambda *a: None,
                                      ann_candidates=lambda s, v, n: cand)
    assert out[0].kind == "exact"

def test_duplicate_material_names_matched_once():
    calls = {"n": 0}
    def ann(s, v, n): calls["n"] += 1; return []
    p = _protocol([{"name": "TRIzol"}, {"name": "trizol"}, {"name": "TRIzol"}])
    mm.match_protocol_materials(None, p, _Emb(), exact_lookup=lambda *a: None, ann_candidates=ann)
    assert calls["n"] == 1                 # case-insensitive dedupe

def test_material_view_maps_vendor_and_catalog_to_brand_mpn():
    v = mm._material_view("X", "Acme", "W-1")
    assert v.brand == "Acme" and v.mpn == "W-1"
