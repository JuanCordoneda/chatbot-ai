"""Saca crm_password_enc de accounts

La contraseña de Growi del vendedor deja de guardarse. Ahora la tipea al entrar
y vive solo en la memoria del webService mientras dura su sesión; si el proceso
se reinicia, vuelve a pedírsela.

Borrar la columna es el punto del cambio, no un detalle de limpieza: mientras la
columna exista, las contraseñas que ya están adentro siguen en el disco de la
base y en cada backup. Estaban cifradas con Fernet, pero la clave
(DB_ENCRYPTION_KEY) vive en el mismo entorno que la base, así que quien tuviera
las dos cosas las leía en claro.

El downgrade recrea la columna VACÍA. No se puede hacer otra cosa: las
contraseñas se borran acá y no hay de dónde sacarlas. Volviendo atrás, cada
vendedor tiene que loguearse de nuevo para que el sistema pueda operar a su
nombre — que es exactamente el comportamiento nuevo.

Revision ID: 0021_drop_account_crm_pwd
Revises: 0020_post_cache_collabs
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_drop_account_crm_pwd"
down_revision = "0020_post_cache_collabs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("accounts", "crm_password_enc")


def downgrade() -> None:
    op.add_column("accounts", sa.Column("crm_password_enc", sa.Text(), nullable=True))
