"""Contabilidad de tokens y caché persistente de posts

Dos tablas con el mismo objetivo: dejar de gastar de más sin saberlo.

- token_usage: un registro por llamada a la IA (modelo, tokens, costo). Antes no
  se medía nada, así que el costo por post era una estimación y no se sabía
  cuánto se iba en el "thinking" ni en los reintentos.
- post_cache: el post ya extraído (caption, transcripción, descripción, imagen
  reducida). El caché en memoria se perdía en cada reinicio y no se comparte
  entre workers; volver a pegar el mismo link pagaba scrape + whisper + visión
  de nuevo.

Revision ID: 0014_token_usage_post_cache
Revises: 0013_pending_orders
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_token_usage_post_cache"
down_revision = "0013_pending_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("intento", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("costo_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("client_ig_username", sa.String(100), nullable=True),
        sa.Column("shortcode", sa.String(40), nullable=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_token_usage_kind", "token_usage", ["kind"])
    op.create_index("ix_token_usage_client_ig_username", "token_usage", ["client_ig_username"])
    op.create_index("ix_token_usage_account_id", "token_usage", ["account_id"])
    op.create_index("ix_token_usage_created_at", "token_usage", ["created_at"])

    op.create_table(
        "post_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shortcode", sa.String(40), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_username", sa.String(100), nullable=True),
        sa.Column("owner_full_name", sa.String(200), nullable=True),
        sa.Column("transcription", sa.Text(), nullable=False, server_default=""),
        sa.Column("photo_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_video", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("image_b64", sa.Text(), nullable=True),
        sa.Column("image_media_type", sa.String(40), nullable=True),
        sa.Column("n_imagenes", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_post_cache_shortcode", "post_cache", ["shortcode"], unique=True)
    op.create_index("ix_post_cache_created_at", "post_cache", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_post_cache_created_at", table_name="post_cache")
    op.drop_index("ix_post_cache_shortcode", table_name="post_cache")
    op.drop_table("post_cache")
    op.drop_index("ix_token_usage_created_at", table_name="token_usage")
    op.drop_index("ix_token_usage_account_id", table_name="token_usage")
    op.drop_index("ix_token_usage_client_ig_username", table_name="token_usage")
    op.drop_index("ix_token_usage_kind", table_name="token_usage")
    op.drop_table("token_usage")
