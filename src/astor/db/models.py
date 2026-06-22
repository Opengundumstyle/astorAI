"""Core data model for the distributor / merchant-of-record marketplace.

Design invariants (the no-refactor list):
  1. `Product` (canonical facts) is never conflated with `SupplierOffer`
     (who sells it, at what cost). One product -> many offers.
  2. `OrderLine` references a Product (Astor sets the customer price);
     `UpstreamPoLine` references a SupplierOffer (what Astor actually buys).
  3. `Equivalence` is first-class data (confidence + type), not logic in code.
  4. landed_cost is stored as a JSONB breakdown, never a scalar.
  5. tenant_id lives on tenant-scoped rows from day one (cheap; painful later).
  6. Natural unique keys give idempotent ingestion (safe re-runs).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from astor.config import settings
from astor.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------- #
# Catalog spine
# --------------------------------------------------------------------------- #
class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(32), nullable=False)  # CN | US | OTHER
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)

    offers: Mapped[list["SupplierOffer"]] = relationship(back_populates="supplier")

    __table_args__ = (
        CheckConstraint("tier in ('public','authorized','deep')", name="ck_supplier_tier"),
        UniqueConstraint("name", name="uq_supplier_name"),
    )


class Product(Base, TimestampMixin):
    """Canonical, brand-tagged facts about a thing. Not a listing."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(128))
    # Manufacturer catalog number (a.k.a. MPN) -- the strongest dedupe signal.
    mpn: Mapped[str | None] = mapped_column(String(128))
    specs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Embedding of the canonical product text; populated by the matcher.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))

    offers: Mapped[list["SupplierOffer"]] = relationship(back_populates="product")

    __table_args__ = (
        # Idempotency: a (brand, mpn) pair identifies one canonical product.
        UniqueConstraint("brand", "mpn", name="uq_product_brand_mpn"),
        Index(
            "ix_product_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class SupplierOffer(Base, TimestampMixin):
    """A supplier's sellable offer for a product: cost, stock, lead time."""

    __tablename__ = "supplier_offers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    pack_size: Mapped[str | None] = mapped_column(String(64))
    cost: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    stock: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    supplier: Mapped["Supplier"] = relationship(back_populates="offers")
    product: Mapped["Product"] = relationship(back_populates="offers")

    __table_args__ = (
        # Idempotency: one offer per (supplier, supplier_sku). Re-ingest upserts.
        UniqueConstraint("supplier_id", "supplier_sku", name="uq_offer_supplier_sku"),
    )


class Equivalence(Base, TimestampMixin):
    """First-class product<->product mapping. This is the China<->US matcher output."""

    __tablename__ = "equivalences"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    equivalent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # exact | substitute
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="vector+rules")
    reviewed: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("product_id", "equivalent_id", name="uq_equivalence_pair"),
        CheckConstraint("kind in ('exact','substitute')", name="ck_equivalence_kind"),
        CheckConstraint("product_id <> equivalent_id", name="ck_equivalence_not_self"),
    )


# --------------------------------------------------------------------------- #
# Transaction spine (present now so the schema is whole; filled in M4)
# --------------------------------------------------------------------------- #
class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="biotech")


class Order(Base, TimestampMixin):
    """A customer PO issued TO Astor."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # Idempotency key for order submission (prevents duplicate POs under retry).
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_order_idempotency"),
    )


class OrderLine(Base, TimestampMixin):
    __tablename__ = "order_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    # Customer-facing price + the breakdown behind it (transparency + audit).
    landed_cost: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    order: Mapped["Order"] = relationship(back_populates="lines")


class UpstreamPo(Base, TimestampMixin):
    """Astor's own purchase PO to an upstream supplier. One order may fan out."""

    __tablename__ = "upstream_pos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

    lines: Mapped[list["UpstreamPoLine"]] = relationship(back_populates="po")


class UpstreamPoLine(Base, TimestampMixin):
    __tablename__ = "upstream_po_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    upstream_po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upstream_pos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # References the OFFER, not the product: this is what Astor actually buys.
    supplier_offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="RESTRICT"), nullable=False
    )
    # Captures the sourcing decision as data -> trains the sourcing engine later.
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)

    po: Mapped["UpstreamPo"] = relationship(back_populates="lines")


class Substitution(Base, TimestampMixin):
    """Exception-loop proposal surfaced to the customer for approval."""

    __tablename__ = "substitutions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposed_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)  # oos | discontinued | ...
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
