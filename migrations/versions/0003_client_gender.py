"""TAREA 4 - género del cliente: columna gender en clients

Revision ID: 0003_client_gender
Revises: 0002_usage_events
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_client_gender"
down_revision = "0002_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("gender", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "gender")
