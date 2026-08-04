"""
Acceso de alto nivel a la DB. Todas las funciones de lectura devuelven None (o
lista vacía) cuando la DB no está disponible, para que el llamador caiga al
fallback de archivos sin romperse.

Devuelven dicts desacoplados de la sesión (no objetos ORM vivos) para evitar
problemas de lazy-loading fuera del `session_scope`.
"""
from typing import Optional

from werkzeug.security import check_password_hash

from common.db import db_available, session_scope
from common.models import Account, User, Client, PromptRequest, UsageEvent
from common import crypto


# ── Clientes ──────────────────────────────────────────────────────────────────

# Cliente reservado del sistema: el que se aplica a TODO post que no es de un
# cliente cargado, en cualquier cuenta. Es UNO SOLO global (la fila vive en una
# cuenta cualquiera, pero se busca sin filtrar por cuenta) y no se gestiona con
# el CRUD normal: los vendedores ni lo ven, y solo el admin le edita el prompt.
GENERIC_IG = "__generico__"

# Mismo mecanismo, otra herramienta: el prompt del MODO KEYWORD (el vendedor
# escribe una palabra y salen N comentarios que son solo esa palabra, variando
# mayúsculas/minúsculas). Vive acá para que el admin lo edite desde el panel
# igual que el genérico, sin redeploy.
KEYWORD_IG = "__keyword__"

# Todos los reservados: nunca salen en el CRUD normal ni en la lista del vendedor.
SYSTEM_IGS = (GENERIC_IG, KEYWORD_IG)


def is_generic(ig_username) -> bool:
    return (ig_username or "").strip().lstrip("@").lower() == GENERIC_IG


def is_system(ig_username) -> bool:
    return (ig_username or "").strip().lstrip("@").lower() in SYSTEM_IGS


_GENDERS = ("male", "female")


def _norm_gender(g):
    """Normaliza el género a 'male'/'female' o None. Acepta variantes ES/EN."""
    if not g:
        return None
    g = str(g).strip().lower()
    if g in ("male", "hombre", "m", "h"):
        return "male"
    if g in ("female", "mujer", "f"):
        return "female"
    return None


_QUALITIES = ("pro", "standard")


def _norm_quality(q):
    """Normaliza la calidad del motor a 'pro'/'standard'. Cualquier otra cosa
    (vacío, valor viejo, basura) cae en 'standard': el modelo liviano es el
    default seguro para la plata, y subir a pro es una decisión explícita."""
    q = (q or "").strip().lower()
    return q if q in _QUALITIES else "standard"


_RANGE_KEYS = ("likes", "views", "shares", "reposts", "saves", "reach")

# Espaciado por defecto del dripfeed: 2 horas entre tandas. Con 3 partes el post
# recibe engagement durante 4 horas; con 5, durante 8.
_DRIP_CADA_DEFAULT = 120


def _norm_ranges(r):
    """Normaliza el dict de rangos min-max por producto. Descarta lo inválido.
    Devuelve {'likes':{'min':int,'max':int,'prod_id':str,'prod_nombre':str}, ...}
    o None si no queda nada.

    prod_id/prod_nombre son la CALIDAD elegida: cuando el CRM ofrece varias
    variantes del mismo tipo (ej "Likes" vs "Likes 1178 JAP"), el admin fija cuál
    usar para este cliente. Vacío = la herramienta elige la base como antes."""
    if not r or not isinstance(r, dict):
        return None
    out = {}
    for k in _RANGE_KEYS:
        entradas = [e for e in _as_list(r.get(k)) if isinstance(e, dict)]
        limpias = [x for x in (_norm_range_entry(e) for e in entradas) if x]
        if limpias:
            out[k] = limpias
    com = _norm_comentarios(r.get("comentarios"))
    if com:
        out["comentarios"] = com
    return out or None


# Cuántos comentarios manda el cliente por post, por tipo. Vive dentro de
# `ranges` pero NO es un _RANGE_KEYS: los comentarios no generan una orden
# precreada (salen de los que el vendedor elige en la lista), acá solo se fija
# cuántos de cada tipo hay que seleccionar.
_COMENTARIO_KEYS = ("verificados", "comunes")

# Los comunes se parten en dos tandas de 40 (mañana/tarde), así que 80 por día
# es el techo real: dejar configurar más sería prometer algo que no se manda.
_COMENTARIOS_TOPE = {"comunes": 80}


