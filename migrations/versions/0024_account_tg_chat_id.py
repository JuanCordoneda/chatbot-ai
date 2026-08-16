"""Telegram del vendedor: columna tg_chat_id en accounts

El equivalente de `wa_phone` (migración 0017) para el otro destino del reparto.
Va en `accounts` por el mismo motivo: los vendedores entran con sus credenciales
de Growi y no tienen fila en `users`, así que una cuenta = un vendedor = un chat.

Se guarda como texto y no como entero: los chat_id de Telegram son enteros con
signo (los grupos son negativos) y pueden pasarse de los 32 bits.

Nullable: las cuentas que ya existen arrancan sin vincular, y el sistema les
ofrece hacerlo la primera vez que reparten por Telegram.

Revision ID: 0024_account_tg_chat_id
Revises: 0023_prompt_solo_default
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_account_tg_chat_id"
down_revision = "0023_prompt_solo_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("tg_chat_id", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "tg_chat_id")
