"""Product.sellable — can a shopper actually buy this?

The Shopify Admin API returns every product regardless of status, so archived,
draft and unpublished items were ingested and recommended to customers. Existing
rows default to true; the next Shopify sync corrects the 316 that are not.

Revision ID: 0007_product_sellable
Revises: 0006_sourcing_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_product_sellable"
down_revision = "0006_sourcing_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("sellable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_products_sellable", "products", ["sellable"])


def downgrade() -> None:
    op.drop_index("ix_products_sellable", table_name="products")
    op.drop_column("products", "sellable")