def _norm_comentarios(c):
    """Normaliza {'verificados': {'min':int,'max':int}, 'comunes': {...}}.
    Rango min-max: la herramienta saca un número al azar adentro, para que el
    cliente no reciba siempre la misma cantidad. None si no queda nada."""
    if not isinstance(c, dict):
        return None
    out = {}
    for k in _COMENTARIO_KEYS:
        e = c.get(k)
        if not isinstance(e, dict):
            continue
        try:
            mn = int(e.get("min"))
            mx = int(e.get("max"))
        except (TypeError, ValueError):
            continue
        if mn < 0 or mx <= 0:
            continue
        if mx < mn:
            mn, mx = mx, mn
        tope = _COMENTARIOS_TOPE.get(k)
        if tope:
            mn, mx = min(mn, tope), min(mx, tope)
        out[k] = {"min": mn, "max": mx}
    return out or None


def _as_list(v):
    """Un tipo puede tener VARIAS entradas (mismo post, dos calidades de likes
    con rangos distintos). Las fichas viejas guardaron un solo dict: se leen
    igual, envueltas en lista."""
    if isinstance(v, list):
        return v
    return [v] if isinstance(v, dict) else []


def _norm_range_entry(v):
    try:
        mn = int(v.get("min"))
        mx = int(v.get("max"))
    except (TypeError, ValueError):
        return None
    if mn < 0 or mx < 0 or mx == 0:
        return None
    if mx < mn:
        mn, mx = mx, mn
    entry = {"min": mn, "max": mx}
    prod_id = str(v.get("prod_id") or "").strip()
    if prod_id:
        entry["prod_id"] = prod_id
        entry["prod_nombre"] = str(v.get("prod_nombre") or "").strip()
    # Dripfeed: en vez de una orden de 3.000 de una (que deja el post con cara de
    # comprado), se parte en N tandas espaciadas. split=1 es "todo junto".
    try:
        split = int(v.get("split") or 1)
    except (TypeError, ValueError):
        split = 1
    if split in (3, 5):
        entry["split"] = split
        try:
            cada = int(v.get("cada_min"))
        except (TypeError, ValueError):
            cada = _DRIP_CADA_DEFAULT
        # Entre 5 minutos y 24 horas: menos no es dripfeed y más se va de día.
        entry["cada_min"] = min(1440, max(5, cada))
    return entry


def _client_to_dict(c: Client) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "ig_username": c.ig_username,
        "display_name": c.display_name,
        "prompt": c.prompt,
        "status": c.status,
        "gender": c.gender,
        "quality": _norm_quality(c.quality),
        "keyword_mode": bool(c.keyword_mode),
        "ranges": c.ranges or {},
        "crm_idventa": c.crm_idventa or "",
        "crm_idvendedor": c.crm_idvendedor or "",
    }


def get_client_by_ig_username(ig_username: str, account_id: Optional[int] = None) -> Optional[dict]:
    """Busca un cliente activo por su @usuario de IG. Si account_id se pasa, acota
    a esa cuenta; si no, toma el primer match (etapa 1 = una sola cuenta).
    Devuelve None si no hay DB o no existe."""
    if not db_available() or not ig_username:
        return None
    key = ig_username.strip().lower()
    with session_scope() as s:
        q = s.query(Client).filter(
            Client.ig_username == key,
            Client.status == "active",
        )
        if account_id is not None:
            q = q.filter(Client.account_id == account_id)
        c = q.first()
        return _client_to_dict(c) if c else None


def _get_system_client(ig: str) -> Optional[dict]:
    """Un cliente reservado del sistema. No filtra por cuenta ni por estado: es
    UNO SOLO global, sin importar de qué vendedor sea el post."""
    if not db_available():
        return None
    with session_scope() as s:
        c = (s.query(Client)
             .filter(Client.ig_username == ig)
             .order_by(Client.id)
             .first())
        return _client_to_dict(c) if c else None


def _get_system_prompt(ig: str) -> Optional[str]:
    """Prompt de un reservado. None si no hay DB o está vacío (el llamador cae
    al .txt de la imagen)."""
    c = _get_system_client(ig)
    if c and c["prompt"] and c["prompt"].strip():
        return c["prompt"]
    return None


def get_generic_client() -> Optional[dict]:
    return _get_system_client(GENERIC_IG)


