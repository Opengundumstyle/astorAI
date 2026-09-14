"""Re-sync a channel feed (Shopify) onto rows we already hold.

WHY THIS EXISTS
    `ingestion.upsert_step` can only update a product it can identify by
    (brand, mpn). The Shopify catalog carries no MPN, so a re-run of the full
    ingest INSERTS every product a second time. The catalog therefore froze at
    its first load and drifted from the store: SKUs renamed, titles edited,
    variants added, none of it visible to the assistant.

THE KEY
    `supplier_offers.external_id` = the Shopify variant id. A SKU can be renamed
    (5 were) or shared by two variants (23 are); a variant id cannot. Rows loaded
    before the column existed have it NULL, so the first run backfills it: match
    by SKU, then by exact name+brand when that is unambiguous, and stamp the id.
    Every later run matches by id alone.

THE PLAN IS PURE
    `plan_sync` takes the feed and the existing rows and returns what to do. No
    session, so every rule is unit-tested; the writer (`apply_plan`) is a thin
    translation of the plan into ORM calls. Nothing is ever deleted: a variant
    that vanished from the store marks its product unsellable, because protocol
    links and equivalences still point at the row.

SKU COLLISIONS
    The offer table forbids two offers with one (supplier, SKU); Shopify does
    not. A feed variant whose SKU another variant already holds cannot be
    written without corrupting the other row, so it is reported as a collision
    and skipped. That is a data error on the Shopify side, and the report names
    the variants so it can be fixed there.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from astor.catalog.normalization import canonical_text
from astor.catalog.schemas import NormalizedItem, NormalizedProduct
from astor.db.models import Product, Supplier, SupplierOffer


@dataclass(frozen=True)
class ExistingOffer:
    """The slice of an offer row (and its product) the planner needs."""
    offer_id: str
    product_id: str
    external_id: str | None
    supplier_sku: str
    product_name: str
    product_brand: str | None
    product_category: str
    product_specs: dict


@dataclass(frozen=True)
class Update:
    offer_id: str
    product_id: str
    item: NormalizedItem
    reembed: bool        # embedded text changed -> clear the vector
    sku_conflict: bool   # new SKU is held by another row -> keep the old SKU


@dataclass(frozen=True)
class Collision:
    item: NormalizedItem
    reason: str


@dataclass
class SyncPlan:
    updates: list[Update] = field(default_factory=list)
    inserts: list[NormalizedItem] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    vanished: list[ExistingOffer] = field(default_factory=list)


def _embedded_text(row: ExistingOffer) -> str:
    return canonical_text(NormalizedProduct(
        category=row.product_category, name=row.product_name,
        brand=row.product_brand, specs=row.product_specs or {}))


def plan_sync(feed: list[NormalizedItem], existing: list[ExistingOffer]) -> SyncPlan:
    plan = SyncPlan()
    by_external = {r.external_id: r for r in existing if r.external_id}
    unclaimed_by_sku = {r.supplier_sku: r for r in existing if not r.external_id}
    unclaimed_by_name: dict[tuple[str, str | None], list[ExistingOffer]] = {}
    for r in existing:
        if not r.external_id:
            unclaimed_by_name.setdefault((r.product_name, r.product_brand), []).append(r)

    # Who holds which SKU, as the plan is built: existing rows, then renames and
    # inserts as they are accepted. Values identify the holder for the report.
    sku_holder: dict[str, str] = {r.supplier_sku: f"variant {r.external_id}" if r.external_id
                                  else f"row {r.supplier_sku}" for r in existing}
    claimed: set[str] = set()

    for item in feed:
        o, p = item.offer, item.product
        row = by_external.get(o.external_id) if o.external_id else None
        if row is None:
            row = unclaimed_by_sku.get(o.supplier_sku)
        if row is None:
            candidates = unclaimed_by_name.get((p.name, p.brand), [])
            row = candidates[0] if len(candidates) == 1 else None
        if row is not None and row.offer_id in claimed:
            row = None

        if row is None:
            holder = sku_holder.get(o.supplier_sku)
            if holder is not None:
                plan.collisions.append(Collision(item, f"sku held by {holder}"))
                continue
            plan.inserts.append(item)
            sku_holder[o.supplier_sku] = f"variant {o.external_id}"
            continue

        claimed.add(row.offer_id)
        sku_conflict = False
        if o.supplier_sku != row.supplier_sku:
            holder = sku_holder.get(o.supplier_sku)
            if holder is not None:
                sku_conflict = True
            else:
                del sku_holder[row.supplier_sku]
        if not sku_conflict:
            # The row now belongs to this variant; name it in any later collision.
            sku_holder[o.supplier_sku] = f"variant {o.external_id}"
        plan.updates.append(Update(
            offer_id=row.offer_id, product_id=row.product_id, item=item,
            reembed=canonical_text(p) != _embedded_text(row), sku_conflict=sku_conflict))

    plan.vanished = [r for r in existing if r.offer_id not in claimed]
    return plan


# --------------------------------------------------------------------------- #
# DB side: load rows for the planner, write the plan back.
# --------------------------------------------------------------------------- #
def load_existing(session: Session, supplier: Supplier) -> list[ExistingOffer]:
    rows = session.execute(
        select(SupplierOffer, Product)
        .join(Product, Product.id == SupplierOffer.product_id)
        .where(SupplierOffer.supplier_id == supplier.id)
    ).all()
    return [ExistingOffer(
        offer_id=str(o.id), product_id=str(p.id), external_id=o.external_id,
        supplier_sku=o.supplier_sku, product_name=p.name, product_brand=p.brand,
        product_category=p.category, product_specs=p.specs or {},
    ) for o, p in rows]


def apply_plan(session: Session, plan: SyncPlan, supplier: Supplier) -> dict[str, int]:
    """Write the plan. Returns counts. Caller owns the transaction."""
    for u in plan.updates:
        o, p = u.item.offer, u.item.product
        offer = session.get(SupplierOffer, u.offer_id)
        product = session.get(Product, u.product_id)
        product.name, product.category, product.brand = p.name, p.category, p.brand
        product.specs, product.sellable = p.specs, p.sellable
        if u.reembed:
            product.embedding = None
        offer.external_id = o.external_id
        if not u.sku_conflict:
            offer.supplier_sku = o.supplier_sku
        offer.pack_size, offer.cost, offer.currency = o.pack_size, o.cost, o.currency
        offer.stock, offer.lead_time_days = o.stock, o.lead_time_days
    for item in plan.inserts:
        o, p = item.offer, item.product
        product = Product(category=p.category, name=p.name, brand=p.brand, mpn=p.mpn,
                          specs=p.specs, sellable=p.sellable)
        session.add(product)
        session.flush()
        session.add(SupplierOffer(
            supplier_id=supplier.id, product_id=product.id, supplier_sku=o.supplier_sku,
            external_id=o.external_id, pack_size=o.pack_size, cost=o.cost,
            currency=o.currency, stock=o.stock, lead_time_days=o.lead_time_days))
    for r in plan.vanished:
        session.get(Product, r.product_id).sellable = False
    session.flush()
    return {"updated": len(plan.updates), "inserted": len(plan.inserts),
            "vanished": len(plan.vanished), "collisions": len(plan.collisions)}
