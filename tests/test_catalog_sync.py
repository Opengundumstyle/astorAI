"""Pure planner behind scripts/sync_shopify.py: feed + existing rows -> plan.

No database. The apply step is covered DB-gated in tests/api/test_sync_apply_repo.py.
"""
from astor.catalog.normalization import normalize
from astor.catalog.schemas import ExtractedProduct
from astor.catalog.sync import ExistingOffer, plan_sync


def _item(sku, name, *, external_id, brand="NEST", category="cell_culture", specs=None, price=1.0):
    return normalize(ExtractedProduct(
        supplier_sku=sku, external_id=external_id, name=name, brand=brand,
        category=category, specs=specs or {}, cost=price, currency="USD"))


def _row(sku, name, *, external_id=None, offer_id="o1", product_id="p1", brand="NEST",
         category="cell_culture", specs=None):
    return ExistingOffer(offer_id=offer_id, product_id=product_id, external_id=external_id,
                         supplier_sku=sku, product_name=name, product_brand=brand,
                         product_category=category, product_specs=specs or {})


def test_normalize_passes_external_id_through_to_the_offer():
    assert _item("A-1", "Flask", external_id="11").offer.external_id == "11"


def test_matches_by_external_id_and_applies_a_sku_rename():
    plan = plan_sync([_item("NT707003", "Flask 25", external_id="11")],
                     [_row("AS-707003", "Flask 25", external_id="11")])
    (u,) = plan.updates
    assert u.offer_id == "o1" and u.item.offer.supplier_sku == "NT707003"
    assert not u.sku_conflict and not plan.inserts and not plan.vanished


def test_first_run_backfills_by_sku_when_rows_have_no_external_id():
    plan = plan_sync([_item("AS-707003", "Flask 25", external_id="11")],
                     [_row("AS-707003", "Flask 25")])
    assert [u.offer_id for u in plan.updates] == ["o1"]
    assert plan.updates[0].item.offer.external_id == "11"


def test_renamed_sku_on_first_run_matches_by_exact_name_and_brand():
    plan = plan_sync([_item("NT707003", "Flask 25", external_id="11")],
                     [_row("AS-707003", "Flask 25")])
    assert [u.offer_id for u in plan.updates] == ["o1"]
    assert not plan.inserts


def test_ambiguous_name_match_inserts_rather_than_guessing():
    rows = [_row("X-1", "Flask 25", offer_id="o1", product_id="p1"),
            _row("X-2", "Flask 25", offer_id="o2", product_id="p2")]
    plan = plan_sync([_item("NT707003", "Flask 25", external_id="11")], rows)
    assert not plan.updates and len(plan.inserts) == 1
    assert {v.offer_id for v in plan.vanished} == {"o1", "o2"}


def test_new_variant_whose_sku_another_variant_holds_is_a_collision_not_an_insert():
    # Shopify allows two variants to share a SKU; the offer table does not.
    rows = [_row("BX23-0010A", "Filter tips LIMITED", external_id="11")]
    feed = [_item("BX23-0010A", "Filter tips LIMITED", external_id="11"),
            _item("BX23-0010A", "Filter Pipette Tips-10uL", external_id="12")]
    plan = plan_sync(feed, rows)
    assert [u.offer_id for u in plan.updates] == ["o1"]
    assert not plan.inserts
    assert [(c.item.offer.external_id, c.reason) for c in plan.collisions] == [("12", "sku held by variant 11")]


def test_sku_renamed_onto_a_sku_another_row_holds_keeps_the_old_sku_but_updates_the_rest():
    rows = [_row("A-1", "Flask 25", external_id="11", offer_id="o1", product_id="p1"),
            _row("A-2", "Flask 75", external_id="12", offer_id="o2", product_id="p2")]
    plan = plan_sync([_item("A-2", "Flask 25 (new title)", external_id="11"),
                      _item("A-2", "Flask 75", external_id="12")], rows)
    by_id = {u.offer_id: u for u in plan.updates}
    assert by_id["o1"].sku_conflict is True
    assert by_id["o1"].reembed is True   # the title changed, so the vector is stale
    assert by_id["o2"].sku_conflict is False


def test_vanished_rows_are_reported_not_deleted():
    plan = plan_sync([], [_row("A-1", "Flask 25", external_id="11")])
    assert [v.offer_id for v in plan.vanished] == ["o1"]


def test_reembed_only_when_the_embedded_text_changed():
    rows = [_row("A-1", "Flask 25", external_id="11", specs={"Size": "25 cm2"})]
    same = plan_sync([_item("A-1", "Flask 25", external_id="11", specs={"Size": "25 cm2"}, price=9.0)], rows)
    changed = plan_sync([_item("A-1", "Flask 25", external_id="11", specs={"Size": "75 cm2"})], rows)
    assert same.updates[0].reembed is False       # price is not embedded
    assert changed.updates[0].reembed is True