def get_generic_prompt() -> Optional[str]:
    return _get_system_prompt(GENERIC_IG)


def get_keyword_client() -> Optional[dict]:
    return _get_system_client(KEYWORD_IG)


def get_keyword_prompt() -> Optional[str]:
    """Prompt maestro del modo keyword. Cae a prompts/keyword.txt si no hay DB."""
    return _get_system_prompt(KEYWORD_IG)


def get_client_display_name(ig_username: str, account_id: Optional[int] = None) -> Optional[str]:
    """Equivalente en DB a clients_map.json (owner_username -> nombre)."""
    c = get_client_by_ig_username(ig_username, account_id)
    return c["display_name"] if c else None


def get_client_prompt(ig_username: str, account_id: Optional[int] = None) -> Optional[str]:
    """Prompt del cliente desde DB. None si no hay DB o el cliente no existe/está
    sin prompt (el llamador cae al .txt de archivo)."""
    c = get_client_by_ig_username(ig_username, account_id)
    if c and c["prompt"] and c["prompt"].strip():
        return c["prompt"]
    return None


# ── CRUD de clientes (admin self-serve, TAREA 3) ────────────────────────────────

class RepoError(Exception):
    """Error de validación de negocio (ej: @usuario duplicado). El llamador lo
    traduce a un 400 con mensaje claro."""


def list_clients(account_id: int) -> list[dict]:
    if not db_available():
        return []
    with session_scope() as s:
        # Los reservados son del sistema: nunca salen en la lista de clientes (el
        # panel del admin los agrega aparte, marcados como reservados).
        cs = (s.query(Client)
              .filter(Client.account_id == account_id,
                      Client.ig_username.notin_(SYSTEM_IGS))
              .order_by(Client.display_name, Client.ig_username)
              .all())
        return [_client_to_dict(c) for c in cs]


def _norm_ig(ig_username: str) -> str:
    return (ig_username or "").strip().lstrip("@").lower()


def create_client(account_id: int, ig_username: str, display_name: str, prompt: str,
                  status: str = "active", gender=None, quality=None, ranges=None,
                  crm_idventa=None, crm_idvendedor=None, keyword_mode=False) -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    key = _norm_ig(ig_username)
    if not key:
        raise RepoError("El @usuario es obligatorio")
    if key in SYSTEM_IGS:
        raise RepoError(f"@{key} es un cliente reservado del sistema")
    with session_scope() as s:
        dup = s.query(Client).filter(
            Client.account_id == account_id, Client.ig_username == key
        ).first()
        if dup:
            raise RepoError(f"Ya existe un cliente con @{key} en esta cuenta")
        c = Client(
            account_id=account_id,
            ig_username=key,
            display_name=(display_name or "").strip() or key,
            prompt=prompt or "",
            status=status if status in ("active", "paused") else "active",
            gender=_norm_gender(gender),
            quality=_norm_quality(quality),
            ranges=_norm_ranges(ranges),
            crm_idventa=(crm_idventa or "").strip() or None,
            crm_idvendedor=(crm_idvendedor or "").strip() or None,
            keyword_mode=bool(keyword_mode),
        )
        s.add(c)
        s.flush()
        return _client_to_dict(c)


def update_client(account_id: int, client_id: int, *, display_name=None,
                  prompt=None, status=None, ig_username=None, gender=None,
                  gender_set=False, quality=None, quality_set=False,
                  ranges=None, ranges_set=False,
                  crm_idventa=None, crm_idvendedor=None,
                  keyword_mode=None, keyword_mode_set=False) -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    with session_scope() as s:
        c = s.query(Client).filter(
            Client.account_id == account_id, Client.id == client_id
        ).first()
        if not c:
            raise RepoError("Cliente no encontrado")
        if c.ig_username in SYSTEM_IGS:
            raise RepoError("Es un cliente del sistema y se edita desde su "
                            "propia ficha (solo el admin)")
        if ig_username is not None:
            key = _norm_ig(ig_username)
            if not key:
                raise RepoError("El @usuario no puede quedar vacío")
            if key in SYSTEM_IGS:
                raise RepoError(f"@{key} es un cliente reservado del sistema")
            if key != c.ig_username:
                dup = s.query(Client).filter(
                    Client.account_id == account_id, Client.ig_username == key,
                    Client.id != client_id
                ).first()
                if dup:
                    raise RepoError(f"Ya existe un cliente con @{key}")
                c.ig_username = key
        if display_name is not None:
            c.display_name = display_name.strip()
        if prompt is not None:
            c.prompt = prompt
        if status is not None:
            if status not in ("active", "paused"):
                raise RepoError("Estado inválido")
            c.status = status
        if gender_set:
            c.gender = _norm_gender(gender)
        if quality_set:
            c.quality = _norm_quality(quality)
        if ranges_set:
            c.ranges = _norm_ranges(ranges)
        if keyword_mode_set:
            c.keyword_mode = bool(keyword_mode)
        # Vaciar el campo = desasignar la venta (vuelve al fallback de la cuenta).
        if crm_idventa is not None:
            c.crm_idventa = crm_idventa.strip() or None
        if crm_idvendedor is not None:
            c.crm_idvendedor = crm_idvendedor.strip() or None
        s.flush()
        return _client_to_dict(c)


