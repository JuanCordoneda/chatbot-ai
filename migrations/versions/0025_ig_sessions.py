"""Sesiones de Instagram del scraper en la base: tabla ig_sessions

Hasta acá las cookies del scraper vivían en una sola env var de Railway
(INSTAGRAM_COOKIES_JSON). Eso significaba una única cuenta —su caída era la
caída del servicio— y que renovarla necesitaba una Mac concreta con la sesión
abierta y el CLI de Railway. Un checkpoint de Instagram un sábado dejaba a los
vendedores generando posts sin imagen ni transcripción hasta el lunes.

La tabla guarda VARIAS cuentas ordenadas por prioridad, cifradas con Fernet, y
se carga desde el panel de administración. La env var queda como fallback.

Revision ID: 0025_ig_sessions
Revises: 0024_account_tg_chat_id
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_ig_sessions"
down_revision = "0024_account_tg_chat_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ig_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
        sa.Column("ds_user_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("cookies_enc", sa.Text(), nullable=False),
        sa.Column("prioridad", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("estado", sa.String(20), nullable=False, server_default="viva"),
        sa.Column("ultimo_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("caida_desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creada_por", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_ig_sessions_ds_user_id", "ig_sessions", ["ds_user_id"])
    op.create_index("ix_ig_sessions_prioridad", "ig_sessions", ["prioridad"])
    op.create_index("ix_ig_sessions_estado", "ig_sessions", ["estado"])


def downgrade() -> None:
    op.drop_index("ix_ig_sessions_estado", table_name="ig_sessions")
    op.drop_index("ix_ig_sessions_prioridad", table_name="ig_sessions")
    op.drop_index("ix_ig_sessions_ds_user_id", table_name="ig_sessions")
    op.drop_table("ig_sessions")
