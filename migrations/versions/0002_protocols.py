"""protocols: Plane 2 scientific-grounding corpus

Adds the `protocols` table. Two dedupe axes (see astor.db.models.Protocol):
`(source, source_id)` always, plus a PARTIAL unique index on DOI — partial
because DOI is nullable and Postgres counts each NULL as distinct, so a plain
unique constraint would look enforced while permitting duplicate DOI-less rows.

Revision ID: 0002_protocols
Revises: 0001_initial
Create Date: 2026-07-19 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

from astor.config import settings

revision = "0002_protocols"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocols",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_uri", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("authors", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("doi", sa.String(255)),
        sa.Column("version", sa.String(64)),
        sa.Column("license", sa.String(32), nullable=False),
        sa.Column("servable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("steps", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("materials", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("review", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rank_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("embedding", Vector(settings.embedding_dim)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_protocol_source_id"),
    )
    op.create_index("ix_protocols_source", "protocols", ["source"])
    op.create_index("ix_protocols_servable", "protocols", ["servable"])
    op.create_index("ix_protocol_rank", "protocols", ["rank_score"])
    op.create_index(
        "uq_protocol_doi", "protocols", ["doi"], unique=True,
        postgresql_where=sa.text("doi IS NOT NULL"),
    )
    op.create_index(
        "ix_protocol_embedding_hnsw", "protocols", ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("protocols")
