"""`astor_sku` is the SKU printed on the Astor storefront listing: the variant SKU of
the Shopify channel offer. It is NOT derived from the product UUID (the M1
placeholder), and it is never an upstream supplier's part number."""
from types import SimpleNamespace

from astor.api.skus import storefront_sku

CHANNEL = "Astor Shopify (US)"


def _offer(sku, supplier=CHANNEL):
    return SimpleNamespace(supplier_sku=sku, supplier=SimpleNamespace(name=supplier))


def test_shopify_channel_offer_sku_is_the_storefront_sku():
    assert storefront_sku([_offer("BX23-0010A")], CHANNEL) == "BX23-0010A"


def test_upstream_supplier_sku_is_never_exposed():
    assert storefront_sku([_offer("VZ-EX-001", supplier="Sample CN")], CHANNEL) is None


def test_channel_offer_wins_over_upstream_offer_regardless_of_order():
    offers = [_offer("VZ-EX-001", supplier="Sample CN"), _offer("AS-707003")]
    assert storefront_sku(offers, CHANNEL) == "AS-707003"


def test_synthetic_key_for_a_blank_shopify_sku_is_not_a_sku():
    # shopify_source falls back to "shopify:<variant id>" when the variant has no SKU.
    assert storefront_sku([_offer("shopify:4412")], CHANNEL) is None


def test_no_offers_means_no_sku():
    assert storefront_sku([], CHANNEL) is None


def test_two_channel_offers_pick_deterministically():
    offers = [_offer("ZZ-2"), _offer("AA-1")]
    assert storefront_sku(offers, CHANNEL) == "AA-1"
    assert storefront_sku(list(reversed(offers)), CHANNEL) == "AA-1"
