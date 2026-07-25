"""Etapa 2 - tablas base multi-tenant: accounts, users, clients

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("crm_url", sa.String(length=300), nullable=True),
        sa.Column("crm_email", sa.String(length=200), nullable=True),
        sa.Column("crm_password_enc", sa.Text(), nullable=True),
        sa.Column("crm_idvendedor", sa.String(length=50), nullable=True),
        sa.Column("crm_idventa", sa.String(length=50), nullable=True),
        sa.Column("crm_proxy", sa.String(length=400), nullable=True),
        sa.Column("crm_disponible", sa.String(length=50), nullable=True),
        sa.UniqueConstraint("slug", name="uq_accounts_slug"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="vendedor"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_account_id", "users", ["account_id"])

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ig_username", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "ig_username", name="uq_client_account_iguser"),
    )
    op.create_index("ix_clients_account_id", "clients", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_clients_account_id", table_name="clients")
    op.drop_table("clients")
    op.drop_index("ix_users_account_id", table_name="users")
    op.drop_table("users")
    op.drop_table("accounts")
