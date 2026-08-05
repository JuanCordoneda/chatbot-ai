"""Cola de órdenes pendientes de envío al CRM

Cuando el envío a Growi falla por un problema de red, la orden ya armada se
guarda acá en vez de perderse, y un worker la reintenta con backoff. Antes, una
caída del proxy significaba que el vendedor tenía que regenerar y recargar todo
a mano.

Revision ID: 0013_pending_orders
Revises: 0012_client_keyword_mode
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_pending_orders"
down_revision = "0012_client_keyword_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=False),
        sa.Column("client_ig_username", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_error", sa.Text(), nullable=True),
        sa.Column("proximo_intento", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("enviada_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pending_orders_account_id", "pending_orders", ["account_id"])
    op.create_index("ix_pending_orders_user_id", "pending_orders", ["user_id"])
    op.create_index("ix_pending_orders_estado", "pending_orders", ["estado"])
    op.create_index("ix_pending_orders_created_at", "pending_orders", ["created_at"])
    # El worker consulta "pendientes cuyo proximo_intento ya venció" en cada
    # vuelta: sin este índice sería un seq scan cada 60 segundos.
    op.create_index("ix_pending_orders_proximo_intento", "pending_orders",
                    ["proximo_intento"])


def downgrade() -> None:
    op.drop_table("pending_orders")
