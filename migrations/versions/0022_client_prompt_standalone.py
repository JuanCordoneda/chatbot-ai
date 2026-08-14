"""Prompt sin capa genérica por cliente: columna prompt_standalone

El prompt final del motor son dos capas: el genérico (reglas de oficio para
todos) + el del cliente pegado abajo. Para casi todos está bien, pero hay
cuentas cuyo estilo es lo contrario de las reglas generales y arrancan peleadas
con la capa de arriba: el admin terminaba escribiendo "ignorá todo lo anterior"
y pagando igual los tokens del genérico en cada tanda.

Con esto prendido, el cliente usa SOLO su prompt: el genérico no se arma ni se
manda. El FORMATO DE SALIDA sigue yendo siempre — lo pone el sistema y el
parseo de la respuesta depende de sus encabezados.

Todos los clientes que ya existen quedan en false: hoy todos trabajan por capas
y prenderlo por error les cambiaría los comentarios de golpe.

Revision ID: 0022_client_prompt_solo
Revises: 0021_drop_account_crm_pwd
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_client_prompt_solo"
down_revision = "0021_drop_account_crm_pwd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column(
        "prompt_standalone", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("clients", "prompt_standalone")
