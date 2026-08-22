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
    # Shape is roles._PRODUCT_BUYER_KEYS — brand/mpn/region are withheld by the gate.
    assert result["products"] == [{"id": "p1", "astor_sku": "ASR-1", "name": "BCA Kit",
                                   "category": "antibodies", "offer_count": 0,
                                   "best_landed": None}]
    assert items == [tools.ReferencedItem("product", "p1", "BCA Kit")]

def test_search_protocols_refs(monkeypatch):
    monkeypatch.setattr(repo, "list_protocols",
        lambda s, q, page, page_size: ([{"id": "x1", "title": "Western Blot", "source": "protocols.io",
                                         "rank_score": 8.1, "product_count": 5,
                                         "first_author": "Karyna Tarasova"}], 1))
    result, items = tools.dispatch(_sess(), "search_protocols", {"query": "western"})
    assert result["protocols"] == [{"id": "x1", "title": "Western Blot", "product_count": 5,
                                    "first_author": "Karyna Tarasova"}]
    assert items == [tools.ReferencedItem("protocol", "x1", "Western Blot (Karyna Tarasova)")]


def test_search_protocols_without_author_keeps_bare_title(monkeypatch):
    monkeypatch.setattr(repo, "list_protocols",
        lambda s, q, page, page_size: ([{"id": "x2", "title": "ELISA", "source": "protocols.io",
                                         "rank_score": 1.0, "product_count": 2,
                                         "first_author": None}], 1))
    result, items = tools.dispatch(_sess(), "search_protocols", {"query": "elisa"})
    assert result["protocols"] == [{"id": "x2", "title": "ELISA", "product_count": 2,
                                    "first_author": None}]
    assert items == [tools.ReferencedItem("protocol", "x2", "ELISA")]

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

def test_schemas_cover_all_seven_tools():
    names = {t["name"] for t in tools.TOOL_SCHEMAS}
    assert names == {"search_products", "search_protocols", "protocol_products",
                     "product_protocols", "product_detail", "protocols_by_material",
                     "flag_sourcing_request"}

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


def test_flag_sourcing_request_server_identity_wins(monkeypatch):
    captured = {}
    def fake_create(session, *, requested_item, context, shop, customer_id, email):
        captured.update(requested_item=requested_item, context=context, shop=shop,
                        customer_id=customer_id, email=email)
        return {"id": "1", "requested_item": requested_item, "status": "new"}
    monkeypatch.setattr(repo, "create_sourcing_request", fake_create)
    # model tries to spoof shop/customer_id in args; server request_context must win.
    result, items = tools.dispatch(
        _sess(), "flag_sourcing_request",
        {"item": "Anti-FLAG antibody", "context": "WB for FLAG", "email": "a@b.com",
         "shop": "EVIL", "customer_id": "EVIL"},
        request_context={"shop": "astor-dev.myshopify.com", "customer_id": "cust-9"})
    assert result == {"logged": True, "item": "Anti-FLAG antibody", "status": "new"}
    assert items == []
    assert captured["shop"] == "astor-dev.myshopify.com"   # not "EVIL"
    assert captured["customer_id"] == "cust-9"              # not "EVIL"
    assert captured["email"] == "a@b.com"
    assert captured["context"] == "WB for FLAG"


def test_flag_sourcing_request_demo_path_null_identity(monkeypatch):
    captured = {}
    def fake_create(session, *, requested_item, context, shop, customer_id, email):
        captured.update(shop=shop, customer_id=customer_id, email=email)
        return {"id": "1", "requested_item": requested_item, "status": "new"}
    monkeypatch.setattr(repo, "create_sourcing_request", fake_create)
    result, items = tools.dispatch(_sess(), "flag_sourcing_request", {"item": "X"})  # no request_context
    assert result["logged"] is True
    assert captured["shop"] is None and captured["customer_id"] is None and captured["email"] is None


# --- Origin confidentiality -------------------------------------------------- #
# roles.py states the rule: buyers must never receive product origin, supplier
# identity, manufacturer brand/MPN, or cost internals. The chat tools are a buyer
# surface, so what they hand the model is bounded by the same allowlist. These
# guard the boundary, not the prompt: a prompt rule is a request, withholding the
# data is a guarantee.

