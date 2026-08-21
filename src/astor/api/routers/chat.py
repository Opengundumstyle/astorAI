"""Storefront assistant endpoint — a tool-using chat turn."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from astor.api.deps import get_session
from astor.chat import agent
from astor.config import settings

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
        "items": [{"type": i.type, "id": i.id, "name": i.name, "url": i.url} for i in reply.items],
    }


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, session: Session = Depends(get_session)):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set — the assistant needs it.")

    def sse():
        for event in agent.run_chat_stream(session, [m.model_dump() for m in body.messages]):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        sse(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
