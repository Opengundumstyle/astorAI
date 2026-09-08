"""Zero lexical hits must not become "Astor doesn't carry it".

The 2026-09-02 incident (assistant denied EMEM that was in stock) was fixed in
`catalog/search.py`, but the failure CLASS survives: a long query, a purely
semantic one, or a digit token that appears nowhere still returns zero rows, and
the assistant reads zero rows as absence. Measured against the live catalog:

    'serum free EMEM medium for HEK293 cells'  -> 0 lexical hits
    'something to detach adherent cells'       -> 0 lexical hits
    'DMEM 1x'                                  -> 0 lexical hits

All three are answerable from the embeddings already stored for every product.
"""
from astor.api import repo
from astor.chat import agent, tools


def _sess():
    return object()  # session unused; repo is monkeypatched


def _product(pid, name):
    return {"id": pid, "name": name, "brand": "Astor", "category": "cell_culture",
            "astor_sku": "ASR-1", "mpn": None, "region": None,
            "offer_count": 0, "best_landed": None}


def test_falls_back_to_semantic_when_lexical_finds_nothing(monkeypatch):
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: ([], 0))
    monkeypatch.setattr(
        repo, "search_products_semantic",
        lambda s, q, limit: [_product("p9", "Accutase Cell Detachment Solution - 100 ML")])

    result, items = tools.dispatch(
        _sess(), "search_products", {"query": "something to detach adherent cells"})

    assert result["match"] == "semantic", "the model must be told these are approximate"
    assert [p["name"] for p in result["products"]] == [
        "Accutase Cell Detachment Solution - 100 ML"]
    assert items == [tools.ReferencedItem(
        "product", "p9", "Accutase Cell Detachment Solution - 100 ML")]


def test_no_fallback_when_lexical_succeeds(monkeypatch):
    """The fallback is a rescue, not a second opinion: a good lexical hit wins."""
    calls = []
    monkeypatch.setattr(repo, "list_products",
                        lambda *a, **k: ([_product("p1", "DMEM, High Glucose - 500ml")], 1))
    monkeypatch.setattr(repo, "search_products_semantic",
                        lambda *a, **k: calls.append(1) or [])

    result, _ = tools.dispatch(_sess(), "search_products", {"query": "DMEM"})

    assert calls == [], "semantic search must not run when lexical already matched"
    assert result.get("match") != "semantic"


def test_semantic_results_stay_behind_the_buyer_gate(monkeypatch):
    """Fallback rows take the same `roles.gate_product` path as lexical ones —
    a vendor brand must not reach the model just because retrieval changed."""
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: ([], 0))
    monkeypatch.setattr(repo, "search_products_semantic",
                        lambda s, q, limit: [_product("p9", "Some Medium")])

    result, _ = tools.dispatch(_sess(), "search_products", {"query": "medium"})

    assert "brand" not in result["products"][0]
    assert "mpn" not in result["products"][0]


def test_empty_semantic_fallback_is_still_an_empty_result(monkeypatch):
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: ([], 0))
    monkeypatch.setattr(repo, "search_products_semantic", lambda s, q, limit: [])

    result, items = tools.dispatch(_sess(), "search_products", {"query": "zxqw flarn"})

    assert result["products"] == []
    assert items == []


def test_semantic_failure_does_not_break_the_turn(monkeypatch):
    """A dead embedding provider must degrade to "nothing found", not a tool error:
    `get_embedder()` raises by design when a provider is configured without a key."""
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: ([], 0))

    def boom(*a, **k):
        raise RuntimeError("EMBEDDINGS_PROVIDER=voyage but VOYAGE_API_KEY is not set")
    monkeypatch.setattr(repo, "search_products_semantic", boom)

    result, items = tools.dispatch(_sess(), "search_products", {"query": "medium"})

    assert result["products"] == []
    assert "error" not in result
    assert items == []


def test_prompt_forbids_reading_zero_results_as_absence():
    """The guard `protocols_by_material` already has, extended to products. Without
    it the model infers absence from an empty tool result and denies real stock.

    `don't carry` is deliberately NOT asserted — the prompt already says it under
    PROMOTE->SOURCE->POINT, so matching it would pass without the new rule.
    """
    system = agent.SYSTEM.lower()
    assert "retrieval miss" in system, "must name the empty-result-is-not-absence rule"
    assert "closest" in system, "must tell the model how to frame semantic fallbacks"
