"""
Acceso de alto nivel a la DB. Todas las funciones de lectura devuelven None (o
lista vacía) cuando la DB no está disponible, para que el llamador caiga al
fallback de archivos sin romperse.

Devuelven dicts desacoplados de la sesión (no objetos ORM vivos) para evitar
problemas de lazy-loading fuera del `session_scope`.
"""
import os
import re
from typing import Optional

from werkzeug.security import check_password_hash

from common.db import db_available, session_scope
from common.models import (Account, User, Client, PromptRequest, UsageEvent,
                           PendingOrder, PostCache, TokenUsage, GrowiCall)


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
        "prompt_standalone": bool(c.prompt_standalone),
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


def get_client_prompt_layer(ig_username: str, account_id: Optional[int] = None) -> tuple[Optional[str], bool]:
    """Prompt del cliente + si va SOLO (sin la capa genérica arriba).

    Las dos cosas salen de la MISMA fila, así que van juntas: ai_generator las
    necesita para armar el template y pedirlas por separado eran dos consultas
    por tanda. El bool es False si no hay DB o el cliente no existe: el default
    seguro es el armado por capas de siempre."""
    c = get_client_by_ig_username(ig_username, account_id)
    if not c:
        return None, False
    prompt = c["prompt"] if (c["prompt"] and c["prompt"].strip()) else None
    return prompt, bool(c["prompt_standalone"])


# ── CRUD de clientes (admin self-serve, TAREA 3) ────────────────────────────────

class RepoError(Exception):
    """Error de validación de negocio (ej: @usuario duplicado). El llamador lo
    traduce a un 400 con mensaje claro."""


def list_clients(account_id: Optional[int] = None) -> list[dict]:
    """Clientes de una cuenta. account_id=None trae los de TODAS las cuentas: lo
    usa el admin, que necesita ver/probar los clientes de cualquier vendedor."""
    if not db_available():
        return []
    with session_scope() as s:
        # Los reservados son del sistema: nunca salen en la lista de clientes (el
        # panel del admin los agrega aparte, marcados como reservados).
        q = s.query(Client).filter(Client.ig_username.notin_(SYSTEM_IGS))
        if account_id is not None:
            q = q.filter(Client.account_id == account_id)
        cs = q.order_by(Client.display_name, Client.ig_username).all()
        return [_client_to_dict(c) for c in cs]


def _norm_ig(ig_username: str) -> str:
    return (ig_username or "").strip().lstrip("@").lower()


def create_client(account_id: int, ig_username: str, display_name: str, prompt: str,
                  status: str = "active", gender=None, quality=None, ranges=None,
                  crm_idventa=None, crm_idvendedor=None, keyword_mode=False,
                  prompt_standalone=None) -> dict:
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
            # None = no vino en el alta ⇒ el default de fábrica (prendido). Un
            # False explícito del panel sí se respeta: es alguien que lo apagó.
            prompt_standalone=True if prompt_standalone is None else bool(prompt_standalone),
        )
        s.add(c)
        s.flush()
        return _client_to_dict(c)


def update_client(account_id: int, client_id: int, *, display_name=None,
                  prompt=None, status=None, ig_username=None, gender=None,
                  gender_set=False, quality=None, quality_set=False,
                  ranges=None, ranges_set=False,
                  crm_idventa=None, crm_idvendedor=None,
                  keyword_mode=None, keyword_mode_set=False,
                  prompt_standalone=None, prompt_standalone_set=False) -> dict:
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
        if prompt_standalone_set:
            c.prompt_standalone = bool(prompt_standalone)
        # Vaciar el campo = desasignar la venta (vuelve al fallback de la cuenta).
        if crm_idventa is not None:
            c.crm_idventa = crm_idventa.strip() or None
        if crm_idvendedor is not None:
            c.crm_idvendedor = crm_idvendedor.strip() or None
        s.flush()
        return _client_to_dict(c)


def fijar_venta_de_cliente(account_id: int, ig_username: str, idventa: str,
                           idvendedor: str = None) -> bool:
    """Deja preseteada en la ficha del cliente la campaña con la que SÍ entró una
    orden. Devuelve True si cambió algo.

    Existe porque la campaña asignada a mano se vence: el cliente queda pegado a
    una venta sin saldo (o que el CRM ya no lista) y cada envío rebota hasta que
    alguien se acuerda de entrar a Mis clientes a cambiarla. Cuando el vendedor
    elige otra campaña en el envío y esa orden entra, la elección se guarda y el
    próximo envío ya sale bien solo.

    Busca por @usuario, no por id: el envío conoce el perfil de IG del post, no
    la fila. No toca a los reservados del sistema (el genérico se aplica a los
    posts de todos los vendedores; fijarle una venta se la impondría a todos).
    """
    if not db_available():
        return False
    key = _norm_ig(ig_username)
    idventa = (idventa or "").strip()
    if not key or not idventa or key in SYSTEM_IGS or account_id is None:
        return False
    with session_scope() as s:
        c = s.query(Client).filter(
            Client.account_id == account_id,
            Client.ig_username == key,
            Client.status == "active",
        ).first()
        if not c:
            return False
        cambio = False
        if (c.crm_idventa or "") != idventa:
            c.crm_idventa = idventa
            cambio = True
        # El idvendedor solo se completa si falta: es el mismo número para toda
        # la cuenta y pisarlo con el de una campaña suelta no aporta nada.
        idvendedor = (idvendedor or "").strip()
        if idvendedor and not (c.crm_idvendedor or "").strip():
            c.crm_idvendedor = idvendedor
            cambio = True
        return cambio


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
    """Datos del CRM de una cuenta. None si no hay DB o la cuenta no existe. El
    llamador cae al .env global si es None.

    `crm_password` viene SIEMPRE vacía: la contraseña de Growi ya no se guarda.
    La pone el webService desde su almacén en memoria, con la que el vendedor
    tipeó al entrar. La clave se mantiene en el dict para no romper a quien lo
    consuma esperando la forma completa.
    """
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
            "crm_password": "",
            "crm_idvendedor": a.crm_idvendedor,
            "crm_idventa": a.crm_idventa,
            "crm_proxy": a.crm_proxy,
            "crm_disponible": a.crm_disponible,
        "wa_phone": a.wa_phone or "",
        }


