"""Liveness probe for the platform (Render polls this to gate every rollout).

Deliberately DB-free: a transient Postgres blip must not roll back a healthy deploy.
Unprefixed and never auth-gated — see `create_app`.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