def delete_client(account_id: int, client_id: int) -> bool:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    with session_scope() as s:
        c = s.query(Client).filter(
            Client.account_id == account_id, Client.id == client_id
        ).first()
        if not c:
            return False
        if c.ig_username in SYSTEM_IGS:
            raise RepoError("Es un cliente del sistema y no se puede borrar")
        s.delete(c)
        return True


def update_system_client(ig: str, *, prompt=None, gender=None, gender_set=False,
                         quality=None, quality_set=False,
                         ranges=None, ranges_set=False) -> dict:
    """Edición de un cliente reservado (solo admin). Deliberadamente NO deja
    tocar @usuario, nombre ni estado: siempre activo y siempre el mismo, porque
    es del sistema y lo comparten todos los vendedores."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    with session_scope() as s:
        c = (s.query(Client)
             .filter(Client.ig_username == ig)
             .order_by(Client.id)
             .first())
        if not c:
            raise RepoError(f"El cliente reservado @{ig} no existe todavía "
                            "(falta correr las migraciones)")
        if prompt is not None:
            c.prompt = prompt
        if gender_set:
            c.gender = _norm_gender(gender)
        if quality_set:
            c.quality = _norm_quality(quality)
        if ranges_set:
            c.ranges = _norm_ranges(ranges)
        c.status = "active"
        s.flush()
        return _client_to_dict(c)


def update_generic_client(**kw) -> dict:
    return update_system_client(GENERIC_IG, **kw)


def update_keyword_client(**kw) -> dict:
    return update_system_client(KEYWORD_IG, **kw)


# ── Pedidos de ajuste de prompt (bandeja del admin) ──────────────────────────
#
# El vendedor describe en castellano qué quiere cambiar; el admin resuelve la
# cola desde /admin con el asistente de IA. Deliberadamente NO hay IA de este
# lado: el pedido es texto plano y no cuesta tokens.

_PR_STATUS = ("pending", "done", "discarded")


def _prompt_request_to_dict(p: PromptRequest) -> dict:
    return {
        "id": p.id,
        "account_id": p.account_id,
        "client_id": p.client_id,
        "client_ig_username": p.client_ig_username,
        "user_id": p.user_id,
        "username": p.username,
        "text": p.text,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        "resolved_by": p.resolved_by,
    }


def create_prompt_request(account_id: int, client_id: int, *, user_id=None,
                          username: str = "", text: str = "") -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    text = (text or "").strip()
    if not text:
        raise RepoError("Escribí qué querés cambiar del prompt")
    if len(text) > 4000:
        raise RepoError("El pedido es demasiado largo (máximo 4000 caracteres)")
    with session_scope() as s:
        # El cliente tiene que ser de la cuenta que pide: si no, un vendedor
        # podría abrir pedidos sobre clientes ajenos mandando un id cualquiera.
        c = s.query(Client).filter(
            Client.account_id == account_id, Client.id == client_id
        ).first()
        if not c:
            raise RepoError("Cliente no encontrado")
        p = PromptRequest(
            account_id=account_id,
            client_id=c.id,
            client_ig_username=c.ig_username,
            user_id=user_id,
            username=(username or "").strip(),
            text=text,
            status="pending",
        )
        s.add(p)
        s.flush()
        return _prompt_request_to_dict(p)


def list_prompt_requests(account_id=None, status=None) -> list[dict]:
    """Sin account_id devuelve los de TODAS las cuentas (vista del admin)."""
    if not db_available():
        return []
    with session_scope() as s:
        q = s.query(PromptRequest)
        if account_id is not None:
            q = q.filter(PromptRequest.account_id == account_id)
        if status:
            q = q.filter(PromptRequest.status == status)
        ps = q.order_by(PromptRequest.created_at.desc()).limit(300).all()
        out = []
        # El nombre de la cuenta ahorra el "¿de quién era este pedido?" en el panel.
        nombres = {a.id: a.name for a in s.query(Account).all()}
        for p in ps:
            d = _prompt_request_to_dict(p)
            d["account_name"] = nombres.get(p.account_id, "")
            out.append(d)
        return out


def set_prompt_request_status(request_id: int, status: str, *, resolved_by: str = "") -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    if status not in _PR_STATUS:
        raise RepoError("Estado inválido")
    with session_scope() as s:
        p = s.query(PromptRequest).filter(PromptRequest.id == request_id).first()
        if not p:
            raise RepoError("Pedido no encontrado")
        p.status = status
        if status == "pending":
            p.resolved_at, p.resolved_by = None, None
        else:
            from common.models import _utcnow
            p.resolved_at = _utcnow()
            p.resolved_by = (resolved_by or "").strip() or None
        s.flush()
        return _prompt_request_to_dict(p)


# ── CRUD de vendedores (admin self-serve, TAREA 3) ──────────────────────────────

def list_users(account_id: int) -> list[dict]:
    if not db_available():
        return []
    with session_scope() as s:
        us = (s.query(User)
              .filter(User.account_id == account_id)
              .order_by(User.role.desc(), User.username)
              .all())
        return [_user_to_dict(u) for u in us]


def create_user(account_id: int, username: str, password: str, role: str = "vendedor") -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    from werkzeug.security import generate_password_hash
    uname = (username or "").strip()
    if not uname:
        raise RepoError("El usuario es obligatorio")
    if not password or len(password) < 4:
        raise RepoError("La contraseña debe tener al menos 4 caracteres")
    if role not in ("admin", "vendedor"):
        raise RepoError("Rol inválido")
    with session_scope() as s:
        dup = s.query(User).filter(User.username == uname).first()
        if dup:
            raise RepoError(f"El usuario '{uname}' ya existe")
        u = User(
            account_id=account_id,
            username=uname,
            password_hash=generate_password_hash(password),
            role=role,
            active=True,
        )
        s.add(u)
        s.flush()
        return _user_to_dict(u)


def update_user(account_id: int, user_id: int, *, active=None, role=None,
                password=None) -> dict:
    if not db_available():
        raise RepoError("Base de datos no disponible")
    from werkzeug.security import generate_password_hash
    with session_scope() as s:
        u = s.query(User).filter(
            User.account_id == account_id, User.id == user_id
        ).first()
        if not u:
            raise RepoError("Vendedor no encontrado")
        # No permitir desactivar/degradar al último admin activo de la cuenta.
        if (active is False or role == "vendedor") and u.role == "admin":
            otros_admin = s.query(User).filter(
                User.account_id == account_id, User.role == "admin",
                User.active.is_(True), User.id != user_id
            ).count()
            if otros_admin == 0:
                raise RepoError("No podés dejar la cuenta sin ningún admin activo")
        if active is not None:
            u.active = bool(active)
        if role is not None:
            if role not in ("admin", "vendedor"):
                raise RepoError("Rol inválido")
            u.role = role
        if password is not None:
            if len(password) < 4:
                raise RepoError("La contraseña debe tener al menos 4 caracteres")
            u.password_hash = generate_password_hash(password)
        s.flush()
        return _user_to_dict(u)


# ── Usuarios / auth ─────────────────────────────────────────────────────────────

def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "account_id": u.account_id,
        "username": u.username,
        "role": u.role,
        "active": u.active,
        "is_admin": u.role == "admin",
    }


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Devuelve el dict del usuario si las credenciales son válidas y está activo.
    None en cualquier otro caso (incluida DB no disponible)."""
    if not db_available() or not username:
        return None
    with session_scope() as s:
        u = s.query(User).filter(User.username == username, User.active.is_(True)).first()
        if u and check_password_hash(u.password_hash, password):
            return _user_to_dict(u)
    return None


