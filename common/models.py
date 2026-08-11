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
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
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
    # NO hay columna de contraseña, y es a propósito: la de Growi la tipea el
    # vendedor en cada login y vive solo en memoria del webService mientras dure
    # su sesión. La columna `crm_password_enc` (Fernet) existió hasta la
    # migración 0021, que la borró junto con las contraseñas que tenía adentro.
    crm_idvendedor = Column(String(50), nullable=True)
    crm_idventa = Column(String(50), nullable=True)
    crm_proxy = Column(String(400), nullable=True)
    # WhatsApp del vendedor: a dónde se le mandan las tandas de comentarios para
    # que las reenvíe. Lo carga él mismo la primera vez que reparte, y lo puede
    # cambiar después. Es de la CUENTA y no del User porque los vendedores entran
    # con sus credenciales de Growi y no tienen fila en `users`.
    wa_phone = Column(String(30), nullable=True)
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
    # Cantidad enviada y tipo de producto ("likes" | "views" | "shares" |
    # "reposts" | "saves" | "reach"). Se usan
    # para que la tirada automática no repita una cantidad ya enviada al cliente.
    qty = Column(Integer, nullable=True)
    product_type = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class TokenUsage(Base):
    """Un registro por llamada a la API de IA: qué modelo, cuántos tokens y qué
    costó. Antes no se medía nada, así que el costo por post era una estimación y
    no se podía saber qué parte se iba en el 'thinking' ni cuánto cuestan los
    reintentos. Con esta tabla el admin ve plata real por cliente y por día.

    Se escribe best-effort: si falla el insert, la generación sigue igual (nunca
    se le arruina una tanda al vendedor por no poder loguear).
    """
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True)
    # "descripcion" (visión) | "generacion" (la tanda de comentarios)
    kind = Column(String(20), nullable=False, index=True)
    model = Column(String(60), nullable=False)
    # Nº de intento dentro de generar_comentarios_stream: >1 es plata tirada.
    intento = Column(Integer, nullable=False, default=1, server_default="1")
    input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    # Los tokens cacheados se facturan distinto (lectura ~0.1x, escritura ~1.25x),
    # así que se guardan aparte para que el costo salga bien.
    cache_read_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    cache_creation_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    # Costo en USD calculado al momento de la llamada, con la tarifa vigente.
    # Se guarda calculado (y no solo los tokens) para que un cambio de precios
    # no reescriba la historia de lo que ya se gastó.
    costo_usd = Column(Float, nullable=False, default=0.0, server_default="0")
    # Contexto, todo opcional: sirve para agrupar el gasto pero nunca para decidir
    # si se loguea. Las funciones de IA no siempre conocen la cuenta/vendedor.
    client_ig_username = Column(String(100), nullable=True, index=True)
    shortcode = Column(String(40), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class PostCache(Base):
    """Post de Instagram ya procesado, guardado para no volver a pagarlo.

    Extraer un post es lo caro del sistema: scrape a Instagram, descarga del
    video, whisper y una llamada de visión. El caché en memoria del proceso
    (post_processor._scrape_cache) resolvía el "Cargar más" pero se perdía en cada
    reinicio y no se comparte entre workers, así que volver a pegar el mismo link
    diez minutos después pagaba todo de nuevo. Esta tabla lo hace persistente.

    NO guarda comentarios generados: cada tanda tiene que salir distinta.
    """
    __tablename__ = "post_cache"

    id = Column(Integer, primary_key=True)
    shortcode = Column(String(40), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    caption = Column(Text, nullable=False, default="", server_default="")
    owner_username = Column(String(100), nullable=True)
    owner_full_name = Column(String(200), nullable=True)
    # Colaboradores del post (`coauthor_producers` de Instagram): lista de
    # @usuarios en minúsculas. Se guarda porque la asignación del cliente los
    # mira: un post que publica una cuenta partner con el cliente como collab es
    # del cliente. Si no viviera en el caché, un hit resolvería otro cliente que
    # el scrape original — la asignación cambiaría según si el post estaba
    # cacheado o no.
    collaborators = Column(JSON, nullable=True)
    transcription = Column(Text, nullable=False, default="", server_default="")
    photo_description = Column(Text, nullable=False, default="", server_default="")
    is_video = Column(Boolean, nullable=False, default=False, server_default="false")
    # La imagen ya reducida (lado máximo 1024, JPEG q80): ~5 KB en base64. Se
    # guarda para no re-scrapear NI re-describir; es lo que hace que un hit del
    # caché no cueste nada de IA.
    image_b64 = Column(Text, nullable=True)
    image_media_type = Column(String(40), nullable=True)
    n_imagenes = Column(Integer, nullable=False, default=1, server_default="1")
    # Para métricas: cuántas veces se reusó y cuándo fue la última.
    hits = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    last_hit_at = Column(DateTime(timezone=True), nullable=True)


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
    # Modo palabra clave: este cliente NO quiere comentarios de verdad, quiere N
    # veces una palabra (CLAUDE / Claude / claude), como los que deja la gente
    # para que el bot del creador le mande el recurso. La palabra sale del
    # caption de cada post; acá solo se decide que el cliente trabaja así.
    keyword_mode = Column(Boolean, nullable=False, default=False, server_default="false")
    # Rangos min-max de cantidades por producto (TAREA 6). Ej:
    # Cada tipo es una LISTA de entradas, porque un mismo post puede llevar dos
    # calidades del mismo producto con rangos distintos. Ej:
    # {"likes": [{"min": 800, "max": 1200, "prod_id": "12", "prod_nombre": "Likes"},
    #            {"min": 100, "max": 200, "prod_id": "37", "prod_nombre": "Likes JAP"}],
    #  "views": [...], "shares": [...], "reposts": [...], "saves": [...], "reach": [...]}
    # Las fichas viejas guardaron un dict suelto por tipo: se sigue leyendo.
    ranges = Column(JSON, nullable=True)
    # Venta del CRM de la que salen los FONDOS de este cliente. Sin esto, todo el
    # tráfico se descontaba del idventa del .env (una sola venta para todos).
    # El idvendedor va aparte porque el CRM imputa la orden a ese par.
    crm_idventa = Column(String(50), nullable=True)
    crm_idvendedor = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    account = relationship("Account", back_populates="clients")


class GrowiCall(Base):
    """Una fila por cada ENVÍO de órdenes al CRM de Growi, con su respuesta.

    Existe porque el CRM es una caja negra de terceros y cuando algo sale mal
    (una orden que "no entró", un 401, una sesión que se cae) lo único que había
    era el print del último deploy: los logs se rotan y el vendedor reclama al
    día siguiente. Con esta tabla se puede contestar qué se mandó exactamente,
    qué contestó el CRM, cuánto tardó y por qué proxy salió.

    Se escribe best-effort: si el insert falla, el envío sigue igual. Nunca se
    le arruina una campaña a un vendedor por no poder auditar.
    """
    __tablename__ = "growi_calls"

    id = Column(Integer, primary_key=True)
    # Correlaciona los envíos de una misma acción del usuario (un click que
    # dispara más de una orden).
    trace_id = Column(String(36), nullable=True, index=True)
    # De dónde salió: "web" (webService), "openai" (generación), "cola"
    # (worker de reintentos), "monitor" (health check).
    origen = Column(String(20), nullable=False, default="web")
    # Hoy siempre "enviar_trafico". Queda como columna (y no como constante
    # implícita) para poder sumar otra operación sin migrar la tabla.
    operacion = Column(String(50), nullable=False, index=True)
    method = Column(String(10), nullable=False, default="POST")
    url = Column(Text, nullable=False)

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username = Column(String(200), nullable=True)

    # El request y el response completos, ya redactados (sin passwords ni
    # cookies) y truncados. El cuerpo enviado va como JSON porque se consulta
    # por dentro (qué órdenes tenía); la respuesta va como texto porque el CRM a
    # veces devuelve HTML de login en vez del JSON esperado.
    request_payload = Column(JSON, nullable=True)
    request_headers = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    response_headers = Column(JSON, nullable=True)

    status_code = Column(Integer, nullable=True)
    # False también cuando el CRM contestó 200 pero con success=false.
    ok = Column(Boolean, nullable=False, default=False, server_default="false")
    duracion_ms = Column(Integer, nullable=True)
    # Reintentos gastados dentro de ESTA llamada (relogin por sesión caída, 401).
    intentos = Column(Integer, nullable=False, default=1, server_default="1")
    proxy = Column(String(200), nullable=True)   # ofuscado: nunca la password del proxy
    error = Column(Text, nullable=True)

    # Contexto de negocio, todo opcional: sirve para buscar ("¿qué le mandamos a
    # @cliente ayer?") pero nunca para decidir si se loguea.
    post_url = Column(Text, nullable=True)
    client_ig_username = Column(String(100), nullable=True, index=True)
    idventa = Column(String(50), nullable=True)
    idvendedor = Column(String(50), nullable=True)
    costo = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class PendingOrder(Base):
    """Orden armada que NO se pudo mandar al CRM, guardada para reintentar sola.

    Nació de una caída del proxy de salida: el vendedor generaba 89 comentarios,
    apretaba Publicar, y el envío fallaba por un problema de red. Los comentarios
    se perdían y había que rehacer todo a mano. Con esta tabla, una caída de 20
    minutos es un retraso de 20 minutos en vez de trabajo tirado.

    IMPORTANTE: solo se encolan los envíos que sabemos que NUNCA salieron
    (fallo al conectar). Si el POST llegó al CRM y lo que se cortó fue la
    respuesta, la orden pudo haber entrado: reintentarla la duplicaría, así que
    esa queda para revisión manual. Ver growi_client._falló_al_conectar.
    """
    __tablename__ = "pending_orders"

    id = Column(Integer, primary_key=True)
    # Nullable: el flujo del bot de WhatsApp no tiene cuenta asociada todavía.
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"),
                        nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                     nullable=True, index=True)
    post_url = Column(Text, nullable=False)
    client_ig_username = Column(String(100), nullable=True)
    # Todo lo necesario para reconstruir el envío tal cual:
    # {"comentarios": [...], "ordenes": [...], "disponible": 150.0}
    payload = Column(JSON, nullable=False)
    # pendiente → en cola | enviando → la tomó un worker | enviada → OK
    # fallida   → se agotaron los reintentos | revisar → puede haber entrado
    estado = Column(String(20), nullable=False, default="pendiente",
                    server_default="pendiente", index=True)
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
    ultimo_error = Column(Text, nullable=True)
    # Cuándo volver a intentar. Indexado porque el worker filtra por esto.
    proximo_intento = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow,
                        onupdate=_utcnow)
    enviada_at = Column(DateTime(timezone=True), nullable=True)
