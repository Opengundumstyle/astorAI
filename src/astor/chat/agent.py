"""The storefront assistant: a bounded Claude tool-use loop.

`client` is injectable so the loop is testable with a fake Anthropic client; the
real one is built only when client is None. Non-streaming: one ChatReply per turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from astor.chat import tools
from astor.chat.tools import ReferencedItem
from astor.config import settings

log = logging.getLogger(__name__)

SYSTEM = (
    "You are Astor Scientific's procurement assistant. Customers describe an "
    "experiment or a product need; you help them find the right lab protocol and the "
    "Astor products required for it.\n\n"
    "RULES:\n"
    "- Use the tools to find real protocols and products. NEVER invent a product, SKU, "
    "or protocol — only mention ones a tool returned this turn.\n"
    "- If the request is vague, ask ONE focused clarifying question before searching.\n"
    "- Decide freely whether to go protocol-first (find a protocol, then its products) "
    "or product-first (search products, optionally show protocols that use them).\n"
    "- Be concise and practical. Summarize what you found; the UI shows clickable cards "
    "for the items you referenced, so you don't need to paste ids or long lists."
)


@dataclass
class ChatReply:
    reply: str
    items: list[ReferencedItem]


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _block_to_dict(b) -> dict:
    """Serialize a response content block (SDK object or test SimpleNamespace) to a
    plain dict so it round-trips as conversation history on the next create() call."""
    if isinstance(b, dict):
        return b
    btype = getattr(b, "type", None)
    if btype == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if btype == "text":
        return {"type": "text", "text": b.text}
    return {"type": btype}


def run_chat(session, messages, *, client=None, model=None, max_iters: int = 6) -> ChatReply:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the assistant needs it.")
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)
    model = model or settings.chat_model

    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    collected: list[ReferencedItem] = []
    seen: set[tuple[str, str]] = set()
    last_text = ""

    for _ in range(max_iters):
        resp = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM,
            tools=tools.TOOL_SCHEMAS, messages=convo,
            thinking={"type": "disabled"},
        )
        last_text = _text_of(resp) or last_text
        if resp.stop_reason != "tool_use":
            return ChatReply(_text_of(resp), collected)

        convo.append({"role": "assistant", "content": [_block_to_dict(b) for b in resp.content]})
        results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result, items = tools.dispatch(session, block.name, block.input)
            for it in items:
                if (it.type, it.id) not in seen:
                    seen.add((it.type, it.id))
                    collected.append(it)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(result)})
        convo.append({"role": "user", "content": results})

    log.warning("chat loop hit max_iters=%d", max_iters)
    return ChatReply(last_text or "Sorry — I couldn't finish that. Try rephrasing?", collected)