def get_user(user_id: int) -> Optional[dict]:
    if not db_available():
        return None
    with session_scope() as s:
        u = s.query(User).filter(User.id == user_id).first()
        return _user_to_dict(u) if u else None


# ── Cuentas / config CRM ────────────────────────────────────────────────────────

# ── Registro de uso ─────────────────────────────────────────────────────────────

def log_usage(account_id: int, user_id: Optional[int], action: str,
              client_ig_username: Optional[str] = None, post_url: Optional[str] = None,
              qty: Optional[int] = None, product_type: Optional[str] = None) -> None:
    """Registra una acción de un vendedor. No-op silencioso si no hay DB (el
    registro de uso nunca debe romper la acción principal)."""
    if not db_available() or not account_id:
        return
    try:
        with session_scope() as s:
            s.add(UsageEvent(
                account_id=account_id,
                user_id=user_id,
                action=action,
                client_ig_username=(client_ig_username or None),
                post_url=(post_url or None),
                qty=qty,
                product_type=(product_type or None),
            ))
    except Exception as e:
        print(f"[usage] no pude registrar uso ({e})", flush=True)


def used_quantities(account_id: int, client_ig_username: str, product_type: str) -> list[int]:
    """Cantidades ya enviadas para ese cliente y tipo de producto. La tirada
    automática las evita para no repetir nunca el mismo número."""
    if not db_available() or not account_id or not client_ig_username or not product_type:
        return []
    try:
        with session_scope() as s:
            rows = (s.query(UsageEvent.qty)
                    .filter(UsageEvent.account_id == account_id,
                            UsageEvent.client_ig_username == client_ig_username.strip().lower(),
                            UsageEvent.product_type == product_type,
                            UsageEvent.qty.isnot(None))
                    .all())
            return [r[0] for r in rows]
    except Exception as e:
        print(f"[usage] no pude leer cantidades usadas ({e})", flush=True)
        return []


