"""Fondos por cliente: cada cliente apunta a SU venta del CRM

Antes el idventa salía del .env y todo el tráfico se descontaba de la misma
venta (la de Peter), sin importar a qué cliente se le mandaba.

Revision ID: 0006_client_crm_venta
Revises: 0005_usage_qty
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_client_crm_venta"
down_revision = "0005_usage_qty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("crm_idventa", sa.String(length=50), nullable=True))
    op.add_column("clients", sa.Column("crm_idvendedor", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "crm_idvendedor")
    op.drop_column("clients", "crm_idventa")
