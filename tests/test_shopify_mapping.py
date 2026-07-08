"""Offline tests for the Shopify -> ExtractedProduct mapping (no network, no key).

Canned nodes mirror real Admin GraphQL shapes for the astorscientific.us catalog:
a resold branded product with a real unit cost, and a multi-variant product with
no 'Cost per item' set (exercises the sell-price fallback + provenance flag).
"""
from __future__ import annotations

from astor.catalog.shopify_source import ShopifyConfig, map_product_node

CFG = ShopifyConfig(
    shop_domain="astor.myshopify.com",
    admin_token="test",
    mpn_metafield="custom.mpn",
    specs_metafield_namespace="specs",
)

NODE_WITH_UNIT_COST = {
    "id": "gid://shopify/Product/1",
    "title": "PBS 1X without calcium and magnesium, pH 7.4",
    "vendor": "Corning",
    "productType": "Cell Culture",
    "tags": ["buffers"],
    "metafields": {"edges": [
        {"node": {"namespace": "custom", "key": "mpn", "value": "21-040-CV"}},
        {"node": {"namespace": "specs", "key": "ph", "value": "7.4"}},
    ]},
    "variants": {"edges": [
        {"node": {
            "id": "gid://shopify/ProductVariant/11",
            "sku": "COR-21040",
            "barcode": "0001",
            "title": "500 mL",
            "price": "18.00",
            "inventoryQuantity": 200,
            "selectedOptions": [{"name": "Size", "value": "500 mL"}],
            "inventoryItem": {"unitCost": {"amount": "9.50", "currencyCode": "USD"}},
        }},
    ]},
}

NODE_NO_UNIT_COST = {
    "id": "gid://shopify/Product/2",
    "title": "2x Taq Master Mix",
    "vendor": "AstorScientific",
    "productType": "Molecular Biology",
    "tags": [],
    "metafields": {"edges": []},
    "variants": {"edges": [
        {"node": {
            "id": "gid://shopify/ProductVariant/21", "sku": "AS-TAQ-1",
            "barcode": "", "title": "1 mL", "price": "40.00",
            "inventoryQuantity": 20,
            "selectedOptions": [{"name": "Volume", "value": "1 mL"}],
            "inventoryItem": {"unitCost": None},
        }},
        {"node": {
            "id": "gid://shopify/ProductVariant/22", "sku": "",
            "barcode": "", "title": "5 mL", "price": "160.00",
            "inventoryQuantity": 0,
            "selectedOptions": [{"name": "Volume", "value": "5 mL"}],
            "inventoryItem": {"unitCost": None},
        }},
    ]},
}


def test_unit_cost_and_metafields():
    [p] = map_product_node(NODE_WITH_UNIT_COST, CFG)
    assert p.supplier_sku == "COR-21040"
    assert p.brand == "Corning"
    assert p.category == "Cell Culture"
    assert p.mpn == "21-040-CV"          # metafield beats barcode
    assert p.cost == 9.50                 # unit cost, NOT the 18.00 sell price
    assert p.currency == "USD"
    assert p.stock == 200
    assert p.pack_size == "500 mL"
    assert p.specs["ph"] == "7.4"         # specs-namespace metafield mapped
    assert p.specs["_cost_basis"] == "unit_cost"


def test_sell_price_fallback_and_variants():
    ps = map_product_node(NODE_NO_UNIT_COST, CFG)
    assert len(ps) == 2                    # one ExtractedProduct per variant
    p1, p2 = ps
    assert p1.name == "2x Taq Master Mix - 1 mL"
    assert p1.cost == 40.00                # fell back to sell price...
    assert p1.specs["_cost_basis"] == "sell_price_fallback"  # ...and flagged it
    assert p1.stock == 20
    # variant with no sku gets a stable synthetic key, never empty
    assert p2.supplier_sku == "shopify:22"
    assert p2.mpn is None


def test_graphql_url_normalizes_bare_handle():
    assert ShopifyConfig("astor", "t", api_version="2026-01").graphql_url == (
        "https://astor.myshopify.com/admin/api/2026-01/graphql.json"
    )
