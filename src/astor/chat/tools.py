"""Tools the storefront assistant can call — thin, compact wrappers over repo reads.

Each returns (result_dict, referenced_items). `referenced_items` are the products
and protocols the tool surfaced, so the chat UI can render real clickable cards
instead of trusting the model's prose. A tool that raises returns an {"error": ...}
result so the model can recover rather than 500 the turn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from astor import curation
from astor.api import repo, roles
from astor.curation import troubleshoot as _troubleshoot_mod

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferencedItem:
    type: str   # "product" | "protocol"
    id: str
    name: str
    url: str | None = None  # click target; attached by the agent after collection


def _public_specs(specs: dict | None) -> dict:
    """Drop internal-only spec keys (underscore-prefixed, e.g. `_cost_basis`). The
    role gate keeps `specs` wholesale, so nested internals need stripping here."""
    return {k: v for k, v in (specs or {}).items() if not k.startswith("_")}


def _search_products(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    limit = int(args.get("limit") or 8)
    rows, _ = repo.list_products(session, args["query"], None, 1, limit)
    semantic = False
    if not rows:
        # Zero lexical hits is a retrieval outcome, not proof Astor lacks the item:
        # 'serum free EMEM medium for HEK293 cells' scores 0 lexically while the
        # product sits in the catalog. Fall back to the embeddings every product
        # already has, and tell the model the results are approximate.
        try:
            rows = repo.search_products_semantic(session, args["query"], limit)
            semantic = bool(rows)
        except Exception:  # noqa: BLE001 — a dead embedder degrades to "nothing found"
            log.warning("semantic product fallback failed", exc_info=True)
            rows = []
    # Buyer gate, not a hand-picked field list: `roles` is the single authority on what
    # a buyer may see, so a field added to the product DTO later is withheld from the
    # assistant by default instead of silently reaching a shopper.
    products = [roles.gate_product(r, roles.BUYER) for r in rows]
    items = [ReferencedItem("product", r["id"], r["name"]) for r in rows]
    result = {"products": products}
    if semantic:
        result["match"] = "semantic"
    return result, items


def _protocol_label(row: dict) -> str:
    """Card label. Distinct protocols often share a title (the corpus has 11 named
    exactly "Western Blot"), so append the first author to keep cards tellable apart."""
    author = row.get("first_author")
    return f"{row['title']} ({author})" if author else row["title"]


def _search_protocols(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    limit = int(args.get("limit") or 8)
    rows, _ = repo.list_protocols(session, args["query"], 1, limit)
    protocols = [{"id": r["id"], "title": r["title"], "product_count": r["product_count"],
                  "first_author": r.get("first_author")}
                 for r in rows]
    items = [ReferencedItem("protocol", r["id"], _protocol_label(r)) for r in rows]
    return {"protocols": protocols}, items


def _protocol_products(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    r = repo.protocol_materials(session, args["protocol_id"], reviewed_only=False, limit=50)
    if r is None:
        return {"error": "protocol not found"}, []
    products = [{"product_id": m["product_id"], "product_name": m["product_name"],
                 "material_name": m["material_name"], "confidence": m["confidence"]}
                for m in r["materials"]]
    items = [ReferencedItem("protocol", args["protocol_id"], r["protocol_title"])]
    items += [ReferencedItem("product", m["product_id"], m["product_name"]) for m in r["materials"]]
    return {"protocol_title": r["protocol_title"], "products": products}, items


def _product_protocols(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    r = repo.product_protocols(session, args["product_id"], reviewed_only=False, limit=50)
    if r is None:
        return {"error": "product not found"}, []
    protocols = [{"protocol_id": p["protocol_id"], "title": p["title"]} for p in r["protocols"]]
    items = [ReferencedItem("product", args["product_id"], r["product_name"])]
    items += [ReferencedItem("protocol", p["protocol_id"], p["title"]) for p in r["protocols"]]
    return {"product_name": r["product_name"], "protocols": protocols}, items


def _product_detail(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    d = repo.get_product_detail(session, args["product_id"])
    if d is None:
        return {"error": "product not found"}, []
    compact = roles.gate_detail(d, roles.BUYER)
    compact["specs"] = _public_specs(compact.get("specs"))
    return compact, [ReferencedItem("product", d["id"], d["name"])]


def _protocols_by_material(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    r = repo.protocols_by_material(session, args["material"], limit=min(int(args.get("limit") or 10), 50))
    items = [ReferencedItem("protocol", p["id"], p["title"]) for p in r["protocols"]]
    return r, items


def _flag_sourcing_request(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    """WRITE: capture a customer-confirmed request for something Astor doesn't carry.
    Identity (shop/customer_id) is taken from the server-supplied request_context, NEVER
    from the model's args."""
    rc = request_context or {}
    r = repo.create_sourcing_request(
        session,
        requested_item=args["item"],
        context=args.get("context", ""),
        shop=rc.get("shop"),
        customer_id=rc.get("customer_id"),
        email=args.get("email"),
    )
    return {"logged": True, "item": r["requested_item"], "status": r["status"]}, []


_matcher: _troubleshoot_mod.Matcher | None = None


