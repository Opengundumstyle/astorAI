"""initial schema: catalog spine + transaction spine + pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

from astor.config import settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid()

    ts = lambda: (  # noqa: E731
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    pk = lambda: sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))  # noqa: E731

    op.create_table(
        "suppliers", pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(32), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False, server_default="public"),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.false()),
        *ts(),
        sa.UniqueConstraint("name", name="uq_supplier_name"),
        sa.CheckConstraint("tier in ('public','authorized','deep')", name="ck_supplier_tier"),
    )

    op.create_table(
        "products", pk(),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("brand", sa.String(128)),
        sa.Column("mpn", sa.String(128)),
        sa.Column("specs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding", Vector(settings.embedding_dim)),
        *ts(),
        sa.UniqueConstraint("brand", "mpn", name="uq_product_brand_mpn"),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.execute(
        "CREATE INDEX ix_product_embedding_hnsw ON products "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "supplier_offers", pk(),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_sku", sa.String(128), nullable=False),
        sa.Column("pack_size", sa.String(64)),
        sa.Column("cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("stock", sa.Integer),
        sa.Column("lead_time_days", sa.Integer),
        *ts(),
        sa.UniqueConstraint("supplier_id", "supplier_sku", name="uq_offer_supplier_sku"),
    )
    op.create_index("ix_supplier_offers_supplier_id", "supplier_offers", ["supplier_id"])
    op.create_index("ix_supplier_offers_product_id", "supplier_offers", ["product_id"])

    op.create_table(
        "equivalences", pk(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("equivalent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="vector+rules"),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.false()),
        *ts(),
        sa.UniqueConstraint("product_id", "equivalent_id", name="uq_equivalence_pair"),
        sa.CheckConstraint("kind in ('exact','substitute')", name="ck_equivalence_kind"),
        sa.CheckConstraint("product_id <> equivalent_id", name="ck_equivalence_not_self"),
    )
    op.create_index("ix_equivalences_product_id", "equivalences", ["product_id"])
    op.create_index("ix_equivalences_equivalent_id", "equivalences", ["equivalent_id"])

    op.create_table(
        "customers", pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default="biotech"),
        *ts(),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])

    op.create_table(
        "orders", pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("idempotency_key", sa.String(128)),
        *ts(),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_order_idempotency"),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

    op.create_table(
        "order_lines", pk(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("landed_cost", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *ts(),
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])

    op.create_table(
        "upstream_pos", pk(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        *ts(),
    )
    op.create_index("ix_upstream_pos_order_id", "upstream_pos", ["order_id"])
    op.create_index("ix_upstream_pos_supplier_id", "upstream_pos", ["supplier_id"])

    op.create_table(
        "upstream_po_lines", pk(),
        sa.Column("upstream_po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("upstream_pos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_offer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("supplier_offers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("cost", sa.Numeric(12, 4), nullable=False),
        *ts(),
    )
    op.create_index("ix_upstream_po_lines_po_id", "upstream_po_lines", ["upstream_po_id"])

    op.create_table(
        "substitutions", pk(),
        sa.Column("order_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposed_product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        *ts(),
    )
    op.create_index("ix_substitutions_order_line_id", "substitutions", ["order_line_id"])


def downgrade() -> None:
    for t in [
        "substitutions", "upstream_po_lines", "upstream_pos", "order_lines",
        "orders", "customers", "equivalences", "supplier_offers", "products", "suppliers",
    ]:
        op.drop_table(t)
