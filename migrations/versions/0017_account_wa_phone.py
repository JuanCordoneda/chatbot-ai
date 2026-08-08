"""WhatsApp del vendedor: columna wa_phone en accounts

Cada vendedor recibe SU tanda de comentarios en SU WhatsApp y desde ahí la
reenvía a donde quiera. Hasta ahora el destino era uno solo, fijo en el .env,
que servía para probar pero le mandaba las tandas de todos a la misma persona.

Va en `accounts` y no en `users` porque los vendedores entran con sus
credenciales de Growi y no tienen fila en `users` (la tabla está vacía): la
sesión les guarda account_id y user_id=None. Una cuenta = un vendedor = un
WhatsApp.

Nullable: las cuentas que ya existen arrancan sin número y el sistema se lo pide
la primera vez que reparten.

Revision ID: 0017_account_wa_phone
Revises: 0016_drop_client_wa_group
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_account_wa_phone"
down_revision = "0016_drop_client_wa_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("wa_phone", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "wa_phone")
