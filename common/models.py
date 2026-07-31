"""
Modelo de datos multi-tenant (etapa 2).

Jerarquía:
  Account (organización: "Growi (Facu)", "Tony", ...)
    ├── User    (vendedor o admin, se loguea con usuario/contraseña)
    └── Client  (@usuario de IG + prompt de generación)

En esta TAREA 1 se crean las 3 tablas. Campos de tareas posteriores
(género del cliente, rangos de cantidades, logs de uso) se agregan con
migraciones incrementales en sus respectivas tareas.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from common.db import Base


def _utcnow():
    return datetime.now(tz=timezone.utc)


class Account(Base):
    """Cuenta / organización. Cada tenant tiene sus propias credenciales del CRM
    Growi (la password se guarda cifrada; ver common.crypto)."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    # Habilitación del vendedor. Cuando alguien entra por primera vez con sus
    # credenciales de Growi se autoregistra como "pending" y NO puede operar
    # hasta que el admin lo apruebe desde el panel ("approved") o le niegue el
    # acceso ("rejected"). Las cuentas que da de alta el admin nacen aprobadas.
    status = Column(String(20), nullable=False, default="approved")  # pending|approved|rejected
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    # Credenciales del CRM Growi, propias de cada cuenta. Reemplazan al .env global.
    crm_url = Column(String(300), nullable=True)
    crm_email = Column(String(200), nullable=True)
    crm_password_enc = Column(Text, nullable=True)   # cifrada con Fernet
    crm_idvendedor = Column(String(50), nullable=True)
    crm_idventa = Column(String(50), nullable=True)
    crm_proxy = Column(String(400), nullable=True)
    crm_disponible = Column(String(50), nullable=True)  # se guarda como texto, se castea a float al usar

    users = relationship("User", back_populates="account", cascade="all, delete-orphan")
    clients = relationship("Client", back_populates="account", cascade="all, delete-orphan")


class User(Base):
    """Vendedor o admin de una cuenta. Login por usuario/contraseña (hash)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="vendedor")  # "admin" | "vendedor"
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    account = relationship("Account", back_populates="users")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class UsageEvent(Base):
    """Registro de uso: qué acción ejecutó un vendedor, cuándo y sobre qué cliente.
    Alimenta el contador visible para el admin (TAREA 2)."""
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(40), nullable=False)          # "generar" | "publicar" | "enviar_trafico"
    client_ig_username = Column(String(100), nullable=True)
    post_url = Column(Text, nullable=True)
    # Cantidad enviada y tipo de producto ("likes" | "views" | "shares"). Se usan
    # para que la tirada automática no repita una cantidad ya enviada al cliente.
    qty = Column(Integer, nullable=True)
    product_type = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class PromptRequest(Base):
    """Pedido de un vendedor para que le ajusten el prompt de un cliente.

    El vendedor escribe en castellano qué quiere cambiar ("que no mencione la
    competencia", "más cortos"); NO usa la IA (eso quema tokens). El admin ve la
    cola desde /admin, genera el prompt nuevo con IA, lo aplica y lo marca
    resuelto. Reemplaza el ida y vuelta por WhatsApp.
    """
    __tablename__ = "prompt_requests"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    # Si borran el cliente el pedido queda igual (con el @usuario de snapshot),
    # así el admin entiende de qué venía la conversación.
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    client_ig_username = Column(String(100), nullable=False, default="")
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username = Column(String(100), nullable=False, default="")   # snapshot de quién pidió
    text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending|done|discarded
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(100), nullable=True)


class Client(Base):
    """Cliente de engagement: un @usuario de Instagram con su prompt propio.
    Antes vivía en clients_map.json + prompts/clients/<key>.txt."""
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("account_id", "ig_username", name="uq_client_account_iguser"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    ig_username = Column(String(100), nullable=False)       # siempre en minúsculas
    display_name = Column(String(200), nullable=False, default="")
    prompt = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="active")  # "active" | "paused"
    gender = Column(String(10), nullable=True)              # "male" | "female" | None (TAREA 4)
    # Calidad del motor: "pro" (modelo más potente) | "standard" (más liviano y
    # barato). Se carga al dar de alta el cliente y decide con qué modelo de IA
    # se le generan los comentarios. None = standard.
    quality = Column(String(10), nullable=True)
    # Rangos min-max de cantidades por producto (TAREA 6). Ej:
    # {"likes": {"min": 800, "max": 1200}, "views": {...}, "shares": {...}}
    ranges = Column(JSON, nullable=True)
    # Venta del CRM de la que salen los FONDOS de este cliente. Sin esto, todo el
    # tráfico se descontaba del idventa del .env (una sola venta para todos).
    # El idvendedor va aparte porque el CRM imputa la orden a ese par.
    crm_idventa = Column(String(50), nullable=True)
    crm_idvendedor = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    account = relationship("Account", back_populates="clients")
