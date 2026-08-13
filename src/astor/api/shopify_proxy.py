"""Shopify App Proxy request verification.

Shopify signs every App-Proxy'd storefront request with a `signature` query param:
hex HMAC-SHA256 over the OTHER query params — each rendered `key=value` (repeated
values joined by ','), with the RENDERED strings (not the keys) sorted, concatenated
with NO separator — keyed by the app's API secret. We recompute and constant-time
compare. (This is distinct from the webhook HMAC, which signs the raw body and
arrives base64 in a header.)
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from astor.config import settings


def valid_app_proxy_signature(query_items: list[tuple[str, str]], secret: str) -> bool:
    params: dict[str, list[str]] = {}
    provided = ""
    for key, value in query_items:
        if key == "signature":
            provided = value
            continue
        params.setdefault(key, []).append(value)
    rendered = [f"{k}={','.join(vals)}" for k, vals in params.items()]
    message = "".join(sorted(rendered))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(provided) and hmac.compare_digest(digest.encode(), provided.encode())


def verify_app_proxy(request: Request) -> dict:
    """FastAPI dependency: reject any request not signed by Shopify's App Proxy."""
    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Shopify App Proxy secret not configured.")
    if not valid_app_proxy_signature(list(request.query_params.multi_items()), secret):
        raise HTTPException(status_code=401, detail="invalid App Proxy signature")
    return {"shop": request.query_params.get("shop")}
