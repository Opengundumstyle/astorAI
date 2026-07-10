"""Shopify house-catalog source: Admin GraphQL -> ExtractedProduct DTOs.

This is an INBOUND channel adapter, the mirror of the order-side Shopify adapter.
It reads Shopify types (product/variant/inventoryItem) and emits the SAME
`ExtractedProduct` the CSV/PDF extractors emit, so everything downstream
(normalize -> upsert -> embed -> match) runs unchanged and the engine stays
free of Shopify types. Shopify is a feed, not a source of truth in the engine;
its data lands in Postgres and the agent queries Postgres.

Mapping (one Shopify VARIANT -> one ExtractedProduct/offer):
  supplier_sku   <- variant.sku (fallback: numeric variant id)
  name           <- product.title (+ variant.title when not "Default Title")
  brand          <- product.vendor
  category       <- product.productType (fallback: first tag); canon'd downstream
  mpn            <- metafield[SHOPIFY_MPN_METAFIELD] if set, else variant.barcode
  pack_size      <- variant.title / selectedOptions
  cost           <- variant.inventoryItem.unitCost.amount  ("Cost per item")
                    NB: this is Astor's COST, not the customer sell price.
                    If unitCost is empty, we fall back to variant.price and tag
                    specs["_cost_basis"]="sell_price_fallback" so landed-cost
                    math never silently treats a sell price as a cost. See the
                    DECISION note in the module for the semantics to ratify.
  currency       <- unitCost.currencyCode (fallback: shop currency, default USD)
  stock          <- variant.inventoryQuantity  (live for the stock-holding house
                    catalog; genuinely 'authorized'/'deep' tier)
  lead_time_days <- None (Shopify has no native field; a metafield can fill later)
  specs          <- selectedOptions + configured metafields + provenance flags

DECISION (yours): the Shopify `vendor` is the brand/manufacturer, not Astor's
upstream supplier. All Shopify-sourced offers are attached to a single Supplier
row ("the Shopify channel as a sourcing origin"); real upstream suppliers get
their own feeds later. Override SHOPIFY_SUPPLIER_* to change this modeling.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from astor.catalog.schemas import ExtractedProduct
from astor.config import settings

log = logging.getLogger(__name__)

_DEFAULT_VARIANT_TITLE = "Default Title"

_PRODUCTS_QUERY = """
query Products($cursor: String, $mfNamespace: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        title
        vendor
        productType
        tags
        metafields(first: 25, namespace: $mfNamespace) {
          edges { node { namespace key value } }
        }
        variants(first: 100) {
          edges {
            node {
              id
              sku
              barcode
              title
              price
              inventoryQuantity
              selectedOptions { name value }
              inventoryItem { unitCost { amount currencyCode } }
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class ShopifyConfig:
    """Auth model, post Jan-2026 Shopify changes.

    Admin-created custom apps (static shpat_ token) were deprecated 2026-01-01.
    New apps are created in the Dev Dashboard and issue a Client ID + Client
    Secret; you exchange those for a 24h access token via the client-credentials
    grant (requires app and store in the SAME Shopify org). This adapter supports
    both: pass a legacy static token if you have an existing admin app, otherwise
    pass client_id/client_secret and it fetches+caches the token itself.
    """
    shop_domain: str          # "astor" | "astor.myshopify.com" | full admin host
    admin_token: str | None = None            # legacy static token (existing apps)
    client_id: str | None = None              # Dev Dashboard app
    client_secret: str | None = None          # Dev Dashboard app (secret)
    api_version: str = "2026-01"
    shop_currency: str = "USD"
    mpn_metafield: str | None = None          # "namespace.key", e.g. "custom.mpn"
    specs_metafield_namespace: str | None = None

    @property
    def _host(self) -> str:
        host = self.shop_domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        host = host.split("/")[0]
        # The Admin API only answers on *.myshopify.com; map a bare handle.
        # A custom domain (astorscientific.us) will NOT work here — use the handle.
        if not host.endswith(".myshopify.com") and "." not in host:
            host = f"{host}.myshopify.com"
        return host

    @property
    def graphql_url(self) -> str:
        return f"https://{self._host}/admin/api/{self.api_version}/graphql.json"

    @property
    def token_url(self) -> str:
        return f"https://{self._host}/admin/oauth/access_token"

    @classmethod
    def from_settings(cls) -> "ShopifyConfig":
        if not settings.shopify_shop_domain:
            raise RuntimeError("Set SHOPIFY_SHOP_DOMAIN (the *.myshopify.com handle, not the .us domain).")
        has_static = bool(settings.shopify_admin_token)
        has_client = bool(settings.shopify_client_id and settings.shopify_client_secret)
        if not (has_static or has_client):
            raise RuntimeError(
                "Provide EITHER SHOPIFY_ADMIN_TOKEN (legacy app) OR "
                "SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (Dev Dashboard app). "
                "Keep all of these in .env, never in code or chat."
            )
        return cls(
            shop_domain=settings.shopify_shop_domain,
            admin_token=settings.shopify_admin_token,
            client_id=settings.shopify_client_id,
            client_secret=settings.shopify_client_secret,
            api_version=settings.shopify_api_version,
            shop_currency=settings.shopify_shop_currency,
            mpn_metafield=settings.shopify_mpn_metafield,
            specs_metafield_namespace=settings.shopify_specs_metafield_namespace,
        )


# --------------------------------------------------------------------------- #
# Pure mapping (no network) -- unit-tested offline against canned nodes.
# --------------------------------------------------------------------------- #
def _gid_tail(gid: str) -> str:
    return gid.rsplit("/", 1)[-1] if gid else gid


def _metafields(node: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for edge in (node.get("metafields", {}) or {}).get("edges", []) or []:
        mf = edge.get("node") or {}
        ns, key, val = mf.get("namespace"), mf.get("key"), mf.get("value")
        if ns and key and val is not None:
            out[f"{ns}.{key}"] = val
    return out


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_product_node(node: dict, cfg: ShopifyConfig) -> list[ExtractedProduct]:
    """One Shopify product node -> one ExtractedProduct per variant."""
    title = (node.get("title") or "").strip()
    vendor = (node.get("vendor") or "").strip() or None
    category = (node.get("productType") or "").strip() or None
    if not category:
        tags = node.get("tags") or []
        category = (tags[0].strip() if tags else None) or None
    mfs = _metafields(node)
    mpn_from_mf = mfs.get(cfg.mpn_metafield) if cfg.mpn_metafield else None

    base_specs: dict = {}
    if cfg.specs_metafield_namespace:
        for k, v in mfs.items():
            if k.startswith(f"{cfg.specs_metafield_namespace}."):
                base_specs[k.split(".", 1)[1]] = v

    out: list[ExtractedProduct] = []
    for vedge in (node.get("variants", {}) or {}).get("edges", []) or []:
        v = vedge.get("node") or {}
        vtitle = (v.get("title") or "").strip()
        options = {o.get("name"): o.get("value") for o in (v.get("selectedOptions") or [])}

        pack_size = None
        if vtitle and vtitle != _DEFAULT_VARIANT_TITLE:
            pack_size = vtitle
        elif options:
            pack_size = " / ".join(f"{k}:{val}" for k, val in options.items() if k and val)

        name = title if (not vtitle or vtitle == _DEFAULT_VARIANT_TITLE) else f"{title} - {vtitle}"

        unit_cost = ((v.get("inventoryItem") or {}).get("unitCost") or {})
        cost = _to_float(unit_cost.get("amount"))
        currency = unit_cost.get("currencyCode") or cfg.shop_currency

        specs = dict(base_specs)
        specs.update({k: val for k, val in options.items() if k and val})

        if cost is None:
            # No "Cost per item" set in Shopify -> fall back to sell price, but
            # tag it so landed-cost never treats a sell price as a supplier cost.
            price = _to_float(v.get("price"))
            cost = price
            if price is not None:
                specs["_cost_basis"] = "sell_price_fallback"
        else:
            specs["_cost_basis"] = "unit_cost"

        mpn = mpn_from_mf or (v.get("barcode") or "").strip() or None
        supplier_sku = (v.get("sku") or "").strip() or f"shopify:{_gid_tail(v.get('id',''))}"

        out.append(
            ExtractedProduct(
                supplier_sku=supplier_sku,
                name=name or supplier_sku,
                category=category,
                brand=vendor,
                mpn=mpn,
                pack_size=pack_size,
                cost=cost,
                currency=currency,
                stock=v.get("inventoryQuantity"),
                lead_time_days=None,
                specs=specs,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Network client (cursor pagination). Kept thin; retries are a later concern.
# --------------------------------------------------------------------------- #
class ShopifySource:
    """Pulls the full product catalog via the Admin GraphQL API."""

    def __init__(self, cfg: ShopifyConfig | None = None) -> None:
        self.cfg = cfg or ShopifyConfig.from_settings()
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _access_token(self) -> str:
        """Legacy static token if present, else a cached client-credentials token.

        Dev Dashboard apps issue Client ID/Secret; exchange them for a token that
        expires in ~24h. Cached and refreshed with a safety margin, so a long
        ingest never fails mid-run on expiry.
        """
        if self.cfg.admin_token:
            return self.cfg.admin_token
        if self._token and time.time() < self._token_expiry - 120:
            return self._token
        body = json.dumps({
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "grant_type": "client_credentials",
        }).encode()
        req = urllib.request.Request(
            self.cfg.token_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:500]
            raise RuntimeError(
                f"Shopify token exchange {e.code} at {self.cfg.token_url}: {detail}. "
                "If 'shop_not_permitted', the app and store are in different Shopify orgs."
            ) from e
        self._token = data["access_token"]
        self._token_expiry = time.time() + float(data.get("expires_in", 86400))
        return self._token

    def _post(self, variables: dict) -> dict:
        body = json.dumps({"query": _PRODUCTS_QUERY, "variables": variables}).encode()
        req = urllib.request.Request(
            self.cfg.graphql_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self._access_token(),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:500]
            raise RuntimeError(f"Shopify API {e.code} at {self.cfg.graphql_url}: {detail}") from e
        if payload.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {payload['errors']}")
        return payload["data"]

    def fetch_nodes(self, limit: int | None = None) -> list[dict]:
        """Return raw product nodes (paginated)."""
        ns = self.cfg.specs_metafield_namespace or (
            self.cfg.mpn_metafield.split(".", 1)[0] if self.cfg.mpn_metafield else None
        )
        nodes: list[dict] = []
        cursor: str | None = None
        while True:
            data = self._post({"cursor": cursor, "mfNamespace": ns})
            conn = data["products"]
            nodes.extend(edge["node"] for edge in conn["edges"])
            if limit and len(nodes) >= limit:
                return nodes[:limit]
            if not conn["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = conn["pageInfo"]["endCursor"]

    def extract(self, limit: int | None = None) -> list[ExtractedProduct]:
        items: list[ExtractedProduct] = []
        for node in self.fetch_nodes(limit=limit):
            items.extend(map_product_node(node, self.cfg))
        log.info("shopify: %d variants extracted", len(items))
        return items
