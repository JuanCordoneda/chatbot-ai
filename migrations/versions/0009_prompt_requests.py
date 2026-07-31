"""Pedidos de ajuste de prompt (bandeja del admin)

Los vendedores dejan por escrito qué quieren cambiar en el prompt de un cliente
y el admin resuelve la cola desde /admin (generando el prompt nuevo con IA).
El vendedor NO usa IA: escribe texto plano, así no se gastan tokens por pedido.

Revision ID: 0009_prompt_requests
Revises: 0008_generic_client
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_prompt_requests"
down_revision = "0008_generic_client"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Integer(),
                  sa.ForeignKey("clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("client_ig_username", sa.String(100), nullable=False, server_default=""),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
    )
    op.create_index("ix_prompt_requests_account_id", "prompt_requests", ["account_id"])
    op.create_index("ix_prompt_requests_client_id", "prompt_requests", ["client_id"])
    op.create_index("ix_prompt_requests_status", "prompt_requests", ["status"])
    op.create_index("ix_prompt_requests_created_at", "prompt_requests", ["created_at"])


def downgrade() -> None:
    op.drop_table("prompt_requests")
