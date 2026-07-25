"""
Engine y sesión de SQLAlchemy, compartidos por ambos servicios.

La conexión se toma de DATABASE_URL (Railway la inyecta sola al agregar el addon
de Postgres). Si DATABASE_URL no está seteada, `engine` queda en None y toda la
capa de datos se considera "no disponible": los llamadores caen al fallback de
archivos. Así el sistema arranca aunque todavía no haya DB configurada.
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


def _normalize_url(url: str) -> str:
    # Railway/Heroku entregan "postgres://"; SQLAlchemy 2.x quiere "postgresql://".
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(
        _normalize_url(DATABASE_URL),
        pool_pre_ping=True,   # evita conexiones muertas tras idle (Railway recicla)
        pool_recycle=1800,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def db_available() -> bool:
    """True si hay una DB configurada. Los llamadores usan esto para decidir si
    intentan la DB o van directo al fallback de archivos."""
    return SessionLocal is not None


@contextmanager
def session_scope():
    """Context manager transaccional. Commit al salir bien, rollback ante error."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL no configurada: no hay sesión de DB disponible")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