def matcher() -> _troubleshoot_mod.Matcher:
    """Row embeddings are computed once per process; a dead embedder leaves the
    matcher keyword-only rather than failing the tool."""
    global _matcher
    if _matcher is None:
        from astor.catalog.embeddings import get_embedder
        try:
            embedder = get_embedder()
        except Exception:  # noqa: BLE001
            log.warning("embedder unavailable; troubleshooting is keyword-only", exc_info=True)
            embedder = None
        _matcher = _troubleshoot_mod.Matcher(curation.tables(), embedder=embedder)
    return _matcher


def reset_matcher() -> None:
    global _matcher
    _matcher = None


def _troubleshoot(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    """READ: curated symptom -> cause -> fix rows, each fix resolved to catalog products.
    Deterministic; the model never sees a row that is not in docs/curation/troubleshooting.csv."""
    symptom = (args.get("symptom") or "").strip()
    if not symptom:
        raise ValueError("symptom is required")
    limit = min(int(args.get("limit") or 5), 10)
    m = matcher()
    hits, kind = m.search(symptom, category=args.get("category") or None, limit=limit)
    entries, items = [], []
    for h in hits:
        products, owns = _troubleshoot_mod.resolve_products(session, h.entry, m.tables)
        gated = [roles.gate_product(p, roles.BUYER) for p in products]
        items.extend(ReferencedItem("product", p["id"], p["name"]) for p in products)
        entries.append({
            "entry_id": h.entry.entry_id,
            "category_id": h.entry.category_id,
            "symptom": h.entry.symptom,
            "likely_cause": h.entry.likely_cause,
            "check_or_fix": h.entry.check_or_fix,
            "confidence": h.entry.confidence,
            "fix_role": h.entry.fix_role,
            "buy_needed": h.entry.buy_needed,
            "lab_usually_owns_it": owns,
            "products": gated,
        })
    return {"entries": entries, "match": kind}, items


_HANDLERS = {
    "search_products": _search_products,
    "search_protocols": _search_protocols,
    "protocol_products": _protocol_products,
    "product_protocols": _product_protocols,
    "product_detail": _product_detail,
    "protocols_by_material": _protocols_by_material,
    "flag_sourcing_request": _flag_sourcing_request,
    "troubleshoot": _troubleshoot,
}


def dispatch(session, name: str, args: dict, request_context: dict | None = None
             ) -> tuple[dict, list[ReferencedItem]]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}, []
    try:
        return handler(session, args, request_context)
    except Exception as exc:  # noqa: BLE001 — surface as recoverable tool error
        return {"error": f"{type(exc).__name__}: {exc}"}, []


TOOL_SCHEMAS = [
    {"name": "search_products",
     "description": "Search the Astor catalog by free text (name/brand). Returns matching products.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "search_protocols",
     "description": "Search harvested lab protocols by title. Returns protocols and how many "
                    "catalog products each maps to. Different labs often publish protocols with "
                    "identical titles; use first_author to tell them apart when presenting.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "protocol_products",
     "description": "Given a protocol id, list the Astor products it needs (its shopping list).",
     "input_schema": {"type": "object",
                      "properties": {"protocol_id": {"type": "string"}},
                      "required": ["protocol_id"]}},
    {"name": "product_protocols",
     "description": "Given a product id, list the protocols that use it.",
     "input_schema": {"type": "object",
                      "properties": {"product_id": {"type": "string"}},
                      "required": ["product_id"]}},
    {"name": "product_detail",
     "description": "Given a product id, get its name, brand, category, and specs.",
     "input_schema": {"type": "object",
                      "properties": {"product_id": {"type": "string"}},
                      "required": ["product_id"]}},
    {"name": "protocols_by_material",
     "description": "Find protocols that USE a given lab material/reagent by name (reverse "
                    "lookup over each protocol's material list). Use this for 'which protocols "
                    "use X' / 'what protocols need X' questions. Returns a total count and the "
                    "top matches; each match includes the material text that matched.",
     "input_schema": {"type": "object",
                      "properties": {"material": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["material"]}},
    {"name": "flag_sourcing_request",
     "description": "Log a customer-confirmed request for a product/reagent Astor does NOT "
                    "currently carry, so the team can look into sourcing it. Call this ONLY "
                    "after the customer agrees to be flagged. Provide `item` (what they want) "
                    "and `context` (their need in brief); include `email` only if the customer "
                    "offers one for follow-up. Do NOT pass shop or customer identity — the "
                    "server attaches that.",
     "input_schema": {"type": "object",
                      "properties": {"item": {"type": "string"},
                                     "context": {"type": "string"},
                                     "email": {"type": "string"}},
                      "required": ["item"]}},
    {"name": "troubleshoot",
     "description": "Look up Astor's curated troubleshooting guidance for a failed or unexpected "
                    "experimental result: 'no bands', 'no Ct', 'high background', 'cells died', "
                    "'low transfection efficiency'. Pass the customer's own words as `symptom`. "
                    "Returns symptom/cause/fix entries and, for each fix, the catalog products "
                    "that fill the needed role. Entries marked confidence='drafted' are commonly "
                    "reported causes, not yet confirmed by Astor's specialist; say so.",
     "input_schema": {"type": "object",
                      "properties": {
                          "symptom": {"type": "string",
                                      "description": "The failure in the customer's words, any language."},
                          "category": {"type": "string",
                                       "enum": ["western_blot", "rt_qpcr", "elisa", "cell_culture_transfection"],
                                       "description": "Omit if unsure; it is inferred from the symptom."},
                          "limit": {"type": "integer"}},
                      "required": ["symptom"]}},
]