_PRODUCT_ROW = {
    "id": "p1", "name": "Anti-FLAG Antibody", "brand": "TribioScience",
    "category": "antibodies", "astor_sku": "ASR-1", "mpn": "TB-4471",
    "region": "CN", "origin": "CN", "offer_count": 2, "best_landed": 91.0,
}

_DETAIL_ROW = {
    "id": "p1", "name": "Anti-FLAG Antibody", "brand": "TribioScience",
    "category": "antibodies", "mpn": "TB-4471", "region": "CN",
    "specs": {"Size": "100 ug", "_cost_basis": "unit_cost"},
    "equivalents": [],
}

_FORBIDDEN = ("brand", "mpn", "origin", "region", "_cost_basis")


def _leaked_keys(payload) -> set[str]:
    """Every forbidden key anywhere in a nested payload, at any depth."""
    found = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in _FORBIDDEN:
                found.add(k)
            found |= _leaked_keys(v)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            found |= _leaked_keys(v)
    return found


def test_search_products_never_exposes_manufacturer_brand(monkeypatch):
    monkeypatch.setattr(repo, "list_products",
        lambda s, q, category, page, page_size: ([dict(_PRODUCT_ROW)], 1))
    result, _ = tools.dispatch(_sess(), "search_products", {"query": "FLAG"})
    assert "brand" not in result["products"][0]
    assert result["products"][0]["name"] == "Anti-FLAG Antibody"


def test_product_detail_never_exposes_manufacturer_brand(monkeypatch):
    monkeypatch.setattr(repo, "get_product_detail", lambda s, pid: dict(_DETAIL_ROW))
    result, _ = tools.dispatch(_sess(), "product_detail", {"product_id": "p1"})
    assert "brand" not in result
    assert result["name"] == "Anti-FLAG Antibody"


def test_product_detail_strips_internal_spec_keys_but_keeps_real_ones(monkeypatch):
    monkeypatch.setattr(repo, "get_product_detail", lambda s, pid: dict(_DETAIL_ROW))
    result, _ = tools.dispatch(_sess(), "product_detail", {"product_id": "p1"})
    assert result["specs"] == {"Size": "100 ug"}


def test_no_product_tool_leaks_confidential_fields_at_any_depth(monkeypatch):
    """Guard for tools that don't exist yet: a future tool that forwards a raw repo
    row fails here, which a per-tool assertion would not catch."""
    monkeypatch.setattr(repo, "list_products",
        lambda s, q, category, page, page_size: ([dict(_PRODUCT_ROW)], 1))
    monkeypatch.setattr(repo, "get_product_detail", lambda s, pid: dict(_DETAIL_ROW))
    monkeypatch.setattr(repo, "protocol_materials",
        lambda s, pid, *, reviewed_only, limit: {
            "protocol_title": "WB", "source_uri": "u",
            "materials": [{"material_name": "BCA", "product_id": "p9",
                           "product_name": "BCA Kit", "brand": "GenDEPOT",
                           "confidence": 0.9, "kind": "exact"}]})
    monkeypatch.setattr(repo, "product_protocols",
        lambda s, pid, *, reviewed_only, limit: {
            "product_name": "BCA Kit", "brand": "GenDEPOT",
            "protocols": [{"protocol_id": "x1", "title": "WB"}]})

    calls = [
        ("search_products", {"query": "FLAG"}),
        ("product_detail", {"product_id": "p1"}),
        ("protocol_products", {"protocol_id": "x1"}),
        ("product_protocols", {"product_id": "p1"}),
    ]
    for name, args in calls:
        result, _ = tools.dispatch(_sess(), name, args)
        assert _leaked_keys(result) == set(), f"{name} leaked {_leaked_keys(result)}"


def test_system_prompt_forbids_naming_manufacturers():
    from astor.chat.agent import SYSTEM
    lowered = SYSTEM.lower()
    assert "confidential" in lowered
    assert "manufacturer" in lowered
