"""Trazabilidad de requests/responses al CRM de Growi

Una fila por llamada al CRM (login, preflight, envío de órdenes, consultas), con
el payload enviado, la respuesta cruda, el status, la duración y el proxy usado.
Antes esto solo existía como print: cuando un vendedor reclamaba al día
siguiente que "la orden no entró", los logs ya se habían rotado.

Revision ID: 0018_growi_calls
Revises: 0017_account_wa_phone
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_growi_calls"
down_revision = "0017_account_wa_phone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "growi_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trace_id", sa.String(36), nullable=True),
        sa.Column("origen", sa.String(20), nullable=False, server_default="web"),
        sa.Column("operacion", sa.String(50), nullable=False),
        sa.Column("method", sa.String(10), nullable=False, server_default="POST"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(200), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("proxy", sa.String(200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("client_ig_username", sa.String(100), nullable=True),
        sa.Column("idventa", sa.String(50), nullable=True),
        sa.Column("idvendedor", sa.String(50), nullable=True),
        sa.Column("costo", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_growi_calls_trace_id", "growi_calls", ["trace_id"])
    op.create_index("ix_growi_calls_operacion", "growi_calls", ["operacion"])
    op.create_index("ix_growi_calls_account_id", "growi_calls", ["account_id"])
    op.create_index("ix_growi_calls_client_ig_username", "growi_calls", ["client_ig_username"])
    # La vista del panel ordena siempre por fecha descendente y la purga borra
    # por antigüedad: sin este índice ambas cosas son un seq scan.
    op.create_index("ix_growi_calls_created_at", "growi_calls", ["created_at"])


def downgrade() -> None:
    op.drop_table("growi_calls")
