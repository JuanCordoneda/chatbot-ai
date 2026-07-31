"""Calidad del motor por cliente: columna quality ('pro' | 'standard')

Cada cliente decide con qué modelo de IA se le generan los comentarios. Se carga
una sola vez desde el admin al dar de alta al cliente.

Los clientes que YA existen quedan en 'pro': hasta ahora TODOS corrían en el
modelo caro, así que ponerlos en 'standard' les bajaría la calidad sin que nadie
lo haya decidido. El default de los clientes nuevos ('standard') se elige en el
alta, no acá.

Revision ID: 0010_client_quality
Revises: 0009_prompt_requests
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_client_quality"
down_revision = "0009_prompt_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("quality", sa.String(10), nullable=True))
    op.execute("UPDATE clients SET quality = 'pro' WHERE quality IS NULL")


def downgrade() -> None:
    op.drop_column("clients", "quality")
