"""Headers del request y del response en la traza del CRM

La traza guardaba solo los cuerpos, y eso cuenta la mitad de la historia: el CRM
responde distinto según el referer y el x-requested-with, y el content-type de
la respuesta es lo que delata cuándo nos devolvió el HTML del login en vez del
JSON esperado.

Se guardan redactados: las cookies de sesión del CRM nunca entran a la tabla.

Revision ID: 0019_growi_calls_headers
Revises: 0018_growi_calls
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_growi_calls_headers"
down_revision = "0018_growi_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("growi_calls", sa.Column("request_headers", sa.JSON(), nullable=True))
    op.add_column("growi_calls", sa.Column("response_headers", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("growi_calls", "response_headers")
    op.drop_column("growi_calls", "request_headers")
