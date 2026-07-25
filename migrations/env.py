"""
Entorno de Alembic. Toma la conexión de DATABASE_URL y el metadata de
common.models, de modo que `alembic revision --autogenerate` y `alembic upgrade`
funcionen igual en local y en Railway.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Hacer importable el paquete `common` (la raíz del repo es el CWD de alembic).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.db import Base, _normalize_url  # noqa: E402
import common.models  # noqa: F401,E402  (registra las tablas en Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL no está seteada. Alembic necesita la conexión a Postgres "
            "para correr las migraciones."
        )
    return _normalize_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
