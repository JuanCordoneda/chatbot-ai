"""Habilitación de vendedores: accounts.status (pending|approved|rejected)

El vendedor que entra por primera vez con sus credenciales de Growi se
autoregistra como "pending" y queda a la espera de que el admin lo habilite.
Las cuentas que ya existían se dan por aprobadas.

Revision ID: 0007_account_status
Revises: 0006_client_crm_venta
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_account_status"
down_revision = "0006_client_crm_venta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column(
        "status", sa.String(length=20), nullable=False, server_default="approved"))


def downgrade() -> None:
    op.drop_column("accounts", "status")
