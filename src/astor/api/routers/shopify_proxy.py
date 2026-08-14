"""Shopify App Proxy endpoints — reachable only via a signed Shopify proxy request.

The Proxy URL configured in the Shopify app points at `https://<host>/proxy`, and
Shopify appends the storefront subpath, so `store/apps/astor/ping` -> `/proxy/ping`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from astor.api.deps import get_session
from astor.api.shopify_proxy import verify_app_proxy
from astor.chat import agent

router = APIRouter(prefix="/proxy", tags=["shopify-proxy"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.get("/ping")
def ping(ctx: dict = Depends(verify_app_proxy)) -> dict:
    """Proof endpoint: returns the verified shop domain from a signed proxy request."""
    return {"ok": True, "shop": ctx["shop"]}


@router.post("/chat")
def chat(
    body: ChatRequest,
    ctx: dict = Depends(verify_app_proxy),
    session: Session = Depends(get_session),
) -> dict:
    """Storefront chat turn, verified as a signed App Proxy request. Reuses the same
    agent + response shape as /api/chat; non-streaming."""
    try:
        reply = agent.run_chat(session, [m.model_dump() for m in body.messages])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "reply": reply.reply,
        "items": [{"type": i.type, "id": i.id, "name": i.name} for i in reply.items],
    }
