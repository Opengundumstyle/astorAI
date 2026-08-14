import types
import pytest
from astor.chat import tools
from astor.api import repo

def _sess(): return object()  # session unused; repo is monkeypatched

def test_search_products_is_compact_and_refs(monkeypatch):
    monkeypatch.setattr(repo, "list_products",
        lambda s, q, category, page, page_size: (
            [{"id": "p1", "name": "BCA Kit", "brand": "Astor", "category": "antibodies",
              "astor_sku": "ASR-1", "mpn": None, "region": None, "offer_count": 0, "best_landed": None}], 1))
    result, items = tools.dispatch(_sess(), "search_products", {"query": "BCA"})
    assert result["products"] == [{"id": "p1", "name": "BCA Kit", "brand": "Astor", "category": "antibodies"}]
    assert items == [tools.ReferencedItem("product", "p1", "BCA Kit")]

def test_search_protocols_refs(monkeypatch):
    monkeypatch.setattr(repo, "list_protocols",
        lambda s, q, page, page_size: ([{"id": "x1", "title": "Western Blot", "source": "protocols.io",
                                         "rank_score": 8.1, "product_count": 5}], 1))
    result, items = tools.dispatch(_sess(), "search_protocols", {"query": "western"})
    assert result["protocols"] == [{"id": "x1", "title": "Western Blot", "product_count": 5}]
    assert items == [tools.ReferencedItem("protocol", "x1", "Western Blot")]

def test_protocol_products_refs_protocol_and_products(monkeypatch):
    monkeypatch.setattr(repo, "protocol_materials",
        lambda s, pid, *, reviewed_only, limit: {
            "protocol_title": "WB", "source_uri": "u",
            "materials": [{"material_name": "BCA", "product_id": "p9", "product_name": "BCA Kit",
                           "brand": "Astor", "confidence": 0.86, "kind": "exact"}]} if pid == "x1" else None)
    result, items = tools.dispatch(_sess(), "protocol_products", {"protocol_id": "x1"})
    assert result["protocol_title"] == "WB"
    assert result["products"][0] == {"product_id": "p9", "product_name": "BCA Kit",
                                     "material_name": "BCA", "confidence": 0.86}
    assert tools.ReferencedItem("protocol", "x1", "WB") in items
    assert tools.ReferencedItem("product", "p9", "BCA Kit") in items

def test_unknown_id_returns_error_not_raise(monkeypatch):
    monkeypatch.setattr(repo, "protocol_materials", lambda s, pid, *, reviewed_only, limit: None)
    result, items = tools.dispatch(_sess(), "protocol_products", {"protocol_id": "nope"})
    assert "error" in result and items == []

def test_tool_exception_is_caught(monkeypatch):
    def boom(*a, **k): raise ValueError("db down")
    monkeypatch.setattr(repo, "list_products", boom)
    result, items = tools.dispatch(_sess(), "search_products", {"query": "x"})
    assert "error" in result and items == []

def test_schemas_cover_all_six_tools():
    names = {t["name"] for t in tools.TOOL_SCHEMAS}
    assert names == {"search_products", "search_protocols", "protocol_products",
                     "product_protocols", "product_detail", "protocols_by_material"}

def test_unknown_tool_name_returns_error():
    result, items = tools.dispatch(_sess(), "nonexistent_tool", {})
    assert "error" in result and items == []

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


def test_protocols_by_material_caps_limit(monkeypatch):
    captured = {}

    def fake_protocols_by_material(s, material, *, limit):
        captured["limit"] = limit
        return {"total": 0, "protocols": []}

    monkeypatch.setattr(repo, "protocols_by_material", fake_protocols_by_material)
    tools.dispatch(_sess(), "protocols_by_material", {"material": "x", "limit": 999})
    assert captured["limit"] == 50