# ── Vendedores = cuentas (login por credenciales de Growi) ───────────────────────
#
# En el modelo multi-tenant cada VENDEDOR es una `Account`: tiene sus propias
# credenciales del CRM Growi (con las que se loguea también en nuestra página) y
# sus propios clientes. El admin es un super-usuario (login local) que ve y
# gestiona todos los vendedores.

def _account_to_dict(a: Account) -> dict:
    """Los datos de una cuenta. Ya no hay variante "con secretos": la contraseña
    del CRM no se guarda, así que no hay nada que revelar."""
    return {
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
    }


def _slugify(text: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in (text or "").strip().lower())
    base = "-".join(p for p in base.split("-") if p)
    return base or "vendedor"


def get_account_by_crm_email(email: str) -> Optional[dict]:
    """Busca una cuenta por su email de Growi (case-insensitive), sin filtrar por
    estado: el login necesita distinguir "no existe" de "pendiente/restringida"
    para darle al vendedor el mensaje correcto. None si no hay DB o no existe.

    No devuelve contraseña porque no hace falta: el login valida contra el CRM la
    que el vendedor acaba de tipear, no una guardada."""
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
        return _account_to_dict(a)


def guardar_idvendedor(account_id: int, idvendedor: str) -> bool:
    """Guarda el ID de vendedor que el CRM le reconoce a esta cuenta.

    Se llama al loguearse: el CRM lo dice en el listado de campañas, y tenerlo en
    la base evita depender de que ese parseo funcione justo cuando el vendedor
    manda una orden. Si no lo tuviéramos, una cuenta sin campañas visibles no
    podría cargar nada a su nombre.

    Devuelve True si cambió algo (para poder loguearlo sin ensuciar cada login).
    """
    idvendedor = (idvendedor or "").strip()
    if not db_available() or not account_id or not idvendedor:
        return False
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        if not a or (a.crm_idvendedor or "") == idvendedor:
            return False
        anterior = a.crm_idvendedor
        a.crm_idvendedor = idvendedor
        print(f"[cuenta {account_id}] idvendedor del CRM: "
              f"{anterior or '(vacío)'} → {idvendedor}", flush=True)
        return True


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


def _norm_wa_phone(telefono) -> str:
    """Normaliza el WhatsApp del vendedor a solo dígitos, con código de país.

    Se guarda sin '+', espacios ni guiones porque es el formato que espera la API
    de Meta. Lo que la persona escriba ("+54 9 223 340-7778", "54 9 2233407778")
    llega al mismo lugar.
    """
    n = "".join(c for c in (telefono or "") if c.isdigit())
    if not n:
        raise RepoError("Escribí tu número de WhatsApp")
    if len(n) < 10:
        raise RepoError("Falta el código de país. Ej: +54 9 223 340 7778")
    if len(n) > 20:
        raise RepoError("Ese número tiene demasiados dígitos")
    if n.startswith("0"):
        raise RepoError("No pongas el 0 inicial: va con código de país. "
                        "Ej: +54 9 223 340 7778")
    return n


