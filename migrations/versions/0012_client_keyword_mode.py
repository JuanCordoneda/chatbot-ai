"""Modo palabra clave por cliente: columna keyword_mode

Hay cuentas cuyos posts no piden comentarios de verdad, piden que la gente
comente una palabra ("comment CLAUDE and I'll send you the PDF"). Esos clientes
se marcan una vez en su ficha y de ahí en adelante todos sus posts se generan en
modo keyword. La palabra de cada post no se guarda acá: sale del caption del post
y el vendedor la confirma antes de generar.

Todos los clientes que ya existen quedan en false: hasta ahora ninguno trabajaba
así, y prenderlo por error le cambiaría los comentarios a todo el mundo.

Revision ID: 0012_client_keyword_mode
Revises: 0011_keyword_client
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_client_keyword_mode"
down_revision = "0011_keyword_client"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column(
        "keyword_mode", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("clients", "keyword_mode")
