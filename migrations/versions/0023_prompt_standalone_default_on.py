"""prompt_standalone pasa a venir prendido de fábrica

0022 agregó la columna apagada porque hasta ese momento TODOS los clientes se
armaban por capas y prenderla les cambiaba los comentarios de golpe. De acá en
adelante la decisión es la contraria: un cliente nuevo nace con su prompt solo,
sin las reglas generales arriba.

Esto cambia el DEFAULT, no las fichas: las que ya existen tienen el false
guardado en su fila y siguen armándose por capas. Prenderlas es una decisión
aparte, cliente por cliente desde el panel (o un UPDATE, si algún día se decide
que van todas).

Revision ID: 0023_prompt_solo_default
Revises: 0022_client_prompt_solo
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_prompt_solo_default"
down_revision = "0022_client_prompt_solo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("clients", "prompt_standalone", server_default="true")


def downgrade() -> None:
    op.alter_column("clients", "prompt_standalone", server_default="false")
