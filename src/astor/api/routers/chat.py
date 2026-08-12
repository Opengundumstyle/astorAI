"""Storefront assistant endpoint — a tool-using chat turn."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from astor.api.deps import get_session
from astor.chat import agent

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.post("/chat")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> dict:
    try:
        reply = agent.run_chat(session, [m.model_dump() for m in body.messages])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "reply": reply.reply,
        "items": [{"type": i.type, "id": i.id, "name": i.name} for i in reply.items],
    }
