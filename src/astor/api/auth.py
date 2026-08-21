"""Admin-token gate for the internal API surface.

Everything under `/api/*` is operator-facing: the catalog endpoints default to
`role="ops"` (see `roles.py`), which returns supplier identity, origin, MPN and cost
internals, and `/api/ingest` triggers a pipeline write. On a public host that surface
must not be anonymous. `/proxy/*` is excluded — Shopify's App Proxy signature already
authenticates it — and so are the health probes.

Unset token (local dev) = open, so the developer loop is unchanged.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from astor.config import settings


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.admin_token
    if not expected:
        return
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing admin token")
