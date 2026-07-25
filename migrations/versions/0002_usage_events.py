"""TAREA 2 - registro de uso por vendedor: usage_events

Revision ID: 0002_usage_events
Revises: 0001_initial
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_usage_events"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("client_ig_username", sa.String(length=100), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_usage_events_account_id", "usage_events", ["account_id"])
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_index("ix_usage_events_account_id", table_name="usage_events")
    op.drop_table("usage_events")
