"""protocol serving_basis: records the contractual authorization for a served row

protocols.io records carry no licence, so the API maps them to UNKNOWN and the
gate fails them closed to link-out. A commercial data licence authorizes serving
them anyway — but that authorization lives in the CONTRACT, not the payload. This
column records, per row, the serving_basis under which its content was made
servable (e.g. "commercial-licence:pio-approval-2026-08"), so the decision is
auditable at rest, not just in the ingest run's manifest. Additive and nullable;
NULL means "servable purely on its own permissive licence" (or not servable).

Revision ID: 0004_protocol_serving_basis
Revises: 0003_embedding_provenance
Create Date: 2026-08-10 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_protocol_serving_basis"
down_revision = "0003_embedding_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("protocols", sa.Column("serving_basis", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("protocols", "serving_basis")
