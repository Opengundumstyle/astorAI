from astor.catalog.normalization import normalize, canonical_text
from astor.catalog.schemas import ExtractedProduct, NormalizedProduct
from astor.catalog import scoring
from astor.pricing.landed_cost import landed_cost


def test_category_canonicalization():
    item = ExtractedProduct(supplier_sku="x", name="Taq Mix", category="PCR", cost=10)
    out = normalize(item)
    assert out.product.category == "molecular_biology"
    assert out.offer.cost == 10.0


def test_canonical_text_is_spec_aware():
    p = NormalizedProduct(category="molecular_biology", name="Taq", brand="Vazyme",
                          specs={"volume": "5 mL"})
    txt = canonical_text(p)
    assert "Vazyme" in txt and "volume=5 mL" in txt


def test_classify_thresholds():
    assert scoring.classify(0.95, 0.92, 0.80) == "exact"
    assert scoring.classify(0.85, 0.92, 0.80) == "substitute"
    assert scoring.classify(0.5, 0.92, 0.80) is None


def test_attribute_bonus_same_brand_mpn_is_exact_signal():
    a = scoring.ProductView(category="molecular_biology", name="Taq", brand="Vazyme", mpn="P112")
    b = scoring.ProductView(category="molecular_biology", name="Taq", brand="Vazyme", mpn="P112")
    assert scoring.attribute_bonus(a, b) >= 0.5


def test_landed_cost_breakdown():
    bd = landed_cost(supplier_cost=100, currency="CNY", category="molecular_biology", qty=2)
    assert bd["currency"] == "USD"
    assert bd["tariff"] > 0
    assert bd["line_total"] == round(bd["unit_price"] * 2, 4)
