"""Shopify App Proxy endpoints — reachable only via a signed Shopify proxy request.

The Proxy URL configured in the Shopify app points at `https://<host>/proxy`, and
Shopify appends the storefront subpath, so `store/apps/astor/ping` -> `/proxy/ping`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from astor.api.shopify_proxy import verify_app_proxy

router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])


@router.get("/ping")
def ping(ctx: dict = Depends(verify_app_proxy)) -> dict:
    """Proof endpoint: returns the verified shop domain from a signed proxy request."""
    return {"ok": True, "shop": ctx["shop"]}
