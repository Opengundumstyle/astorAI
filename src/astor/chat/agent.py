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
    "GROUNDING:\n"
    "- Use the tools to find real protocols and products. NEVER invent a product, SKU, "
    "or protocol — only mention ones a tool returned this turn.\n"
    "- Decide freely whether to go protocol-first or product-first.\n"
    "- When a request is a technique for a SPECIFIC target (e.g. 'Western blot for "
    "phospho-ERK', 'ELISA for IL-6'), treat it as the standard technique plus a "
    "target-specific reagent — the general protocol IS the protocol. Present the general "
    "technique's protocol confidently and note the target-specific reagent to add (e.g. "
    "the anti-phospho-ERK antibody); do NOT frame it as 'no protocol exists'. Offer to "
    "find that reagent.\n\n"
    "KEEP REPLIES SHORT — this is the most important rule:\n"
    "- Lead with a direct 1-2 sentence answer. Aim for 2-4 sentences total; never a wall "
    "of text.\n"
    "- Recommend the SINGLE best-matching protocol or product, not a list of options. "
    "Only compare alternatives if the customer asks.\n"
    "- Do NOT enumerate every product in prose. The UI already renders clickable cards "
    "for everything you referenced — name at most 1-3 key items, and let the cards carry "
    "the rest. Never paste ids or long bulleted breakdowns.\n"
    "- End by offering the next step instead of dumping detail, e.g. 'Want the full "
    "material list?' or 'Should I find a substitute?'.\n"
    "- If the request is vague, ask ONE focused clarifying question and stop — do not "
    "search or list anything yet."
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


def _status_for(tool_names: list[str]) -> str:
    if "search_protocols" in tool_names:
        return "Searching protocols…"
    if "search_products" in tool_names or "product_detail" in tool_names:
        return "Searching the catalog…"
    if "protocol_products" in tool_names or "product_protocols" in tool_names:
        return "Pulling the details…"
    return "Looking that up…"


def run_chat_stream(session, messages, *, client=None, model=None, max_iters: int = 6):
    """Streaming variant of run_chat: a generator of SSE event dicts. Streams the
    final answer's text deltas; tool rounds emit a status event. Never raises to the
    caller — an error becomes an {"type":"error"} event so the SSE stream closes cleanly."""
    if not settings.anthropic_api_key:
        yield {"type": "error", "detail": "ANTHROPIC_API_KEY is not set — the assistant needs it."}
        return
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)
    model = model or settings.chat_model

    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    collected: list[ReferencedItem] = []
    seen: set[tuple[str, str]] = set()

    def _items_event():
        return {"type": "items", "items": [{"type": i.type, "id": i.id, "name": i.name} for i in collected]}

    try:
        for _ in range(max_iters):
            with client.messages.stream(
                model=model, max_tokens=1024, system=SYSTEM,
                tools=tools.TOOL_SCHEMAS, thinking={"type": "disabled"}, messages=convo,
            ) as stream:
                for event in stream:
                    if (getattr(event, "type", None) == "content_block_delta"
                            and getattr(getattr(event, "delta", None), "type", None) == "text_delta"):
                        yield {"type": "delta", "text": event.delta.text}
                final = stream.get_final_message()

            if final.stop_reason != "tool_use":
                yield _items_event()
                yield {"type": "done"}
                return

            tool_names = [b.name for b in final.content if getattr(b, "type", None) == "tool_use"]
            yield {"type": "status", "text": _status_for(tool_names)}
            convo.append({"role": "assistant", "content": [_block_to_dict(b) for b in final.content]})
            results = []
            for block in final.content:
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

        yield _items_event()
        yield {"type": "done"}
    except Exception as exc:  # noqa: BLE001 — surface as a stream event, never break the connection
        yield {"type": "error", "detail": f"{type(exc).__name__}: {exc}"}
