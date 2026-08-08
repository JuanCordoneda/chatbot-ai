"""Saca wa_group_url de clients

La idea de dirigir cada tanda al grupo del cliente se descartó: el bot manda
los comentarios a UN WhatsApp y desde ahí la persona los reenvía adonde quiera.
El destino lo elige WhatsApp, no nosotros, así que guardar el grupo en la ficha
era un campo más que llenar para un botón que nadie iba a usar.

La 0015 agregó la columna y esta la saca. Se deja el downgrade por si hiciera
falta volver, aunque los links que hubiera cargados se pierden.

Revision ID: 0016_drop_client_wa_group
Revises: 0015_client_wa_group
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_drop_client_wa_group"
down_revision = "0015_client_wa_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("clients", "wa_group_url")


def downgrade() -> None:
    op.add_column("clients", sa.Column("wa_group_url", sa.String(255), nullable=True))
