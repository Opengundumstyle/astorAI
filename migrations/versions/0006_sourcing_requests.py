"""sourcing_requests: captured unmet demand from the chat. Additive.

Revision ID: 0006_sourcing_requests
Revises: 0005_protocol_material_links
Create Date: 2026-08-14 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_sourcing_requests"
down_revision = "0005_protocol_material_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sourcing_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("requested_item", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("shop", sa.String(length=255)),
        sa.Column("customer_id", sa.String(length=64)),
        sa.Column("email", sa.String(length=320)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'new'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sourcing_requests_created_at", "sourcing_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_sourcing_requests_created_at", table_name="sourcing_requests")
    op.drop_table("sourcing_requests")
