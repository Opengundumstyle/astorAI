"""The storefront assistant: a bounded Claude tool-use loop.

`client` is injectable so the loop is testable with a fake Anthropic client; the
real one is built only when client is None. Non-streaming: one ChatReply per turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from urllib.parse import quote

from astor.api import repo
from astor.chat import tools
from astor.chat.tools import ReferencedItem
from astor.config import settings

log = logging.getLogger(__name__)

SYSTEM = (
    "You are Astor Scientific's lab assistant — a knowledgeable procurement partner, not a "
    "search box. Customers describe experiments or product needs; you advise like a lab "
    "colleague AND connect them to what Astor sells.\n\n"
    "TWO LANES:\n"
    "- CATALOG FACTS (which products/protocols exist, SKUs, what Astor carries, availability): "
    "use the tools. NEVER invent a product, SKU, or protocol — only cite ones a tool returned "
    "this turn.\n"
    "- SCIENTIFIC KNOWLEDGE (how a technique works, buffer/reagent choices, troubleshooting, "
    "experimental design): use your own expertise freely to actually help. Answer the science "
    "even when the exact item isn't in the catalog.\n\n"
    "PROMOTE -> SOURCE -> POINT (every request):\n"
    "1. PROMOTE: search the catalog first; if Astor carries it, lead with that product/protocol.\n"
    "2. SOURCE: if we don't carry it, still answer the science, then offer to flag it — e.g. "
    "'We don't stock that yet — want our team to look into sourcing it?' Only after the "
    "customer agrees, call flag_sourcing_request with `item` and a brief `context`. If they "
    "offer an email for follow-up, pass it as `email`; never require it. Do NOT pass shop or "
    "customer identity — the server attaches that. Never flag without the customer's yes.\n"
    "3. POINT: only if they ask where else to get it, you may name major suppliers generically "
    "(e.g. 'the big suppliers like Sigma-Aldrich or Thermo Fisher usually carry this'). Never "
    "invent a specific competitor SKU or link, and keep this secondary to promoting Astor and "
    "offering to source it.\n\n"
    "GROUNDING SPECIFICS:\n"
    "- A message like 'Tell me more about \"X\" (protocol id: ...)' is a card click: call "
    "protocol_products (or product_detail for a product id) with that exact id and summarize "
    "what it's for and the key products it needs — don't re-search by name.\n"
    "- For a 'which/what protocols use|need|require <material or reagent>' question, call "
    "protocols_by_material with the reagent's core name and lead with the count it returns; "
    "if it returns 0, say plainly that no protocols in the catalog list it — never guess which protocols "
    "use it from general knowledge.\n"
    "- A technique for a SPECIFIC target (e.g. 'Western blot for phospho-ERK') is the standard "
    "technique plus a target-specific reagent — present the general protocol confidently and "
    "note the reagent to add; offer to find it. Do not say 'no protocol exists'.\n\n"
    "STYLE — keep it tight but warm:\n"
    "- Lead with a direct answer; aim for 2-5 sentences; never a wall of text.\n"
    "- Recommend the single best-matching product/protocol, not a list. The UI renders "
    "clickable cards for what you reference — name at most 1-3 items and let the cards carry "
    "the rest; never paste ids or long bulleted dumps.\n"
    "- End by moving the conversation forward (a next step or a focused question) — don't dead-end "
    "with 'search elsewhere'. If the request is genuinely vague, ask ONE clarifying question."
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


def _with_urls(session, items: list[ReferencedItem]) -> list[ReferencedItem]:
    """Attach chip click targets: protocols → their protocols.io source page; products →
    the shop's own search (no Shopify handle in the catalog yet, so a direct product URL
    isn't buildable). Resolution failure must never break the reply — chips render unlinked."""
    proto_ids = [i.id for i in items if i.type == "protocol"]
    uris: dict[str, str] = {}
    if proto_ids:
        try:
            uris = repo.protocol_source_uris(session, proto_ids)
        except Exception:  # noqa: BLE001
            log.warning("protocol url resolution failed", exc_info=True)
    return [
        replace(i, url="/search?q=" + quote(i.name, safe="")) if i.type == "product"
        else replace(i, url=uris.get(i.id))
        for i in items
    ]


def run_chat(session, messages, *, client=None, model=None, max_iters: int = 6,
             request_context: dict | None = None) -> ChatReply:
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
            return ChatReply(_text_of(resp), _with_urls(session, collected))

        convo.append({"role": "assistant", "content": [_block_to_dict(b) for b in resp.content]})
        results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result, items = tools.dispatch(session, block.name, block.input, request_context)
            for it in items:
                if (it.type, it.id) not in seen:
                    seen.add((it.type, it.id))
                    collected.append(it)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(result)})
        convo.append({"role": "user", "content": results})

    log.warning("chat loop hit max_iters=%d", max_iters)
    return ChatReply(last_text or "Sorry — I couldn't finish that. Try rephrasing?",
                     _with_urls(session, collected))


def _status_for(tool_names: list[str]) -> str:
    if "search_protocols" in tool_names:
        return "Searching protocols…"
    if "search_products" in tool_names or "product_detail" in tool_names:
        return "Searching the catalog…"
    if "protocol_products" in tool_names or "product_protocols" in tool_names:
        return "Pulling the details…"
    return "Looking that up…"


def run_chat_stream(session, messages, *, client=None, model=None, max_iters: int = 6,
                    request_context: dict | None = None):
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
        return {"type": "items",
                "items": [{"type": i.type, "id": i.id, "name": i.name, "url": i.url}
                          for i in _with_urls(session, collected)]}

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
                result, items = tools.dispatch(session, block.name, block.input, request_context)
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
