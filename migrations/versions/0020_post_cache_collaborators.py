"""Colaboradores del post en el caché

La asignación del cliente miraba solo el dueño del post, y los collabs se
resolvían con un diccionario hardcodeado de un caso. Ahora se leen los
colaboradores reales que devuelve Instagram, así que el caché tiene que
guardarlos: si no, un hit del caché resolvería un cliente distinto al del
scrape original.

Revision ID: 0020_post_cache_collabs
Revises: 0019_growi_calls_headers
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_post_cache_collabs"
down_revision = "0019_growi_calls_headers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("post_cache", sa.Column("collaborators", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("post_cache", "collaborators")
