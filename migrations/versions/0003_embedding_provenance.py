"""embedding provenance: model + text hash on products

Adds nullable `embedding_model` and `embedding_text_hash` to `products` so a
re-embed becomes a targeted query rather than a guess. Additive and nullable.

NOTE: the live dev DB's alembic history is diverged from this repo (it records a
phantom revision and lacks the protocols table). This migration is written for
fresh DBs; the dev DB gets these columns via idempotent DDL (see the plan). Do
not reconcile that divergence here.

Revision ID: 0003_embedding_provenance
Revises: 0002_protocols
Create Date: 2026-07-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_embedding_provenance"
down_revision = "0002_protocols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("embedding_text_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "embedding_text_hash")
    op.drop_column("products", "embedding_model")