def usage_counts_by_user(account_id: int) -> list[dict]:
    """Conteo de acciones por vendedor dentro de una cuenta, para el contador del
    admin. Devuelve [{user_id, username, role, active, generar, publicar,
    enviar_trafico, total}], ordenado por total desc."""
    if not db_available():
        return []
    from sqlalchemy import func
    with session_scope() as s:
        # base: todos los usuarios de la cuenta (aunque tengan 0 acciones)
        users = s.query(User).filter(User.account_id == account_id).all()
        rows = {
            u.id: {"user_id": u.id, "username": u.username, "role": u.role,
                   "active": u.active, "generar": 0, "publicar": 0,
                   "enviar_trafico": 0, "total": 0}
            for u in users
        }
        counts = (
            s.query(UsageEvent.user_id, UsageEvent.action, func.count().label("n"))
            .filter(UsageEvent.account_id == account_id)
            .group_by(UsageEvent.user_id, UsageEvent.action)
            .all()
        )
        for user_id, action, n in counts:
            row = rows.get(user_id)
            if row is None:
                continue
            if action in row:
                row[action] += n
            row["total"] += n
        return sorted(rows.values(), key=lambda r: r["total"], reverse=True)


def get_account_crm_config(account_id: int) -> Optional[dict]:
    """Credenciales del CRM de una cuenta, con la password ya descifrada. None si
    no hay DB o la cuenta no existe. El llamador cae al .env global si es None."""
    if not db_available():
        return None
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id, Account.active.is_(True)).first()
        if not a:
            return None
        return {
            "account_id": a.id,
            "crm_url": a.crm_url,
            "crm_email": a.crm_email,
            "crm_password": crypto.decrypt(a.crm_password_enc) if a.crm_password_enc else "",
            "crm_idvendedor": a.crm_idvendedor,
            "crm_idventa": a.crm_idventa,
            "crm_proxy": a.crm_proxy,
            "crm_disponible": a.crm_disponible,
        }


# ── Vendedores = cuentas (login por credenciales de Growi) ───────────────────────
#
# En el modelo multi-tenant cada VENDEDOR es una `Account`: tiene sus propias
# credenciales del CRM Growi (con las que se loguea también en nuestra página) y
# sus propios clientes. El admin es un super-usuario (login local) que ve y
# gestiona todos los vendedores.

