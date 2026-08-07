"""Grupo de WhatsApp por cliente: columna wa_group_url

El vendedor ya tiene un grupo de WhatsApp por cliente y reparte ahí los
comentarios a mano, uno por uno. Guardar el link de invitación en la ficha
permite que el botón de repartir abra el grupo correcto en vez de obligarlo a
buscarlo en el selector de chats cada vez (que es donde se mezclan los clientes).

No sirve para que el bot postee: la Cloud API de Meta no manda mensajes a
grupos. Es un dato para el navegador del vendedor, nada más.

Nullable y sin default: los clientes que ya existen quedan sin grupo y el botón
simplemente cae al selector de chats de siempre.

Revision ID: 0015_client_wa_group
Revises: 0014_token_usage_post_cache
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_client_wa_group"
down_revision = "0014_token_usage_post_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("wa_group_url", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "wa_group_url")
