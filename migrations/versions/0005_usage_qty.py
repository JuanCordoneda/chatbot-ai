"""Cantidad y tipo de producto en el registro de uso

Permite que la tirada automática de cantidades (likes/views/shares) no repita
un número ya enviado para ese cliente y tipo de producto.

Revision ID: 0005_usage_qty
Revises: 0004_client_ranges
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_usage_qty"
down_revision = "0004_client_ranges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("qty", sa.Integer(), nullable=True))
    op.add_column("usage_events", sa.Column("product_type", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "product_type")
    op.drop_column("usage_events", "qty")