def _account_to_dict(a: Account, *, with_secrets: bool = False) -> dict:
    d = {
        "id": a.id,
        "name": a.name,
        "slug": a.slug,
        "active": a.active,
        "status": a.status or "approved",
        "crm_url": a.crm_url,
        "crm_email": a.crm_email,
        "crm_idvendedor": a.crm_idvendedor,
        "crm_idventa": a.crm_idventa,
        "crm_proxy": a.crm_proxy,
        "crm_disponible": a.crm_disponible,
        "has_password": bool(a.crm_password_enc),
    }
    if with_secrets:
        d["crm_password"] = crypto.decrypt(a.crm_password_enc) if a.crm_password_enc else ""
    return d


def _slugify(text: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in (text or "").strip().lower())
    base = "-".join(p for p in base.split("-") if p)
    return base or "vendedor"


def get_account_by_crm_email(email: str) -> Optional[dict]:
    """Busca una cuenta por su email de Growi (case-insensitive), sin filtrar por
    estado: el login necesita distinguir "no existe" de "pendiente/restringida"
    para darle al vendedor el mensaje correcto. Devuelve el dict con la password
    descifrada para poder validar el login en vivo. None si no hay DB o no existe."""
    if not db_available() or not email:
        return None
    key = email.strip().lower()
    with session_scope() as s:
        from sqlalchemy import func
        a = (s.query(Account)
             .filter(func.lower(Account.crm_email) == key)
             .first())
        if not a:
            return None
        return _account_to_dict(a, with_secrets=True)


def update_account_crm_password(account_id: int, plaintext: str) -> None:
    """Guarda cifrada la password del CRM que el vendedor acaba de tipear al
    loguearse, para que las operaciones de fondo puedan reloguear sin pedirla."""
    if not db_available() or not plaintext:
        return
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        if a:
            a.crm_password_enc = crypto.encrypt(plaintext)


def list_vendedores() -> list[dict]:
    """Todas las cuentas-vendedor (sin secretos). Para el panel del admin."""
    if not db_available():
        return []
    with session_scope() as s:
        accs = s.query(Account).order_by(Account.active.desc(), Account.name).all()
        return [_account_to_dict(a) for a in accs]


def get_vendedor(account_id: int) -> Optional[dict]:
    if not db_available():
        return None
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        return _account_to_dict(a) if a else None


