"""Buyer-facing Astor SKU: the variant SKU on the Astor storefront listing.

History: M1 derived `ASR-<6 hex of the UUID>` from the product id. That code
exists nowhere on Shopify, so every SKU the assistant quoted failed to match the
store. The SKU a shopper can actually see is `supplier_offers.supplier_sku` on the
Shopify channel offer (`settings.shopify_supplier_name`), so that is what we expose.

Never an upstream supplier's part number: origin confidentiality (roles.py) is
enforced here as well as at the gate, so a future caller cannot leak one by
passing the wrong offer.
"""
from __future__ import annotations

# shopify_source substitutes this key when a variant has no SKU; it is an
# internal handle, not something printed on the listing.
_SYNTHETIC_PREFIX = "shopify:"


def pick_storefront_sku(channel_skus) -> str | None:
    """Choose the listing SKU from the SKUs of a product's channel offers.

    Synthetic keys are dropped. Sorted so two channel offers on one product
    (possible when variants were merged on brand+mpn) resolve the same way
    every call.
    """
    candidates = sorted(
        sku for sku in channel_skus if sku and not sku.startswith(_SYNTHETIC_PREFIX)
    )
    return candidates[0] if candidates else None


def storefront_sku(offers, channel_supplier: str) -> str | None:
    """SKU of the Shopify channel offer among `offers`, or None.

    `offers` are SupplierOffer rows (or anything with `.supplier_sku` and
    `.supplier.name`).
    """
    return pick_storefront_sku(
        o.supplier_sku for o in offers
        if o.supplier is not None and o.supplier.name == channel_supplier
    )
