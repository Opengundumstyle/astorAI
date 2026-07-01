from astor.api.roles import (
    BUYER,
    OPS,
    gate_detail,
    gate_landed,
    gate_product,
    normalize_role,
)


def test_normalize_role_defaults_to_ops():
    assert normalize_role(None) == OPS
    assert normalize_role("") == OPS
    assert normalize_role("nonsense") == OPS
    assert normalize_role("buyer") == BUYER
    assert normalize_role("OPS") == OPS


def test_gate_product_ops_is_untouched():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
         "region": "CN", "offers": [1], "best_landed": 9.9}
    assert gate_product(d, OPS) == d


def test_gate_product_buyer_strips_origin_fields():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
         "region": "CN", "offers": [1], "best_landed": 9.9}
    out = gate_product(d, BUYER)
    assert out == {"astor_sku": "ASR-1", "name": "x", "best_landed": 9.9}
    for forbidden in ("brand", "mpn", "region", "offers"):
        assert forbidden not in out


def test_gate_detail_buyer_strips_offers_and_equivalent_origin():
    d = {
        "astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "mpn": "P112",
        "category": "molecular_biology", "specs": {},
        "offers": [{"supplier": "S", "region": "CN", "cost": 1}],
        "equivalents": [
            {"astor_sku": "ASR-2", "name": "y", "brand": "NEB", "region": "US",
             "supplier": "NEB Inc", "confidence": 0.9, "kind": "substitute"}
        ],
    }
    out = gate_detail(d, BUYER)
    assert "offers" not in out
    assert "brand" not in out and "mpn" not in out
    eq = out["equivalents"][0]
    assert eq == {"astor_sku": "ASR-2", "name": "y", "confidence": 0.9, "kind": "substitute"}
    for forbidden in ("brand", "region", "supplier"):
        assert forbidden not in eq


def test_gate_landed_buyer_keeps_only_price():
    d = {"currency": "USD", "qty": 2, "ex_works": 16.8, "tariff": 4.2,
         "duty_rate": 0.25, "freight": 1.5, "margin": 4.5,
         "unit_price": 27.0, "line_total": 54.0}
    out = gate_landed(d, BUYER)
    assert out == {"currency": "USD", "qty": 2, "unit_price": 27.0, "line_total": 54.0}
    for forbidden in ("ex_works", "tariff", "duty_rate", "freight", "margin"):
        assert forbidden not in out


def test_gate_product_ops_returns_copy_not_alias():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme"}
    out = gate_product(d, OPS)
    assert out == d
    assert out is not d


def test_gate_detail_ops_returns_copy_not_alias():
    d = {"astor_sku": "ASR-1", "name": "x", "brand": "Vazyme", "equivalents": []}
    out = gate_detail(d, OPS)
    assert out == d
    assert out is not d


def test_gate_landed_ops_returns_copy_not_alias():
    d = {"currency": "USD", "qty": 2, "ex_works": 16.8, "unit_price": 27.0}
    out = gate_landed(d, OPS)
    assert out == d
    assert out is not d


def test_gate_detail_buyer_without_equivalents_omits_key():
    d = {"astor_sku": "ASR-1", "name": "x", "category": "molecular_biology"}
    out = gate_detail(d, BUYER)
    assert "equivalents" not in out


def test_normalize_role_uppercase_buyer():
    assert normalize_role("BUYER") == BUYER


# --- Fail-closed regression tests: unknown future keys must NOT reach buyers ---

def test_gate_product_buyer_drops_unknown_future_key():
    d = {"id": "1", "astor_sku": "ASR-1", "name": "x", "category": "mb",
         "offer_count": 3, "best_landed": 9.9, "secret_origin": "CN"}
    out = gate_product(d, BUYER)
    assert "secret_origin" not in out


def test_gate_product_ops_passes_unknown_future_key():
    d = {"astor_sku": "ASR-1", "name": "x", "secret_origin": "CN"}
    out = gate_product(d, OPS)
    assert "secret_origin" in out


def test_gate_detail_buyer_drops_unknown_future_key():
    d = {
        "id": "1", "astor_sku": "ASR-1", "name": "x", "category": "mb",
        "specs": {}, "supplier_internal": "X",
        "equivalents": [
            {"id": "2", "astor_sku": "ASR-2", "name": "y",
             "confidence": 0.9, "kind": "substitute", "origin": "US"}
        ],
    }
    out = gate_detail(d, BUYER)
    assert "supplier_internal" not in out
    assert "origin" not in out["equivalents"][0]


def test_gate_landed_buyer_drops_unknown_future_key():
    d = {"currency": "USD", "qty": 2, "unit_price": 27.0,
         "line_total": 54.0, "fx_rate": 0.14}
    out = gate_landed(d, BUYER)
    assert "fx_rate" not in out