def _unique_slug(s, base: str) -> str:
    slug = base
    n = 2
    while s.query(Account).filter(Account.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_vendedor(*, name: str, crm_email: str, crm_password: str, crm_url: str = "",
                    crm_idvendedor: str = "", crm_idventa: str = "", crm_proxy: str = "",
                    crm_disponible: str = "") -> dict:
    """Da de alta un vendedor (Account) con sus credenciales de Growi. El admin lo
    usa para pre-registrar a cada vendedor antes de que entre con su login."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    name = (name or "").strip()
    email = (crm_email or "").strip().lower()
    if not name:
        raise RepoError("El nombre del vendedor es obligatorio")
    if not email:
        raise RepoError("El email de Growi es obligatorio")
    if not crm_password:
        raise RepoError("La contraseña de Growi es obligatoria")
    with session_scope() as s:
        from sqlalchemy import func
        dup = s.query(Account).filter(func.lower(Account.crm_email) == email).first()
        if dup:
            raise RepoError(f"Ya existe un vendedor con el email {email}")
        a = Account(
            name=name,
            slug=_unique_slug(s, _slugify(name)),
            active=True,
            status="approved",   # alta manual del admin: nace habilitado
            crm_url=(crm_url or "").strip() or "https://crm.growiagency.com",
            crm_email=email,
            crm_password_enc=crypto.encrypt(crm_password),
            crm_idvendedor=(crm_idvendedor or "").strip() or None,
            crm_idventa=(crm_idventa or "").strip() or None,
            crm_proxy=(crm_proxy or "").strip() or None,
            crm_disponible=(str(crm_disponible).strip() or None),
        )
        s.add(a)
        s.flush()
        return _account_to_dict(a)


def create_pending_vendedor(*, crm_email: str, crm_password: str, crm_url: str = "",
                            crm_proxy: str = "") -> dict:
    """Autoregistro: alguien se logueó con credenciales de Growi VÁLIDAS pero no
    tiene cuenta todavía. Se crea en estado "pending" e inactiva; no puede operar
    hasta que el admin la apruebe. El nombre provisorio sale del email."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    email = (crm_email or "").strip().lower()
    if not email:
        raise RepoError("El email de Growi es obligatorio")
    name = email.split("@")[0] or email
    with session_scope() as s:
        from sqlalchemy import func
        dup = s.query(Account).filter(func.lower(Account.crm_email) == email).first()
        if dup:
            return _account_to_dict(dup)
        a = Account(
            name=name,
            slug=_unique_slug(s, _slugify(name)),
            active=False,
            status="pending",
            crm_url=(crm_url or "").strip() or "https://crm.growiagency.com",
            crm_email=email,
            crm_password_enc=crypto.encrypt(crm_password) if crm_password else None,
            crm_proxy=(crm_proxy or "").strip() or None,
        )
        s.add(a)
        s.flush()
        return _account_to_dict(a)


def set_vendedor_status(account_id: int, status: str) -> dict:
    """Resolución del admin sobre una solicitud: "approved" habilita la cuenta,
    "rejected" le niega el acceso. `active` acompaña al estado para que el resto
    del sistema (que mira active) no necesite cambios."""
    if status not in ("pending", "approved", "rejected"):
        raise RepoError(f"Estado inválido: {status}")
    if not db_available():
        raise RepoError("Base de datos no disponible")
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        if not a:
            raise RepoError("Vendedor no encontrado")
        a.status = status
        a.active = (status == "approved")
        s.flush()
        return _account_to_dict(a)


def update_vendedor(account_id: int, *, name=None, active=None, crm_email=None,
                    crm_password=None, crm_url=None, crm_idvendedor=None,
                    crm_idventa=None, crm_proxy=None, crm_disponible=None) -> dict:
    """Edita los datos/credenciales de un vendedor. Campos None se dejan igual.
    crm_password vacío/None no toca la password guardada."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    with session_scope() as s:
        from sqlalchemy import func
        a = s.query(Account).filter(Account.id == account_id).first()
        if not a:
            raise RepoError("Vendedor no encontrado")
        if name is not None:
            nm = name.strip()
            if not nm:
                raise RepoError("El nombre no puede quedar vacío")
            a.name = nm
        if crm_email is not None:
            email = crm_email.strip().lower()
            if not email:
                raise RepoError("El email de Growi no puede quedar vacío")
            dup = (s.query(Account)
                   .filter(func.lower(Account.crm_email) == email, Account.id != account_id)
                   .first())
            if dup:
                raise RepoError(f"Ya existe un vendedor con el email {email}")
            a.crm_email = email
        if crm_password:
            a.crm_password_enc = crypto.encrypt(crm_password)
        if crm_url is not None:
            a.crm_url = crm_url.strip() or a.crm_url
        if crm_idvendedor is not None:
            a.crm_idvendedor = crm_idvendedor.strip() or None
        if crm_idventa is not None:
            a.crm_idventa = crm_idventa.strip() or None
        if crm_proxy is not None:
            a.crm_proxy = crm_proxy.strip() or None
        if crm_disponible is not None:
            a.crm_disponible = str(crm_disponible).strip() or None
        if active is not None:
            a.active = bool(active)
            # Activar/desactivar a mano también resuelve la solicitud, así no
            # queda una cuenta activa pero en estado "pendiente".
            a.status = "approved" if a.active else "rejected"
        s.flush()
        return _account_to_dict(a)


def usage_counts_all_accounts() -> list[dict]:
    """Conteo de acciones por vendedor (cuenta), para el contador cross-cuenta del
    admin. Devuelve [{account_id, name, crm_email, active, generar, publicar,
    enviar_trafico, total}], ordenado por total desc."""
    if not db_available():
        return []
    from sqlalchemy import func
    with session_scope() as s:
        accs = s.query(Account).all()
        rows = {
            a.id: {"account_id": a.id, "name": a.name, "crm_email": a.crm_email,
                   "active": a.active, "generar": 0, "publicar": 0,
                   "enviar_trafico": 0, "total": 0}
            for a in accs
        }
        counts = (
            s.query(UsageEvent.account_id, UsageEvent.action, func.count().label("n"))
            .group_by(UsageEvent.account_id, UsageEvent.action)
            .all()
        )
        for account_id, action, n in counts:
            row = rows.get(account_id)
            if row is None:
                continue
            if action in row:
                row[action] += n
            row["total"] += n
        return sorted(rows.values(), key=lambda r: r["total"], reverse=True)
