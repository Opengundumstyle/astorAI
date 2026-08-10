"""protocol_material_links: material -> product SKU links

Bidirectional link table produced by the material matcher. Additive.

Revision ID: 0005_protocol_material_links
Revises: 0004_protocol_serving_basis
Create Date: 2026-08-10 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_protocol_material_links"
down_revision = "0004_protocol_serving_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocol_material_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("material_name", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("protocol_id", "product_id", "material_name",
                            name="uq_protocol_material_link"),
        sa.CheckConstraint("kind in ('exact','substitute')",
                           name="ck_protocol_material_link_kind"),
    )
    op.create_index("ix_pml_protocol_id", "protocol_material_links", ["protocol_id"])
    op.create_index("ix_pml_product_id", "protocol_material_links", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_pml_product_id", table_name="protocol_material_links")
    op.drop_index("ix_pml_protocol_id", table_name="protocol_material_links")
    op.drop_table("protocol_material_links")