def set_wa_phone(account_id: int, telefono: str) -> str:
    """Guarda el WhatsApp del vendedor. Devuelve el número normalizado."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    n = _norm_wa_phone(telefono)
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        if not a:
            raise RepoError("Cuenta no encontrada")
        a.wa_phone = n
    return n


def get_wa_phone(account_id: int) -> str:
    if not db_available() or not account_id:
        return ""
    with session_scope() as s:
        a = s.query(Account).filter(Account.id == account_id).first()
        return (a.wa_phone or "") if a else ""


def _unique_slug(s, base: str) -> str:
    slug = base
    n = 2
    while s.query(Account).filter(Account.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_vendedor(*, name: str, crm_email: str, crm_url: str = "",
                    crm_idvendedor: str = "", crm_idventa: str = "", crm_proxy: str = "",
                    crm_disponible: str = "") -> dict:
    """Da de alta un vendedor (Account). El admin lo usa para pre-registrarlo
    antes de que entre con su login.

    Sin contraseña a propósito: la pone el vendedor al entrar y no se guarda. El
    admin da de alta el email de Growi y el resto de la config; la credencial es
    del vendedor y nadie más la ve."""
    if not db_available():
        raise RepoError("Base de datos no disponible")
    name = (name or "").strip()
    email = (crm_email or "").strip().lower()
    if not name:
        raise RepoError("El nombre del vendedor es obligatorio")
    if not email:
        raise RepoError("El email de Growi es obligatorio")
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
            crm_idvendedor=(crm_idvendedor or "").strip() or None,
            crm_idventa=(crm_idventa or "").strip() or None,
            crm_proxy=(crm_proxy or "").strip() or None,
            crm_disponible=(str(crm_disponible).strip() or None),
        )
        s.add(a)
        s.flush()
        return _account_to_dict(a)


def create_pending_vendedor(*, crm_email: str, crm_url: str = "",
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
                    crm_url=None, crm_idvendedor=None,
                    crm_idventa=None, crm_proxy=None, crm_disponible=None) -> dict:
    """Edita los datos de un vendedor. Campos None se dejan igual.

    La contraseña de Growi no está: no se guarda ni se puede cambiar desde acá.
    Si el vendedor la cambió en Growi, entra con la nueva y listo."""
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


# ── Cola de órdenes pendientes ────────────────────────────────────────────────

# Backoff entre reintentos, en minutos. Arranca rápido (una caída de red suele
# durar segundos) y se abre para no martillar un CRM que está caído de verdad.
# Después del último valor se repite cada 60 min hasta agotar MAX_INTENTOS.
BACKOFF_MIN = [1, 2, 5, 10, 20, 30, 60]
MAX_INTENTOS = 24          # con el backoff de arriba, ~20 horas de reintentos

# Cuánto puede durar como mucho un envío en vuelo antes de considerarlo colgado.
# El timeout del POST al CRM es de un minuto, así que 10 es holgadísimo: si pasó
# eso, el proceso se murió en el medio y la fila hay que rescatarla.
TIMEOUT_ENVIANDO_MIN = int(os.environ.get("GROWI_TIMEOUT_ENVIANDO_MIN", "10"))


def _pending_order_to_dict(p: PendingOrder) -> dict:
    return {
        "id": p.id,
        "account_id": p.account_id,
        "user_id": p.user_id,
        "post_url": p.post_url,
        "client_ig_username": p.client_ig_username,
        "payload": p.payload or {},
        "estado": p.estado,
        "intentos": p.intentos,
        "ultimo_error": p.ultimo_error,
        "proximo_intento": p.proximo_intento.isoformat() if p.proximo_intento else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "enviada_at": p.enviada_at.isoformat() if p.enviada_at else None,
    }


def encolar_orden(post_url: str, payload: dict, *, account_id=None, user_id=None,
                  client_ig_username: str = "", error: str = "") -> Optional[dict]:
    """Guarda una orden que no se pudo enviar, para reintentarla sola.

    Devuelve None si no hay DB: sin persistencia no hay cola posible, y el
    llamador tiene que seguir informando el error como antes en vez de mentirle
    al vendedor diciéndole que quedó encolada.
    """
    if not db_available():
        return None
    with session_scope() as s:
        p = PendingOrder(
            account_id=account_id,
            user_id=user_id,
            post_url=post_url,
            client_ig_username=(client_ig_username or "") or None,
            payload=payload,
            estado="pendiente",
            intentos=0,
            ultimo_error=(error or "")[:2000] or None,
            # Primer reintento ya mismo: si fue un parpadeo de red, sale en la
            # próxima vuelta del worker y el vendedor casi no lo nota.
            proximo_intento=_utcnow_naive(),
        )
        s.add(p)
        s.flush()
        return _pending_order_to_dict(p)


def _utcnow_naive():
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc)


def tomar_orden_para_reintentar() -> Optional[dict]:
    """Reclama UNA orden vencida y la marca 'enviando', en una sola transacción.

    El claim atómico (UPDATE ... WHERE estado='pendiente') es lo que evita que
    dos workers —o dos réplicas del servicio— manden la misma orden al CRM y le
    cobren dos veces al cliente. Devuelve None si no hay nada que hacer.
    """
    if not db_available():
        return None
    from sqlalchemy import or_
    ahora = _utcnow_naive()
    with session_scope() as s:
        # with_for_update(skip_locked) deja que varios workers trabajen en
        # paralelo sobre filas distintas sin bloquearse entre ellos.
        q = (s.query(PendingOrder)
               .filter(PendingOrder.estado == "pendiente")
               .filter(or_(PendingOrder.proximo_intento == None,      # noqa: E711
                           PendingOrder.proximo_intento <= ahora))
               .order_by(PendingOrder.created_at.asc()))
        try:
            p = q.with_for_update(skip_locked=True).first()
        except Exception:
            # SQLite y otros backends sin SKIP LOCKED: cae al claim simple. Con
            # un solo worker (el caso actual) es igual de seguro.
            p = q.first()
        if p is None:
            return None
        p.estado = "enviando"
        p.intentos = (p.intentos or 0) + 1
        # Fecha límite del envío en vuelo. Si el proceso se muere en el medio
        # (deploy, OOM, reinicio), la fila quedaba en 'enviando' para siempre:
        # nadie la volvía a tomar y no aparecía en ningún lado. Con esto queda
        # marcada y `revisar_ordenes_colgadas` la rescata.
        from datetime import timedelta
        p.proximo_intento = ahora + timedelta(minutes=TIMEOUT_ENVIANDO_MIN)
        s.flush()
        return _pending_order_to_dict(p)


def revisar_ordenes_colgadas(minutos: int = TIMEOUT_ENVIANDO_MIN) -> int:
    """Rescata las órdenes que quedaron colgadas en 'enviando'.

    Van a 'revisar' y NO se reintentan solas: el proceso murió con el envío en
    vuelo, así que no sabemos si el CRM llegó a cargar la orden. Reintentarla
    automáticamente podría cobrarle dos veces al cliente; que la mire un humano.

    Devuelve cuántas rescató.
    """
    if not db_available():
        return 0
    from datetime import timedelta
    limite = _utcnow_naive() - timedelta(minutes=0)   # el margen ya está en proximo_intento
    with session_scope() as s:
        colgadas = (s.query(PendingOrder)
                      .filter(PendingOrder.estado == "enviando")
                      .filter(PendingOrder.proximo_intento != None)   # noqa: E711
                      .filter(PendingOrder.proximo_intento <= limite)
                      .all())
        for p in colgadas:
            p.estado = "revisar"
            p.proximo_intento = None
            p.ultimo_error = (
                f"El envío quedó colgado más de {minutos} min (probablemente se "
                "reinició el servicio). No se reintenta solo: revisá en Growi si "
                "la orden entró antes de volver a mandarla."
            )
        if colgadas:
            print(f"[cola] {len(colgadas)} orden(es) colgadas en 'enviando' pasaron "
                  f"a revisión manual", flush=True)
        return len(colgadas)


def marcar_orden_enviada(orden_id: int) -> None:
    if not db_available():
        return
    with session_scope() as s:
        p = s.query(PendingOrder).filter(PendingOrder.id == orden_id).first()
        if p:
            p.estado = "enviada"
            p.enviada_at = _utcnow_naive()


def reprogramar_orden(orden_id: int, error: str, *, reintentable: bool = True) -> dict:
    """Devuelve la orden al estado que corresponda tras un intento fallido.

    reintentable=False es el caso delicado: el envío pudo haber llegado al CRM.
    Se marca 'revisar' y NO se reintenta nunca sola — que un humano mire Growi
    antes de arriesgar una orden duplicada.
    """
    from datetime import timedelta
    if not db_available():
        return {}
    with session_scope() as s:
        p = s.query(PendingOrder).filter(PendingOrder.id == orden_id).first()
        if not p:
            return {}
        p.ultimo_error = (error or "")[:2000] or None
        if not reintentable:
            p.estado = "revisar"
            p.proximo_intento = None
        elif p.intentos >= MAX_INTENTOS:
            p.estado = "fallida"
            p.proximo_intento = None
        else:
            idx = min(p.intentos - 1, len(BACKOFF_MIN) - 1)
            espera = BACKOFF_MIN[max(idx, 0)]
            p.estado = "pendiente"
            p.proximo_intento = _utcnow_naive() + timedelta(minutes=espera)
        s.flush()
        return _pending_order_to_dict(p)


def list_pending_orders(account_id=None, estados=None, limite: int = 100) -> list[dict]:
    """Cola visible para el panel. Sin account_id, la de todas las cuentas."""
    if not db_available():
        return []
    with session_scope() as s:
        q = s.query(PendingOrder)
        if account_id is not None:
            q = q.filter(PendingOrder.account_id == account_id)
        if estados:
            q = q.filter(PendingOrder.estado.in_(list(estados)))
        q = q.order_by(PendingOrder.created_at.desc()).limit(limite)
        return [_pending_order_to_dict(p) for p in q.all()]


def contar_ordenes_en_cola() -> dict:
    """Resumen por estado, para el healthcheck y el panel."""
    if not db_available():
        return {}
    from sqlalchemy import func
    with session_scope() as s:
        filas = (s.query(PendingOrder.estado, func.count(PendingOrder.id))
                   .group_by(PendingOrder.estado).all())
        return {estado: total for estado, total in filas}


def cancelar_orden(orden_id: int, account_id=None) -> bool:
    """Baja manual de una orden encolada (el vendedor ya la cargó a mano, o no
    la quiere más). account_id acota para que nadie cancele órdenes ajenas."""
    if not db_available():
        return False
    with session_scope() as s:
        q = s.query(PendingOrder).filter(PendingOrder.id == orden_id)
        if account_id is not None:
            q = q.filter(PendingOrder.account_id == account_id)
        p = q.first()
        # 'enviando' no se cancela: hay un worker con el envío en vuelo.
        if not p or p.estado in ("enviada", "enviando"):
            return False
        p.estado = "cancelada"
        p.proximo_intento = None
        return True


def get_pending_order_account(orden_id: int):
    """De qué cuenta es una orden encolada. None si no existe o no tiene cuenta.

    Lo usa el reintento manual para saber si quien lo pide es su dueño: el envío
    sale con la contraseña de Growi del que está logueado, y esa es la única que
    el sistema tiene (en memoria, y solo la suya).
    """
    if not db_available():
        return None
    with session_scope() as s:
        p = s.query(PendingOrder).filter(PendingOrder.id == orden_id).first()
        return p.account_id if p else None


def tomar_orden_puntual(orden_id: int, account_id=None) -> Optional[dict]:
    """Reclama UNA orden concreta y la marca 'enviando'. None si no se puede.

    Es el reintento manual del vendedor: como ya no hay worker de fondo, el envío
    sale dentro de su request (es el único momento en que tenemos su contraseña
    del CRM). Por eso acá se hace el claim además de la validación: es el mismo
    UPDATE atómico que usaba el worker, y es lo que evita que dos clicks seguidos
    en "reintentar" manden la orden dos veces y le cobren doble al cliente.

    Solo aplica a 'revisar' y 'fallida', los dos estados donde el envío está
    frenado y sabemos que no hay nada en vuelo. Las 'enviando' tienen un POST sin
    respuesta todavía y las 'pendiente' están reclamadas: tocarlas duplicaría.

    Los intentos se resetean a propósito: el vendedor está diciendo que el
    problema de fondo ya lo arregló.
    """
    if not db_available():
        return None
    ahora = _utcnow_naive()
    with session_scope() as s:
        q = s.query(PendingOrder).filter(PendingOrder.id == orden_id,
                                         PendingOrder.estado.in_(("revisar", "fallida")))
        if account_id is not None:
            q = q.filter(PendingOrder.account_id == account_id)
        try:
            p = q.with_for_update(skip_locked=True).first()
        except Exception:
            # SQLite y backends sin SKIP LOCKED: claim simple.
            p = q.first()
        if p is None:
            return None
        p.estado = "enviando"
        p.intentos = 0
        # Igual que en el claim del worker: si el proceso se muere con el envío
        # en vuelo, `revisar_ordenes_colgadas` la rescata pasado este plazo.
        from datetime import timedelta
        p.proximo_intento = ahora + timedelta(minutes=TIMEOUT_ENVIANDO_MIN)
        s.flush()
        return _pending_order_to_dict(p)


# ── Trazabilidad de las llamadas al CRM de Growi ──────────────────────────────
# El CRM es de un tercero y no deja auditar nada del lado nuestro. Estas
# funciones guardan y consultan qué le mandamos y qué contestó, para poder
# reconstruir un envío días después (ver common/growi_trace.py).

def _growi_call_to_dict(c: GrowiCall, con_cuerpos: bool = False) -> dict:
    d = {
        "id": c.id,
        "trace_id": c.trace_id,
        "origen": c.origen,
        "operacion": c.operacion,
        "method": c.method,
        "url": c.url,
        "account_id": c.account_id,
        "user_id": c.user_id,
        "username": c.username,
        "status_code": c.status_code,
        "ok": bool(c.ok),
        "duracion_ms": c.duracion_ms,
        "intentos": c.intentos,
        "proxy": c.proxy,
        "error": c.error,
        "post_url": c.post_url,
        "client_ig_username": c.client_ig_username,
        "idventa": c.idventa,
        "idvendedor": c.idvendedor,
        "costo": c.costo,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    # El motivo de un rechazo (200 + success:false) vive en el cuerpo, así que
    # el listado necesita una probadita: sin esto no hay con qué explicarle al
    # vendedor por qué no entró la orden sin pedir el detalle fila por fila.
    d["response_snippet"] = (c.response_body or "")[:300] or None
    # Con qué se podría reenviar esta llamada, sin mandar el payload entero al
    # listado. El payload de un envío guarda las órdenes CON sus comentarios,
    # así que un rebote se puede reintentar tal cual; salvo que la traza lo haya
    # truncado por tamaño (ver _acotar_payload), y ahí ya no está completo.
    p = c.request_payload if isinstance(c.request_payload, dict) else {}
    d["payload_truncado"] = bool(p.get("_truncado"))
    d["ordenes_guardadas"] = len(p.get("ordenes") or []) if not d["payload_truncado"] else 0
    if con_cuerpos:
        # Los cuerpos solo viajan en el detalle: en el listado serían cientos de
        # KB por pantalla para algo que casi nunca se mira fila por fila.
        d["request_payload"] = c.request_payload
        d["request_headers"] = c.request_headers
        d["response_body"] = c.response_body
        d["response_headers"] = c.response_headers
    return d


def registrar_llamada_crm(**campos) -> bool:
    """Guarda una llamada al CRM. Devuelve False si no se pudo (sin DB o error):
    la auditoría es best-effort y nunca corta el envío."""
    if not db_available():
        return False
    try:
        with session_scope() as s:
            s.add(GrowiCall(**campos))
        return True
    except Exception as e:
        print(f"[growi-trace] insert fallido: {e!r}", flush=True)
        return False


def list_growi_calls(account_id=None, *, operacion=None, solo_errores=False,
                     q=None, trace_id=None, limite: int = 200) -> list[dict]:
    """Últimas llamadas al CRM, de más nueva a más vieja.

    account_id acota a una cuenta (el vendedor solo ve lo suyo); None trae todo,
    que es lo que ve el admin.
    """
    if not db_available():
        return []
    from sqlalchemy import or_
    limite = max(1, min(int(limite or 200), 500))
    with session_scope() as s:
        query = s.query(GrowiCall)
        if account_id is not None:
            query = query.filter(GrowiCall.account_id == account_id)
        if operacion:
            query = query.filter(GrowiCall.operacion == operacion)
        if solo_errores:
            query = query.filter(GrowiCall.ok == False)   # noqa: E712
        if trace_id:
            query = query.filter(GrowiCall.trace_id == trace_id)
        if q:
            like = f"%{q.strip().lstrip('@')}%"
            query = query.filter(or_(GrowiCall.client_ig_username.ilike(like),
                                     GrowiCall.username.ilike(like),
                                     GrowiCall.post_url.ilike(like),
                                     GrowiCall.idventa.ilike(like)))
        filas = query.order_by(GrowiCall.created_at.desc()).limit(limite).all()
        return [_growi_call_to_dict(c) for c in filas]


def get_growi_call(call_id: int, account_id=None) -> Optional[dict]:
    """Detalle con los cuerpos completos. account_id acota igual que el listado."""
    if not db_available():
        return None
    with session_scope() as s:
        q = s.query(GrowiCall).filter(GrowiCall.id == call_id)
        if account_id is not None:
            q = q.filter(GrowiCall.account_id == account_id)
        c = q.first()
        return _growi_call_to_dict(c, con_cuerpos=True) if c else None


def growi_calls_resumen(account_id=None, horas: int = 24) -> dict:
    """Cuántas llamadas y cuántas fallaron en las últimas N horas. Alimenta el
    contador de la pestaña: lo que importa ver de un vistazo son los errores."""
    if not db_available():
        return {}
    from datetime import timedelta
    from sqlalchemy import func
    desde = _utcnow_naive() - timedelta(hours=max(1, horas))
    with session_scope() as s:
        q = s.query(GrowiCall.ok, func.count(GrowiCall.id)).filter(GrowiCall.created_at >= desde)
        if account_id is not None:
            q = q.filter(GrowiCall.account_id == account_id)
        filas = dict(q.group_by(GrowiCall.ok).all())
        ok = int(filas.get(True, 0))
        err = int(filas.get(False, 0))
        return {"ok": ok, "errores": err, "total": ok + err, "horas": horas}


def growi_calls_purgar(dias: int = 30) -> int:
    """Borra trazas más viejas que N días. Es un log, no un registro contable:
    sin purga la tabla crece para siempre con cuerpos de respuesta."""
    if not db_available():
        return 0
    from datetime import timedelta
    try:
        limite = _utcnow_naive() - timedelta(days=max(1, dias))
        with session_scope() as s:
            n = (s.query(GrowiCall)
                   .filter(GrowiCall.created_at < limite)
                   .delete(synchronize_session=False))
            if n:
                print(f"[growi-trace] purgadas {n} trazas de más de {dias} días", flush=True)
            return int(n or 0)
    except Exception as e:
        print(f"[growi-trace] error purgando: {e}", flush=True)
        return 0


# ── Contabilidad de tokens ────────────────────────────────────────────────────

def registrar_tokens(*, kind: str, model: str, input_tokens: int = 0,
                     output_tokens: int = 0, cache_read_tokens: int = 0,
                     cache_creation_tokens: int = 0, costo_usd: float = 0.0,
                     intento: int = 1, client_ig_username=None, shortcode=None,
                     account_id=None, user_id=None) -> bool:
    """Guarda el consumo de UNA llamada a la IA. Devuelve True si se guardó.

    Best-effort a propósito: no levanta nunca. Loguear el gasto no puede ser
    motivo de que a un vendedor le falle la tanda, así que cualquier problema de
    DB se traga (el llamador ya imprimió la línea por consola de todas formas).
    """
    if not db_available():
        return False
    try:
        with session_scope() as s:
            s.add(TokenUsage(
                kind=kind, model=model, intento=intento,
                input_tokens=input_tokens or 0, output_tokens=output_tokens or 0,
                cache_read_tokens=cache_read_tokens or 0,
                cache_creation_tokens=cache_creation_tokens or 0,
                costo_usd=float(costo_usd or 0.0),
                client_ig_username=client_ig_username, shortcode=shortcode,
                account_id=account_id, user_id=user_id,
            ))
        return True
    except Exception as e:
        print(f"[tokens] no se pudo registrar el uso: {e}", flush=True)
        return False


def resumen_tokens(dias: int = 30, account_id=None) -> dict:
    """Gasto agregado de los últimos N días: total, por tipo de llamada, por
    modelo y por cliente. Es lo que necesita el admin para ver dónde se va la
    plata (y cuánto cuestan los reintentos)."""
    if not db_available():
        return {}
    from datetime import timedelta
    from sqlalchemy import func
    desde = _utcnow_naive() - timedelta(days=max(1, dias))

    def _filtrar(q):
        q = q.filter(TokenUsage.created_at >= desde)
        if account_id is not None:
            q = q.filter(TokenUsage.account_id == account_id)
        return q

    with session_scope() as s:
        cols = (func.count(TokenUsage.id), func.coalesce(func.sum(TokenUsage.costo_usd), 0.0),
                func.coalesce(func.sum(TokenUsage.input_tokens), 0),
                func.coalesce(func.sum(TokenUsage.output_tokens), 0))
        llamadas, costo, tin, tout = _filtrar(s.query(*cols)).one()

        def _agrupar(col):
            filas = _filtrar(s.query(col, func.count(TokenUsage.id),
                                     func.coalesce(func.sum(TokenUsage.costo_usd), 0.0))
                             ).group_by(col).all()
            return [{"clave": k, "llamadas": n, "costo_usd": round(float(c), 4)}
                    for k, n, c in filas]

        # Reintentos: llamadas con intento > 1. Es gasto 100% tirado (la
        # generación anterior se descartó), así que va en su propia línea.
        reint_n, reint_costo = _filtrar(
            s.query(func.count(TokenUsage.id),
                    func.coalesce(func.sum(TokenUsage.costo_usd), 0.0))
        ).filter(TokenUsage.intento > 1).one()

        return {
            "dias": dias,
            "llamadas": llamadas,
            "costo_usd": round(float(costo), 4),
            "input_tokens": int(tin),
            "output_tokens": int(tout),
            "reintentos": {"llamadas": reint_n, "costo_usd": round(float(reint_costo), 4)},
            "por_kind": _agrupar(TokenUsage.kind),
            "por_modelo": _agrupar(TokenUsage.model),
            "por_cliente": sorted(_agrupar(TokenUsage.client_ig_username),
                                  key=lambda x: -x["costo_usd"])[:20],
        }


def _mes_inicio(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _sumar_meses(dt, n: int):
    """Corre una fecha N meses (n negativo va para atrás). Siempre sobre el día 1,
    así que no hay que preocuparse por los meses de 30/31 días ni por febrero."""
    mes = dt.month - 1 + n
    return _mes_inicio(dt).replace(year=dt.year + mes // 12, month=mes % 12 + 1)


def gasto_por_vendedor(desde=None, hasta=None, meses_default: int = 6) -> dict:
    """Cuánto gastó cada vendedor en un rango de fechas, con el corte por mes.

    desde/hasta son datetimes (naive, UTC — igual que created_at). `hasta` es
    EXCLUSIVO: el llamador que quiere "todo agosto" pasa 1/8 y 1/9, y no hay que
    razonar sobre el último segundo del día. Sin rango se toman los últimos
    `meses_default` meses calendario completos, incluido el actual.

    Dos detalles que importan para leer bien el número:

    1) Hasta que se agregó la atribución, las filas de token_usage se guardaban
       con account_id NULL. Para no tirar ese histórico, la fila sin dueño se le
       imputa a la cuenta del @cliente (clients.ig_username). Lo que no se pueda
       resolver ni así queda aparte, en `sin_atribuir`, en vez de repartirse
       entre los vendedores y ensuciarles el número.
    2) Un mismo @cliente cargado en dos cuentas es ambiguo: en ese caso NO se
       adivina, la fila cae en `sin_atribuir`.
    """
    if not db_available():
        return {}
    from datetime import timedelta
    from sqlalchemy import String, case, func

    ahora = _utcnow_naive()
    if desde is None:
        desde = _sumar_meses(ahora, -(max(1, meses_default) - 1))
    if hasta is None:
        hasta = _sumar_meses(ahora, 1)      # fin del mes actual (exclusivo)
    if hasta <= desde:                       # rango dado vuelta: no reventamos
        hasta = desde

    # Clave de mes "YYYY-MM" sin funciones propias del motor: castear el
    # timestamp a texto y cortar da lo mismo en Postgres y en SQLite, y evita
    # tener un date_trunc/strftime distinto por backend.
    mes_col = func.substr(func.cast(TokenUsage.created_at, String), 1, 7)

    with session_scope() as s:
        # @cliente -> cuenta, solo cuando es inequívoco (ver punto 2).
        dueño = {}
        for ig, acc_id, n in (s.query(Client.ig_username, func.min(Client.account_id),
                                      func.count(func.distinct(Client.account_id)))
                              .group_by(Client.ig_username).all()):
            if n == 1:
                dueño[ig] = acc_id

        usuarios = {u.id: u.username for u in s.query(User).all()}

        filas = (
            s.query(TokenUsage.account_id, TokenUsage.user_id,
                    TokenUsage.client_ig_username, TokenUsage.kind,
                    mes_col.label("mes"),
                    func.count(TokenUsage.id),
                    func.coalesce(func.sum(TokenUsage.input_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.output_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.cache_read_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.cache_creation_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.costo_usd), 0.0),
                    func.coalesce(func.sum(
                        case((TokenUsage.intento > 1, TokenUsage.costo_usd), else_=0.0)), 0.0))
            .filter(TokenUsage.created_at >= desde, TokenUsage.created_at < hasta)
            .group_by(TokenUsage.account_id, TokenUsage.user_id,
                      TokenUsage.client_ig_username, TokenUsage.kind, mes_col)
            .all()
        )

        def _nuevo(**extra):
            base = {"llamadas": 0, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "costo_usd": 0.0, "costo_reintentos_usd": 0.0}
            base.update(extra)
            return base

        def _sumar(dst, n, tin, tout, cr, cw, costo, reint):
            dst["llamadas"] += n
            dst["input_tokens"] += int(tin)
            dst["output_tokens"] += int(tout)
            dst["cache_read_tokens"] += int(cr)
            dst["cache_creation_tokens"] += int(cw)
            dst["costo_usd"] += float(costo)
            dst["costo_reintentos_usd"] += float(reint)

        cuentas = {
            a.id: _nuevo(account_id=a.id, name=a.name, crm_email=a.crm_email,
                         active=a.active, usuarios={}, clientes={}, por_kind={},
                         por_mes={}, estimado=0)   # llamadas resueltas por @cliente
            for a in s.query(Account).all()
        }
        sin_atribuir = _nuevo(por_kind={})
        total = _nuevo(por_mes={})

        def _mes(dst, mes, n, costo):
            m = dst["por_mes"].setdefault(mes, {"mes": mes, "llamadas": 0, "costo_usd": 0.0})
            m["llamadas"] += n
            m["costo_usd"] += float(costo)

        for (acc_id, uid, ig, kind, mes, n, tin, tout, cr, cw, costo, reint) in filas:
            _sumar(total, n, tin, tout, cr, cw, costo, reint)
            _mes(total, mes, n, costo)
            estimada = False
            if acc_id is None:
                acc_id = dueño.get(ig)
                estimada = acc_id is not None
            cuenta = cuentas.get(acc_id) if acc_id is not None else None
            if cuenta is None:
                _sumar(sin_atribuir, n, tin, tout, cr, cw, costo, reint)
                sin_atribuir["por_kind"][kind] = round(
                    sin_atribuir["por_kind"].get(kind, 0.0) + float(costo), 4)
                continue

            _sumar(cuenta, n, tin, tout, cr, cw, costo, reint)
            _mes(cuenta, mes, n, costo)
            if estimada:
                cuenta["estimado"] += n
            cuenta["por_kind"][kind] = round(
                cuenta["por_kind"].get(kind, 0.0) + float(costo), 4)
            if ig:
                c = cuenta["clientes"].setdefault(ig, {"clave": ig, "llamadas": 0,
                                                       "costo_usd": 0.0})
                c["llamadas"] += n
                c["costo_usd"] += float(costo)
            # El desglose por usuario es lo que permite decir "de los $12 de esta
            # cuenta, $9 los gastó fulano". Las filas viejas no lo tienen.
            clave_u = uid if uid is not None else 0
            u = cuenta["usuarios"].setdefault(
                clave_u, _nuevo(user_id=uid,
                                username=usuarios.get(uid) or "(sin identificar)"))
            _sumar(u, n, tin, tout, cr, cw, costo, reint)

        def _limpiar(d):
            d["costo_usd"] = round(d["costo_usd"], 4)
            d["costo_reintentos_usd"] = round(d["costo_reintentos_usd"], 4)
            return d

        def _serie(d):
            """Meses ordenados cronológicamente, rellenando con cero los que no
            tuvieron gasto: si falta el mes vacío, la comparación mes a mes
            miente (un mes sin actividad parecía no existir)."""
            por_mes = d.pop("por_mes", {})
            serie, cur = [], _mes_inicio(desde)
            fin = hasta
            while cur < fin:
                clave = cur.strftime("%Y-%m")
                m = por_mes.get(clave) or {"mes": clave, "llamadas": 0, "costo_usd": 0.0}
                serie.append({"mes": clave, "llamadas": m["llamadas"],
                              "costo_usd": round(float(m["costo_usd"]), 4)})
                cur = _sumar_meses(cur, 1)
            d["por_mes"] = serie
            return d

        vendedores = []
        for c in cuentas.values():
            if not c["llamadas"]:
                c.pop("por_mes", None)
                continue          # cuentas que no gastaron nada no ensucian la tabla
            _serie(c)
            c["usuarios"] = sorted((_limpiar(u) for u in c["usuarios"].values()),
                                   key=lambda u: -u["costo_usd"])
            c["clientes"] = sorted(
                ({"clave": v["clave"], "llamadas": v["llamadas"],
                  "costo_usd": round(v["costo_usd"], 4)}
                 for v in c["clientes"].values()),
                key=lambda v: -v["costo_usd"])[:10]
            vendedores.append(_limpiar(c))
        vendedores.sort(key=lambda v: -v["costo_usd"])

        return {
            # El rango que se usó DE VERDAD (el front pudo no mandar ninguno, o
            # mandar uno inválido): así el título dice lo que se está mirando.
            "desde": desde.strftime("%Y-%m-%d"),
            # `hasta` es exclusivo internamente; afuera se devuelve el último día
            # incluido, que es lo que el usuario eligió y espera ver.
            "hasta": (hasta - timedelta(days=1)).strftime("%Y-%m-%d"),
            "total": _serie(_limpiar(total)),
            "vendedores": vendedores,
            "sin_atribuir": _limpiar(sin_atribuir),
        }


# ── Caché persistente de posts ────────────────────────────────────────────────

# Un post no cambia: la imagen, la transcripción y la descripción son las mismas
# mañana. El TTL existe para que el caption y el dueño no queden viejos para
# siempre, no porque el contenido caduque.
def _post_cache_to_dict(p: PostCache) -> dict:
    return {
        "shortcode": p.shortcode,
        "url": p.url,
        "caption": p.caption or "",
        "owner_username": p.owner_username or "",
        "owner_full_name": p.owner_full_name or "",
        "collaborators": list(p.collaborators or []),
        "transcription": p.transcription or "",
        "photo_description": p.photo_description or "",
        "is_video": bool(p.is_video),
        "image_b64": p.image_b64 or "",
        "image_media_type": p.image_media_type or "",
        "n_imagenes": p.n_imagenes or 1,
    }


def post_cache_get(shortcode: str, ttl_horas: int = 24) -> Optional[dict]:
    """Post ya procesado, o None si no está o venció. Suma un hit (para métricas)."""
    if not db_available() or not shortcode:
        return None
    from datetime import timedelta
    try:
        limite = _utcnow_naive() - timedelta(hours=max(1, ttl_horas))
        with session_scope() as s:
            p = (s.query(PostCache)
                   .filter(PostCache.shortcode == shortcode)
                   .filter(PostCache.created_at >= limite)
                   .first())
            if not p:
                return None
            p.hits = (p.hits or 0) + 1
            p.last_hit_at = _utcnow_naive()
            return _post_cache_to_dict(p)
    except Exception as e:
        print(f"[cache-db] error leyendo el post {shortcode}: {e}", flush=True)
        return None


def post_cache_put(datos: dict) -> bool:
    """Guarda (o reemplaza) un post en el caché. Best-effort: nunca levanta.

    `datos` usa las mismas claves que PostData. Si el shortcode ya está, se
    sobreescribe con lo nuevo y se reinicia la antigüedad.
    """
    if not db_available():
        return False
    shortcode = (datos or {}).get("shortcode")
    if not shortcode:
        return False
    campos = {k: datos.get(k) for k in (
        "url", "caption", "owner_username", "owner_full_name", "transcription",
        "photo_description", "image_b64", "image_media_type")}
    campos["collaborators"] = list(datos.get("collaborators") or [])
    campos["is_video"] = bool(datos.get("is_video"))
    campos["n_imagenes"] = int(datos.get("n_imagenes") or 1)
    try:
        with session_scope() as s:
            p = s.query(PostCache).filter(PostCache.shortcode == shortcode).first()
            if p is None:
                p = PostCache(shortcode=shortcode, **campos)
                s.add(p)
            else:
                for k, v in campos.items():
                    setattr(p, k, v)
                p.created_at = _utcnow_naive()   # se renueva el TTL
            return True
    except Exception as e:
        print(f"[cache-db] no se pudo guardar el post {shortcode}: {e}", flush=True)
        return False


def post_cache_purgar(dias: int = 30) -> int:
    """Borra los posts cacheados más viejos que N días. Devuelve cuántos borró.

    El image_b64 son unos 5 KB por fila, así que la tabla no explota — pero
    tampoco tiene sentido guardar para siempre un post que nadie va a volver a
    pegar. Se llama de vez en cuando, no en el camino de una generación.
    """
    if not db_available():
        return 0
    from datetime import timedelta
    try:
        limite = _utcnow_naive() - timedelta(days=max(1, dias))
        with session_scope() as s:
            n = (s.query(PostCache)
                   .filter(PostCache.created_at < limite)
                   .delete(synchronize_session=False))
            if n:
                print(f"[cache-db] purgados {n} posts de más de {dias} días", flush=True)
            return int(n or 0)
    except Exception as e:
        print(f"[cache-db] error purgando: {e}", flush=True)
        return 0
