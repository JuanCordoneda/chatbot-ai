"""TAREA 6 - rangos de cantidades por cliente: columna ranges (JSON)

Revision ID: 0004_client_ranges
Revises: 0003_client_gender
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_client_ranges"
down_revision = "0003_client_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("ranges", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "ranges")
