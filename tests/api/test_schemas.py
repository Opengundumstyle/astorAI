import uuid
from types import SimpleNamespace

from astor.api import schemas

PID = uuid.UUID("7f3a21b4-0000-0000-0000-000000000000")


def _product():
    return SimpleNamespace(
        id=PID, name="2x Taq Master Mix", category="molecular_biology",
        brand="Vazyme", mpn="P112", specs={"volume": "5 mL"},
    )


def _offer():
    return SimpleNamespace(
        supplier_sku="VZ-EX-001", pack_size="5 mL", cost=120, currency="CNY",
        stock=200, lead_time_days=7,
        supplier=SimpleNamespace(name="Sample CN", region="CN"),
    )


def test_product_summary_has_astor_sku_and_no_db_needed():
    d = schemas.product_summary(_product(), offer_count=3, best_landed=24.8)
    assert d["astor_sku"] == "ASR-7F3A21"
    assert d["name"] == "2x Taq Master Mix"
    assert d["brand"] == "Vazyme"
    assert d["mpn"] == "P112"
    assert d["region"] is None  # summary has no single region; offers carry it
    assert d["offer_count"] == 3
    assert d["best_landed"] == 24.8


def test_offer_out_reads_supplier_identity():
    d = schemas.offer_out(_offer())
    assert d["supplier"] == "Sample CN"
    assert d["region"] == "CN"
    assert d["cost"] == 120.0
    assert d["currency"] == "CNY"
    assert d["lead_time_days"] == 7


def test_product_detail_bundles_offers_and_equivalents():
    eq_product = SimpleNamespace(
        id=uuid.UUID("abcdef12-0000-0000-0000-000000000000"),
        name="NEB Taq", category="molecular_biology", brand="NEB", mpn="M0480",
        specs={},
    )
    d = schemas.product_detail(_product(), [_offer()], [(eq_product, 0.86, "substitute")])
    assert d["astor_sku"] == "ASR-7F3A21"
    assert d["specs"] == {"volume": "5 mL"}
    assert d["offers"][0]["supplier"] == "Sample CN"
    eq = d["equivalents"][0]
    assert eq["astor_sku"] == "ASR-ABCDEF"
    assert eq["brand"] == "NEB"
    assert eq["confidence"] == 0.86
    assert eq["kind"] == "substitute"


def test_stats_out_shape():
    d = schemas.stats_out(products=10, offers=20, exact=3, substitute=5,
                          suppliers=2, avg_savings=0.38)
    assert d == {"products": 10, "offers": 20,
                 "equivalences": {"exact": 3, "substitute": 5, "total": 8},
                 "suppliers": 2, "avg_savings": 0.38}
