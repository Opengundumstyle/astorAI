"""SupplierOffer.external_id — the source's stable id for an offer (Shopify variant id).

The catalog had no key that survives a SKU rename, so it could not be re-synced:
re-running the ingest inserted every product a second time. This column is the
sync key (see astor/catalog/sync.py). Existing rows are NULL and are backfilled
by the first run of scripts/sync_shopify.py.

Equivalent DDL, for a database whose alembic chain is broken (see docs):
  ALTER TABLE supplier_offers ADD COLUMN IF NOT EXISTS external_id varchar(64);
  CREATE UNIQUE INDEX IF NOT EXISTS uq_offer_supplier_external_id
    ON supplier_offers (supplier_id, external_id) WHERE external_id IS NOT NULL;

Revision ID: 0008_offer_external_id
Revises: 0007_product_sellable
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_offer_external_id"
down_revision = "0007_product_sellable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supplier_offers", sa.Column("external_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_offer_supplier_external_id", "supplier_offers", ["supplier_id", "external_id"],
        unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_offer_supplier_external_id", table_name="supplier_offers")
    op.drop_column("supplier_offers", "external_id")
