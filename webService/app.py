from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, make_response
from functools import wraps
import requests
import os
import json
import re
import time
import threading
from datetime import timedelta

app = Flask(__name__)

# El dev-server de Werkzeug escribe su propio header `Server:` (con versión de
# Werkzeug+Python) a nivel socket, después de la respuesta de la app: no se puede
# pisar desde after_request. Se parchea el handler para no filtrar la pila. En
# prod bajo gunicorn esto no aplica (gunicorn setea su propio Server).
try:
    from werkzeug.serving import WSGIRequestHandler as _WSGIHandler
    _WSGIHandler.server_version = "GROWI"
    _WSGIHandler.sys_version = ""
except Exception:
    pass


def _is_production() -> bool:
    """Producción = Railway (setea RAILWAY_ENVIRONMENT solo) o APP_ENV explícito.
    En dev/local no hay ninguna, así que los fallbacks de abajo siguen andando."""
    if os.environ.get("APP_ENV", "").strip().lower() in ("prod", "production"):
        return True
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


# SECRET_KEY: firma las cookies de sesión. En prod conviene setearla como
# variable de entorno; si falta, cae a una clave fija para no bloquear el arranque.
app.secret_key = os.environ.get("SECRET_KEY", "").strip() or "growi-secret-2026"

# Cookies de sesión: HttpOnly (default de Flask) y SameSite=Lax (corta el CSRF
# cross-site). El flag Secure quedó OPT-IN por env: detrás del proxy de Railway,
# forzarlo en prod fue el sospechoso de que la sesión se perdiera y hubiera que
# re-loguear. Se prende sólo con SESSION_COOKIE_SECURE=1 cuando esté confirmado.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").strip() in ("1", "true", "yes"),
    # La sesión dura (login persistente): antes era cookie de sesión "a secas" y
    # moría al cerrar/reciclar la pestaña, obligando a re-loguear seguido.
    PERMANENT_SESSION_LIFETIME=timedelta(
        days=int(os.environ.get("SESSION_DAYS", "14"))),
)

# Recargar templates ante cambios sin reiniciar el proceso (dev / edición en caliente).
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


# ── Headers de seguridad ─────────────────────────────────────────────────────
# El CSP permite 'unsafe-inline' en script/style porque las plantillas usan
# onclick=/style= inline en todos lados (sacarlos es un refactor aparte). Aun
# así suma: frame-ancestors corta clickjacking, y se acota de dónde pueden
# venir scripts, fuentes y conexiones. La defensa anti-XSS real sigue siendo el
# autoescape de Jinja (ya verificado).
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    # imágenes: thumbnails de IG (CDNs impredecibles) + data: (avatares/preview).
    "img-src 'self' data: https:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@app.after_request
def _security_headers(resp):
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Frame-Options"] = "DENY"            # clickjacking (compat viejos)
    resp.headers["X-Content-Type-Options"] = "nosniff"  # anti MIME-sniffing
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # El `Server` genérico lo fija el parche del dev-server de arriba (no se puede
    # pisar acá porque lo escribe el WSGI server tras la respuesta).
    # HSTS solo en prod (local es http; en http el browser la ignora igual).
    if _is_production():
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

OPENAI_SERVICE_URL = os.environ.get("OPENAI_SERVICE_URL", "http://openai-service:8000")

# Capa de datos multi-tenant. Opcional: si no está, se usa el login legacy.
try:
    from common import repository as _repo
except Exception:
    _repo = None

# Login del ADMIN. Los vendedores YA NO tienen login local: entran con su email y
# password de Growi (ver _authenticate). Solo el admin conserva un login local
# nuestro. Se acepta un admin de la DB (role=admin) y, como fallback anti-lockout
# si la DB está caída, estas credenciales históricas.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "growi-admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "growi2026")
LEGACY_ADMIN = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}

GROWI_CRM_URL    = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
GROWI_CRM_EMAIL  = os.environ.get("GROWI_CRM_EMAIL", "")
GROWI_CRM_PASSWORD = os.environ.get("GROWI_CRM_PASSWORD", "")
GROWI_IDVENDEDOR = os.environ.get("GROWI_IDVENDEDOR", "")
GROWI_IDVENTA    = os.environ.get("GROWI_IDVENTA", "32600")  # id del cliente en el CRM
DISPONIBLE       = float(os.environ.get("GROWI_DISPONIBLE", "150"))

# El CRM ata la sesión a la IP que se loguea; se rutea por un proxy de IP fija
# si está configurado (ver openAIService/modules/growi_client.py, misma idea).
# Admite varios separados por coma, con failover (ver common/proxy_pool.py).
try:
    from common.proxy_pool import ProxyPool, proxies_de
except Exception:
    # Mismo criterio que el import de `common.repository` de más arriba: correr
    # el webService suelto (sin el paquete común en el path) tiene que seguir
    # funcionando. Sin pool, se usa el primer proxy y listo.
    def proxies_de(p):
        return {"http": p, "https": p} if p else {}

    class ProxyPool:
        def __init__(self, raw="", cooldown=0):
            self._proxies = [x.strip() for x in (raw or "").split(",") if x.strip()]

        def candidatos(self):
            return self._proxies or [None]

        def marcar_muerto(self, p): pass

        def marcar_vivo(self, p): pass

_GROWI_PROXY_URL = os.environ.get("GROWI_HTTP_PROXY", "")
_GROWI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
# Sesiones del CRM cacheadas POR CUENTA (account_id -> {"session", "cfg"}). Cada
# vendedor opera Growi bajo su propio login. La clave None = sesión global del
# .env (fallback para el admin / entornos sin DB).
_growi_sessions = {}


def _mensaje_amigable(e):
    """Traduce errores del backend a un mensaje claro para el usuario, sin filtrar
    infraestructura interna (nombres de servicio, puertos, URLs internas)."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        # Si el backend mandó un mensaje limpio en el body, lo usamos.
        try:
            body = e.response.json()
            msg = body.get("error") or body.get("mensaje") or body.get("detail")
            if msg and "http://" not in str(msg) and "https://" not in str(msg):
                return str(msg)
        except Exception:
            pass
        status = e.response.status_code
        if status == 400:
            return ("El link no es válido o el post no se pudo procesar. "
                    "Verificá que sea un link de un post público de Instagram.")
        if status == 404:
            return "No encontramos el post. Revisá que el link sea correcto."
        return "No pudimos procesar el post en este momento. Probá de nuevo en unos minutos."
    if isinstance(e, requests.exceptions.Timeout):
        return "El procesamiento tardó demasiado. Probá de nuevo."
    if isinstance(e, requests.exceptions.ConnectionError):
        return "No pudimos conectar con el servicio de generación. Probá de nuevo en unos minutos."
    return "Ocurrió un error inesperado. Probá de nuevo."


def _env_crm_cfg():
    """Config del CRM tomada del .env (fallback global, usado por el admin o cuando
    no hay DB)."""
    return {
        "crm_url": GROWI_CRM_URL,
        "crm_email": GROWI_CRM_EMAIL,
        "crm_password": GROWI_CRM_PASSWORD,
        "crm_idvendedor": GROWI_IDVENDEDOR,
        "crm_idventa": GROWI_IDVENTA,
        "crm_proxy": _GROWI_PROXY_URL,
        "crm_disponible": DISPONIBLE,
    }


def _account_crm_cfg(account_id):
    """Credenciales del CRM de la cuenta (vendedor). Cae al .env global si no hay
    cuenta / DB. La URL siempre queda seteada."""
    if _repo is not None and account_id:
        try:
            cfg = _repo.get_account_crm_config(account_id)
            if cfg and cfg.get("crm_email"):
                cfg["crm_url"] = cfg.get("crm_url") or GROWI_CRM_URL
                # El proxy es infra COMPARTIDA (la IP de salida estable).
                # OJO: acá decía que el CRM tenía esa IP "en whitelist". Es
                # FALSO y confunde el diagnóstico: el CRM acepta el login desde
                # cualquier IP (verificado). Lo que hace es atar la sesión a la
                # IP del login, y Railway rota la suya entre requests: por eso
                # hace falta un proxy ESTABLE, no uno autorizado.
                # Si la cuenta del vendedor no lo tiene cargado, usamos el del .env;
                # sin esto sus pedidos salen directos y el CRM los bloquea (por eso
                # solo andaba el admin, que usa la config del .env).
                cfg["crm_proxy"] = cfg.get("crm_proxy") or _GROWI_PROXY_URL
                return cfg
        except Exception as e:
            print(f"[growi-web] no pude leer config de cuenta {account_id} ({e})", flush=True)
    return _env_crm_cfg()


class GrowiAuthError(RuntimeError):
    """El CRM no nos dejó entrar. Existe para que el front muestre el motivo:
    antes un login rechazado se veía como un `401 Unauthorized` pelado sobre
    enviar_trafico.php, que no dice nada de lo que realmente pasó."""


def _growi_session_ok(s, url):
    """True/False si la sesión quedó autenticada; None si no se pudo comprobar
    (timeout, red). El None importa: un problema de red no es un login rechazado."""
    try:
        return s.get(f"{url}/paginas/trafico.php", allow_redirects=False,
                     timeout=15).status_code == 200
    except Exception as e:
        print(f"[growi-web] no pude verificar la sesión ({e!r})", flush=True)
        return None


def _growi_login_with(cfg, verify=True):
    """Abre una sesión autenticada contra el CRM con las credenciales dadas.
    Con verify, comprueba que el login haya funcionado de verdad (mismo patrón
    que openAIService/growi_client) y falla fuerte si no."""
    url = cfg.get("crm_url") or GROWI_CRM_URL
    # crm_proxy puede traer VARIOS proxies separados por coma: se prueban en
    # orden y gana el primero que responda. Con uno solo se comporta igual que
    # antes. Ojo: sin este parseo, una lista se pasaría entera como si fuera una
    # única URL de proxy y no conectaría con ninguno.
    pool = ProxyPool(cfg.get("crm_proxy") or "")
    ultimo_error = None

    for proxy in pool.candidatos():
        s = requests.Session()
        s.headers.update({"user-agent": _GROWI_USER_AGENT})
        s.proxies.update(proxies_de(proxy))
        try:
            s.post(
                f"{url}/cuenta/login.php",
                data={"correo": cfg.get("crm_email", ""), "password": cfg.get("crm_password", "")},
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "referer": f"{url}/cuenta/login.php",
                    "origin": url,
                },
                timeout=(5, 15),
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            ultimo_error = e
            pool.marcar_muerto(proxy)
            print(f"[growi-web] proxy caído, pruebo el siguiente ({e.__class__.__name__})",
                  flush=True)
            continue

        estado = _growi_session_ok(s, url)
        if estado is None:
            # No se pudo comprobar por un problema de red: puede ser este proxy.
            ultimo_error = ultimo_error or RuntimeError("sesión no verificable")
            pool.marcar_muerto(proxy)
            continue
        if verify and estado is False:
            # Credenciales rechazadas: cambiar de proxy no arregla nada.
            raise GrowiAuthError(
                f"El CRM rechazó el login de {cfg.get('crm_email') or '(sin email)'}. "
                "Revisá el usuario y la contraseña de Growi en la ficha del vendedor."
            )
        pool.marcar_vivo(proxy)
        return s

    raise requests.exceptions.ConnectionError(
        f"No hay ruta hasta el CRM por ninguno de los proxies configurados "
        f"({ultimo_error.__class__.__name__ if ultimo_error else 'sin detalle'})"
    )


def _growi_validate_credentials(cfg):
    """Chequea las credenciales contra el CRM y distingue los dos "no" posibles:

      "ok"          → el login en Growi es posible (adentro);
      "invalid"     → el CRM nos rebotó: usuario o contraseña incorrectos;
      "unreachable" → no pudimos hablar con el CRM (caído, proxy, timeout). NO es
                      culpa del usuario, así que no lo tratamos como pass errónea.

    Se comprueba pidiendo una página que exige sesión (trafico.php): 200 = adentro;
    un redirect al login = credenciales inválidas. Mismo patrón que
    openAIService/growi_client."""
    url = cfg.get("crm_url") or GROWI_CRM_URL
    try:
        # verify=False: acá el chequeo lo hacemos nosotros y queremos un resultado,
        # no una excepción (esto valida credenciales que el usuario está cargando).
        s = _growi_login_with(cfg, verify=False)
        check = s.get(f"{url}/paginas/trafico.php", allow_redirects=False, timeout=15)
        return "ok" if check.status_code == 200 else "invalid"
    except Exception as e:
        print(f"[auth] no pude hablar con el CRM al validar credenciales ({e!r})", flush=True)
        return "unreachable"


def _get_growi_session(account_id):
    entry = _growi_sessions.get(account_id)
    if entry is None:
        cfg = _account_crm_cfg(account_id)
        entry = {"session": _growi_login_with(cfg), "cfg": cfg}
        _growi_sessions[account_id] = entry
    return entry


def _crm_base(account_id=None):
    """URL base del CRM de la cuenta logueada (para armar referers). Cae al .env."""
    if account_id is None:
        account_id = session.get("account_id")
    if _repo is not None and account_id:
        try:
            cfg = _repo.get_account_crm_config(account_id)
            if cfg and cfg.get("crm_url"):
                return cfg["crm_url"]
        except Exception:
            pass
    return GROWI_CRM_URL


def _growi_sesion_caida(resp) -> bool:
    """¿La respuesta indica que la sesión del CRM murió? Growi NO devuelve 401
    cuando se vence la sesión (su timeout de inactividad, ~20 min): redirige al
    login. Como seguimos el redirect, terminamos en login.php con status 200, y
    antes eso se colaba como si fuera contenido válido — y nadie re-logueaba.
    Detectamos ambos casos: el 401 y el redirect/página de login."""
    if resp.status_code == 401:
        return True
    # Tras seguir redirects, resp.url apunta a login.php si la sesión venció.
    if "login" in (resp.url or "").lower():
        return True
    return False


def _growi_request(method, path, account_id=None, **kwargs):
    """GET/POST autenticado contra el CRM de la cuenta logueada, reintentando con
    login fresco si la sesión murió (401 o redirect al login). account_id
    explícito o el de la sesión."""
    if account_id is None:
        account_id = session.get("account_id")
    timeout = kwargs.pop("timeout", 15)
    entry = _get_growi_session(account_id)
    for intento in range(1, 5):
        url = entry["cfg"].get("crm_url") or GROWI_CRM_URL
        resp = entry["session"].request(
            method, f"{url}{path}", timeout=timeout, **kwargs
        )
        if not _growi_sesion_caida(resp):
            return resp
        print(f"[growi-web] sesión caída ({resp.status_code}, url={resp.url}) en "
              f"intento {intento}/4 para {path} (cuenta {account_id}), relogueando", flush=True)
        _growi_sessions.pop(account_id, None)
        cfg = _account_crm_cfg(account_id)
        entry = {"session": _growi_login_with(cfg), "cfg": cfg}
        _growi_sessions[account_id] = entry
    # Cuatro logins frescos y la sesión sigue sin abrir: no es mala suerte de IP,
    # es que no estamos entrando. Lo decimos con todas las letras.
    raise GrowiAuthError(
        f"El CRM rebotó al login en {path} después de 4 intentos. La sesión de "
        "Growi no se está abriendo: revisá las credenciales del vendedor y el proxy."
    )


def _growi_relogin(account_id=None):
    """Tira la sesión guardada del CRM y abre una nueva. Para los casos que
    _growi_request no detecta como 'sesión caída': el CRM contesta 200 pero con
    HTML/vacío en vez del JSON esperado."""
    if account_id is None:
        account_id = session.get("account_id")
    _growi_sessions.pop(account_id, None)
    cfg = _account_crm_cfg(account_id)
    entry = {"session": _growi_login_with(cfg), "cfg": cfg}
    _growi_sessions[account_id] = entry
    return entry


def _authenticate_db_user(identifier, password):
    """Login local contra la DB, para CUALQUIER rol (admin y vendedor).
    El admin opera sobre varias cuentas (elige el vendedor con ?vendedor=<id>), así
    que su sesión va sin account_id; el vendedor queda atado a su cuenta para que
    las operaciones del CRM y el registro de uso salgan bajo ella."""
    if _repo is None:
        return None
    try:
        u = _repo.authenticate_user(identifier, password)
        if u:
            return {"user_id": u["id"],
                    "account_id": None if u["is_admin"] else u.get("account_id"),
                    "username": u["username"],
                    "is_admin": u["is_admin"]}
    except Exception as e:
        print(f"[auth] DB no disponible, pruebo fallback ({e})", flush=True)
    return None


def _authenticate_admin_fallback(identifier, password):
    """Fallback anti-lockout con las credenciales del .env (si la DB no está o el
    admin no existe todavía)."""
    adm = LEGACY_ADMIN.get(identifier)
    if adm and adm["password"] == password:
        return {"user_id": None, "account_id": None,
                "username": identifier, "is_admin": True}
    return None


# ── Rate limiting del login ──────────────────────────────────────────────────
# En memoria (el web-service es un proceso único). Frena la fuerza bruta / el
# credential stuffing: sin esto, cada intento además golpea EN VIVO al CRM de
# Growi (_authenticate_vendedor valida la password contra el CRM real).
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "8"))
LOGIN_WINDOW_SEC = int(os.environ.get("LOGIN_WINDOW_SEC", "300"))   # 5 min
_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()

MSG_DEMASIADOS = ("Demasiados intentos fallidos. Esperá unos minutos antes de "
                  "volver a probar.")


def _login_rate_key() -> str:
    """Clave por IP + email: no bloquea a toda la oficina por un solo atacante,
    pero sí frena el barrido contra una cuenta o desde una IP."""
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() if fwd else "") or (request.remote_addr or "?")
    email = (request.form.get("username", "") or "").strip().lower()
    return f"{ip}|{email}"


def _login_throttled(key: str) -> bool:
    now = time.time()
    with _login_lock:
        # Purga oportunista para que el dict no crezca sin fin.
        if len(_login_attempts) > 5000:
            for k in [k for k, v in _login_attempts.items()
                      if not v or now - v[-1] > LOGIN_WINDOW_SEC]:
                _login_attempts.pop(k, None)
        hist = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SEC]
        _login_attempts[key] = hist
        return len(hist) >= LOGIN_MAX_ATTEMPTS


def _login_register_fail(key: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(key, []).append(time.time())


def _login_reset(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)


MSG_CREDENCIALES = "Usuario o contraseña incorrectos"
MSG_PENDIENTE = ("Tus credenciales de Growi son correctas, pero tu acceso todavía no "
                 "está habilitado. Enviamos tu solicitud al administrador para que la "
                 "verifique; vas a poder entrar en cuanto la apruebe.")
MSG_RECHAZADO = ("Tu acceso a la plataforma está restringido por el administrador. "
                 "Contactate con él si creés que es un error.")
MSG_CRM_CAIDO = ("No pudimos verificar tus credenciales con Growi en este momento. "
                 "Probá de nuevo en unos minutos.")


def _authenticate_vendedor(email, password):
    """Login del vendedor por credenciales de Growi. Valida la password EN VIVO
    contra el CRM y después mira la habilitación de su cuenta. Devuelve
    (dict_de_sesión|None, mensaje_de_error|None):

      - cuenta aprobada + credenciales válidas → entra;
      - cuenta pendiente o restringida        → no entra, con el mensaje del caso;
      - sin cuenta + credenciales válidas     → se autoregistra como PENDIENTE y
        queda a la espera de que el admin la apruebe desde el panel.
    """
    if _repo is None:
        return None, None
    try:
        acc = _repo.get_account_by_crm_email(email)
    except Exception as e:
        print(f"[auth] no pude buscar la cuenta por email ({e})", flush=True)
        return None, None

    cfg = {
        "crm_url": (acc.get("crm_url") if acc else None) or GROWI_CRM_URL,
        "crm_email": email,
        "crm_password": password,
        "crm_proxy": (acc.get("crm_proxy") if acc else None) or _GROWI_PROXY_URL or None,
    }
    # La solicitud de acceso se manda SOLO si el login en Growi es posible. Si el
    # CRM rebota las credenciales, es contraseña incorrecta y no se registra nada.
    check = _growi_validate_credentials(cfg)
    if check == "unreachable":
        return None, MSG_CRM_CAIDO
    if check != "ok":
        # Credenciales inválidas: no revelamos si la cuenta existe ni en qué estado
        # está; es el mismo mensaje que cualquier login fallido.
        return None, None

    if acc is None:
        # Primer ingreso: se autoregistra y espera la verificación del admin.
        try:
            nueva = _repo.create_pending_vendedor(
                crm_email=email, crm_password=password,
                crm_url=GROWI_CRM_URL, crm_proxy=_GROWI_PROXY_URL or "")
            print(f"[auth] solicitud de acceso creada para {email} (cuenta {nueva['id']})", flush=True)
        except Exception as e:
            print(f"[auth] no pude registrar la solicitud de {email} ({e})", flush=True)
        return None, MSG_PENDIENTE

    estado = acc.get("status") or "approved"
    if estado == "pending":
        return None, MSG_PENDIENTE
    if estado == "rejected" or not acc.get("active"):
        return None, MSG_RECHAZADO

    # Credenciales válidas y cuenta habilitada: refrescamos la password guardada y
    # limpiamos cualquier sesión cacheada vieja de esta cuenta para que las
    # próximas operaciones usen la password recién validada.
    try:
        _repo.update_account_crm_password(acc["id"], password)
    except Exception as e:
        print(f"[auth] no pude refrescar la password del CRM ({e})", flush=True)
    _growi_sessions.pop(acc["id"], None)
    return {"user_id": None, "account_id": acc["id"],
            "username": acc.get("crm_email") or email, "is_admin": False}, None


def _authenticate(identifier, password):
    """Devuelve (dict de sesión|None, mensaje de error|None). Orden: usuario de la
    DB (admin o vendedor) → fallback anti-lockout del .env → vendedor por
    credenciales de Growi (que además resuelve el alta pendiente)."""
    user = _authenticate_db_user(identifier, password)
    if user:
        return user, None
    admin = _authenticate_admin_fallback(identifier, password)
    if admin:
        return admin, None
    user, error = _authenticate_vendedor(identifier.strip().lower(), password)
    return user, error


def _current_user():
    return {
        "user_id": session.get("user_id"),
        "account_id": session.get("account_id"),
        "username": session.get("username"),
        "is_admin": session.get("is_admin", False),
    }


def _tipo_producto(nombre):
    """Clasifica el nombre del producto del CRM en likes/views/shares/reposts/
    saves/reach (o None). Espeja _tipoProducto() del front para que lo registrado
    coincida con lo que la tirada automática consulta después."""
    n = (nombre or "").lower()
    if "like" in n or "me gusta" in n:
        return "likes"
    if any(k in n for k in ("view", "reproduc", "visualiz", "vista")):
        return "views"
    # Reposts antes que shares: en el CRM aparecen como "Reposteos"/"Repost" y
    # algunos nombres los mezclan con "compartir".
    if any(k in n for k in ("repost", "reposte", "requeteo")):
        return "reposts"
    if any(k in n for k in ("save", "guardad", "guardar")):
        return "saves"
    if any(k in n for k in ("reach", "alcance")):
        return "reach"
    # Followers no está en los rangos por cliente (RANGE_KEYS), así que el front
    # no lo clasifica; acá sí, para que el envío quede bien registrado en el uso.
    if any(k in n for k in ("follower", "seguidor")):
        return "followers"
    if "share" in n or "compart" in n:
        return "shares"
    return None


def _log_uso(action, post_url=None, client_ig_username=None, qty=None, product_type=None):
    """Registra una acción del vendedor logueado. Silencioso ante cualquier error."""
    if _repo is None:
        return
    try:
        _repo.log_usage(
            account_id=session.get("account_id"),
            user_id=session.get("user_id"),
            action=action,
            client_ig_username=client_ig_username,
            post_url=post_url,
            qty=qty,
            product_type=product_type,
        )
    except Exception as e:
        print(f"[usage] no pude registrar '{action}' ({e})", flush=True)


def require_login(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autenticado"}), 401
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            return jsonify({"error": "No autenticado"}), 401
        if not session.get("is_admin"):
            return jsonify({"error": "Requiere permisos de administrador"}), 403
        return fn(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    error_kind = "error"
    username = ""
    status = 200
    if request.method == "POST":
        rate_key = _login_rate_key()
        if _login_throttled(rate_key):
            # Ni intentamos autenticar: no gastamos un request contra el CRM.
            username = request.form.get("username", "").strip()
            error, error_kind, status = MSG_DEMASIADOS, "error", 429
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user, auth_error = _authenticate(username, password)
            if user:
                _login_reset(rate_key)   # login OK: no arrastra intentos fallidos
                session.permanent = True  # dura PERMANENT_SESSION_LIFETIME, no muere al cerrar
                session["logged_in"] = True
                session["user_id"] = user["user_id"]
                session["account_id"] = user["account_id"]
                session["username"] = user["username"]
                session["is_admin"] = user["is_admin"]
                return redirect(url_for("index"))
            # Solo cuenta como intento de fuerza bruta la credencial equivocada.
            # Pendiente / restringido / CRM caído son credenciales válidas o un
            # problema nuestro: no penalizan al usuario.
            if auth_error is None:
                _login_register_fail(rate_key)
            # auth_error explica el caso (pendiente de habilitación / acceso
            # restringido); sin él es un login fallido común.
            error = auth_error or MSG_CREDENCIALES
            # "Pendiente" no es un error del usuario: se muestra como aviso.
            error_kind = "info" if error == MSG_PENDIENTE else "error"
    resp = make_response(render_template("login.html", error=error,
                                         error_kind=error_kind, username=username), status)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    resp = make_response(render_template(
        "index.html",
        is_admin=session.get("is_admin", False),
        username=session.get("username", ""),
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/followers")
def followers_page():
    """Generador de followers: pantalla propia, al lado del de comentarios."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    resp = make_response(render_template(
        "followers.html",
        is_admin=session.get("is_admin", False),
        username=session.get("username", ""),
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/ayuda")
def ayuda_page():
    """Guía de uso y preguntas frecuentes. Es la pantalla que se le pasa a todo
    cliente nuevo: estática, sin llamadas a la API, sólo lectura."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template(
        "ayuda.html",
        is_admin=session.get("is_admin", False),
        username=session.get("username", ""),
    )


@app.route("/api/procesar", methods=["POST"])
@require_login
def procesar():
    data = request.get_json()
    post_url = data.get("url", "").strip()
    if not post_url:
        return jsonify({"error": "Falta el link de Instagram"}), 400

    # "Cargar más" manda los comentarios ya generados para que no se repitan.
    evitar = data.get("evitar", []) or []
    # Modo keyword: la tanda es N veces esta palabra (ver ai_generator).
    keyword = (data.get("keyword") or "").strip()

    # Registro de uso: solo la primera tanda (no cada "Cargar más").
    if not evitar:
        _log_uso("generar", post_url=post_url, client_ig_username=data.get("client"))

    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/procesar_post",
            json={"url": post_url, "evitar": evitar, "keyword": keyword,
                  # Para imputar los tokens al vendedor que generó (panel de
                  # gasto). Va la cuenta de la SESIÓN, no ?vendedor=: el gasto
                  # es de quien aprieta el botón.
                  "account_id": session.get("account_id"),
                  "user_id": session.get("user_id")},
            timeout=30,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        print(f"[procesar] error: {e!r}", flush=True)
        return jsonify({"error": _mensaje_amigable(e)}), 502


@app.route("/api/sugerir-keyword", methods=["GET"])
@require_login
def sugerir_keyword():
    """Sugerencia de palabra clave leída del caption del post. Nunca falla: sin
    sugerencia, el vendedor escribe la palabra a mano."""
    try:
        resp = requests.get(
            f"{OPENAI_SERVICE_URL}/sugerir_keyword",
            params={"url": (request.args.get("url") or "").strip()},
            timeout=12,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        print(f"[sugerir-keyword] error: {e!r}", flush=True)
        return jsonify({"keyword": "", "caption": ""})


@app.route("/api/stream/<job_id>", methods=["GET"])
def stream(job_id):
    offset = request.args.get("offset", "0")
    progreso_offset = request.args.get("progreso_offset", "0")
    resets = request.args.get("resets", "0")

    def generate():
        try:
            with requests.get(
                f"{OPENAI_SERVICE_URL}/procesar_post/stream/{job_id}",
                params={"offset": offset, "progreso_offset": progreso_offset, "resets": resets},
                stream=True,
                timeout=300,
            ) as resp:
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
        except Exception as e:
            import json
            print(f"[stream] error: {e!r}", flush=True)
            yield f"data: {json.dumps({'tipo': 'error', 'mensaje': _mensaje_amigable(e)})}\n\n".encode()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/cancelar/<job_id>", methods=["POST"])
@require_login
def cancelar(job_id):
    """El vendedor ya apretó "Publicar seleccionados": cortamos la generación
    que siga en curso para no gastar tokens de más."""
    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/procesar_post/cancelar/{job_id}", timeout=10)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        print(f"[cancelar] error: {e!r}", flush=True)
        return jsonify({"cancelado": False}), 502


@app.route("/api/publicar", methods=["POST"])
@require_login
def publicar():
    data = request.get_json()
    post_url    = data.get("url", "").strip()
    comentarios = data.get("comentarios", [])
    ordenes     = data.get("ordenes", [])
    disponible  = data.get("disponible", DISPONIBLE)

    if not post_url or not comentarios:
        return jsonify({"error": "Faltan datos"}), 400

    _log_uso("publicar", post_url=post_url, client_ig_username=data.get("client"))

    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/publicar",
            # account_id/user_id viajan para que, si la orden termina en la cola
            # de reintentos, quede atribuida al vendedor que la mandó y se pueda
            # ver desde el panel de su cuenta.
            json={"url": post_url, "comentarios": comentarios, "ordenes": ordenes,
                  "disponible": disponible,
                  "account_id": session.get("account_id"),
                  "user_id": session.get("user_id"),
                  "client": data.get("client")},
            timeout=60,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        print(f"[publicar] error: {e!r}", flush=True)
        return jsonify({"error": _mensaje_amigable(e)}), 502


@app.route("/api/nombre_red", methods=["GET"])
@require_login
def nombre_red():
    red_id = request.args.get("red", "1")
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_nombre.php",
            params={"red": red_id},
            headers={"referer": f"{_crm_base()}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/costo_trafico", methods=["POST"])
@require_login
def costo_trafico():
    data = request.get_json()
    try:
        resp = _growi_request(
            "POST", "/paginas/obtenercostotrafico.php",
            json=data,
            headers={
                "referer": f"{_crm_base()}/paginas/trafico.php",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/demora", methods=["GET"])
@require_login
def demora():
    redsocial = request.args.get("redsocial", "")
    producto  = request.args.get("producto", "")
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_demora.php",
            params={"redsocial": redsocial, "producto": producto},
            headers={"referer": f"{_crm_base()}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/productos", methods=["GET"])
@require_login
def productos():
    rrss_id = request.args.get("rrss", "1")

    def _traer():
        resp = _growi_request(
            "GET", "/paginas/obtener_productos_con_precios.php",
            params={"rrss": rrss_id},
            headers={"referer": f"{_crm_base()}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return resp.json()   # si el CRM devolvió HTML (sesión rara), esto revienta

    try:
        return jsonify(_traer())
    except Exception as e:
        # Segundo intento con login fresco: el CRM a veces contesta 200 con una
        # página en vez del JSON, y eso _growi_request no lo ve como sesión caída.
        print(f"[growi-web] productos falló ({e}); relogueo y reintento", flush=True)
        try:
            _growi_relogin()
            return jsonify(_traer())
        except Exception as e2:
            return jsonify({"error": str(e2)}), 500


@app.route("/api/server_time_ar", methods=["GET"])
@require_login
def server_time_ar():
    import random
    try:
        resp = _growi_request(
            "GET", "/paginas/server_time_ar.php",
            params={"_": random.random()},
            headers={"referer": f"{_crm_base()}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/enviar_trafico", methods=["POST"])
@require_login
def enviar_trafico():
    data = request.get_json()
    ordenes = data.get("ordenes", [])
    if not ordenes:
        return jsonify({"error": "Sin órdenes"}), 400

    # Registramos cada orden con su cantidad y tipo (likes/views/shares) para que
    # la tirada automática no vuelva a proponer un número ya enviado a ese cliente.
    cliente_ig = data.get("client")
    for o in ordenes:
        try:
            qty = int(o.get("cantidad") or 0) or None
        except (TypeError, ValueError):
            qty = None
        _log_uso("enviar_trafico", post_url=data.get("url"), client_ig_username=cliente_ig,
                 qty=qty, product_type=_tipo_producto(o.get("prod") or ""))

    # De qué campaña salen los FONDOS: la asignada al cliente, si no la última
    # campaña de su propio perfil, y recién si no hay ninguna la de por defecto.
    # Antes iba fija la del .env y todo el tráfico se descontaba de la misma.
    # idventa: cuando el post no es de ningún cliente, el front deja elegir a
    # mano de cuál de las campañas propias sale la plata; esa elección manda.
    cfg = _account_crm_cfg(session.get("account_id"))
    fondos = resolver_venta(session.get("account_id"), cliente_ig,
                            idventa_elegida=data.get("idventa"))
    idventa, idvendedor = fondos["idventa"], fondos["idvendedor"]
    print(f"[fondos] @{cliente_ig or '—'} → idventa {idventa} "
          f"({fondos['origen']}: {fondos['detalle']}, saldo {fondos['saldo']})", flush=True)
    # El disponible que informamos al CRM es el saldo REAL de esa campaña; el
    # valor de config queda como respaldo si no se pudo leer.
    if fondos["saldo"] is not None:
        disponible_default = fondos["saldo"]
    else:
        try:
            disponible_default = float(cfg.get("crm_disponible") or DISPONIBLE)
        except (TypeError, ValueError):
            disponible_default = DISPONIBLE
    crm_base = _crm_base()

    # Obtener fecha/hora del servidor en AR
    try:
        import random
        ts_resp = _growi_request(
            "GET", "/paginas/server_time_ar.php",
            params={"_": random.random()},
            headers={"referer": f"{crm_base}/paginas/trafico.php"},
        )
        ts_data = ts_resp.json()
        fecha_ar = ts_data.get("ymdhmAR", "")[:10]  # "2026-06-25"
    except Exception:
        from datetime import date as _date
        fecha_ar = _date.today().isoformat()

    costo_total = sum(float(o.get("costo", 0)) for o in ordenes)
    disponible  = float(data.get("disponible", disponible_default))

    for o in ordenes:
        o["disponible"] = disponible

    payload = {
        "idvendedor": idvendedor,
        "idventa":    idventa,
        "fecha":      fecha_ar,
        "vendedor":   " ",
        "cant_enviada": 0,
        "aprobada":   "Aprobado",
        "ordenes":    ordenes,
        "creador":    idvendedor,
        "disponible": disponible,
        "resto":      round(disponible - costo_total, 6),
        "costo_orden": round(costo_total, 6),
    }

    req_headers = {
        "referer":      f"{crm_base}/paginas/trafico.php",
        "content-type": "application/json; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
    }

    try:
        resp = _growi_request(
            "POST", "/paginas/enviar_trafico.php",
            json=payload,
            headers=req_headers,
        )
        print(f"[enviar_trafico] respuesta CRM ({resp.status_code}): {resp.text[:2000]}", flush=True)
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "application/json")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Menú de admin self-serve (TAREA 3) ──────────────────────────────────────────

def _repo_error_response(fn):
    """Traduce RepoError (validación de negocio) a 400 con mensaje claro."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if _repo is None:
            return jsonify({"error": "Base de datos no disponible"}), 503
        try:
            return fn(*a, **kw)
        except _repo.RepoError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            print(f"[admin] error: {e!r}", flush=True)
            return jsonify({"error": "Error interno"}), 500
    return wrapper


@app.route("/admin")
@require_admin
def admin_page():
    resp = make_response(render_template("admin.html", username=session.get("username", ""),
                                         is_admin=True, account_id=session.get("account_id") or ""))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/mis-clientes")
@require_login
def mis_clientes_page():
    """Los VENDEDORES gestionan SOLO sus propios clientes: misma UI que el admin
    pero en modo acotado (sin selector de vendedor ni pestañas de usuarios/
    vendedores/uso). El scoping a su cuenta lo garantiza _target_account_id."""
    resp = make_response(render_template("admin.html", username=session.get("username", ""),
                                         is_admin=False, account_id=session.get("account_id") or ""))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


def _target_account_id():
    """Cuenta sobre la que se opera. El VENDEDOR (no admin) siempre trabaja sobre
    SU propia cuenta: ignora el ?vendedor, así no puede tocar clientes de otras
    cuentas. El ADMIN elige la cuenta por query string (?vendedor=<id>) o el body."""
    if not session.get("is_admin"):
        acc = session.get("account_id")
        if acc:
            return acc
        raise _repo.RepoError("Cuenta no disponible")
    raw = (request.args.get("vendedor")
           or (request.get_json(silent=True) or {}).get("account_id")
           or (request.get_json(silent=True) or {}).get("vendedor"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise _repo.RepoError("Falta indicar el vendedor")


# --- Vendedores (cuentas) ---
@app.route("/api/admin/vendedores", methods=["GET"])
@require_admin
@_repo_error_response
def admin_vendedores_list():
    return jsonify({"vendedores": _repo.list_vendedores()})


@app.route("/api/admin/vendedores", methods=["POST"])
@require_admin
@_repo_error_response
def admin_vendedores_create():
    d = request.get_json(silent=True) or {}
    v = _repo.create_vendedor(
        name=d.get("name", ""),
        crm_email=d.get("crm_email", ""),
        crm_password=d.get("crm_password", ""),
        crm_url=d.get("crm_url", ""),
        crm_idvendedor=d.get("crm_idvendedor", ""),
        crm_idventa=d.get("crm_idventa", ""),
        crm_proxy=d.get("crm_proxy", ""),
        crm_disponible=d.get("crm_disponible", ""),
    )
    return jsonify({"vendedor": v}), 201


@app.route("/api/admin/vendedores/<int:account_id>", methods=["PATCH"])
@require_admin
@_repo_error_response
def admin_vendedores_update(account_id):
    d = request.get_json(silent=True) or {}
    # Campos presentes en el body se actualizan; ausentes quedan igual.
    kwargs = {k: d[k] for k in (
        "name", "active", "crm_email", "crm_password", "crm_url",
        "crm_idvendedor", "crm_idventa", "crm_proxy", "crm_disponible",
    ) if k in d}
    v = _repo.update_vendedor(account_id, **kwargs)
    # Si cambiaron credenciales, la sesión CRM cacheada de esa cuenta quedó vieja.
    _growi_sessions.pop(account_id, None)
    return jsonify({"vendedor": v})


@app.route("/api/admin/vendedores/<int:account_id>/estado", methods=["PATCH"])
@require_admin
@_repo_error_response
def admin_vendedores_estado(account_id):
    """Resuelve una solicitud de acceso: {"status": "approved"|"rejected"}. Es lo
    que el admin toca en el panel cuando le llega un vendedor nuevo."""
    d = request.get_json(silent=True) or {}
    v = _repo.set_vendedor_status(account_id, (d.get("status") or "").strip())
    _growi_sessions.pop(account_id, None)
    return jsonify({"vendedor": v})


# --- Usuarios / logins (scoped al vendedor elegido: ?vendedor=<account_id>) ---
# Cada usuario es un login propio = un asiento. Sin esto no se pueden dar de alta
# ni de baja vendedores desde el panel (había que tocar código y redeployar).
@app.route("/api/admin/usuarios", methods=["GET"])
@require_admin
@_repo_error_response
def admin_usuarios_list():
    return jsonify({"usuarios": _repo.list_users(_target_account_id())})


@app.route("/api/admin/usuarios", methods=["POST"])
@require_admin
@_repo_error_response
def admin_usuarios_create():
    d = request.get_json(silent=True) or {}
    u = _repo.create_user(
        _target_account_id(),
        username=d.get("username", ""),
        password=d.get("password", ""),
        role=d.get("role", "vendedor"),
    )
    return jsonify({"usuario": u}), 201


@app.route("/api/admin/usuarios/<int:user_id>", methods=["PATCH"])
@require_admin
@_repo_error_response
def admin_usuarios_update(user_id):
    d = request.get_json(silent=True) or {}
    u = _repo.update_user(
        _target_account_id(), user_id,
        active=d.get("active"),
        role=d.get("role"),
        password=d.get("password"),
    )
    return jsonify({"usuario": u})


# --- Clientes (scoped al vendedor elegido: ?vendedor=<account_id>) ---
# ── Campañas / ventas del CRM (de dónde salen los fondos) ───────────────────────
# El CRM no expone un JSON: la grilla del paso 1 de trafico.php se puebla con
# POST /paginas/traer_campanas.php (antiguas=0 activas, =1 también las viejas) y
# devuelve HTML con un botón por campaña que trae todo en data-attributes.
# Ya viene acotado al vendedor logueado, así que cada cuenta ve solo las suyas.
# OJO: el CRM mezcla comillas simples y dobles en esos atributos.
_VENTA_BTN_RE = re.compile(
    r"""<button[^>]*seleccionar-cliente[^>]*>""", re.I)
_VENTA_ATTR_RE = re.compile(r"""(data-[\w-]+)\s*=\s*["']([^"']*)["']""")


def _parse_ventas(html):
    ventas, vistos = [], set()
    for btn in _VENTA_BTN_RE.findall(html):
        a = dict(_VENTA_ATTR_RE.findall(btn))
        idventa = (a.get("data-id") or "").strip()
        if not idventa or idventa in vistos:
            continue
        vistos.add(idventa)
        ventas.append({
            "idventa": idventa,
            "idvendedor": (a.get("data-idvendedor") or "").strip(),
            "nombre": (a.get("data-nombre") or "").strip(),
            "correo": (a.get("data-correo") or "").strip(),
            "vendedor": (a.get("data-vendedor") or "").strip(),
            "estado": (a.get("data-estadoventa") or "").strip(),
            "disponible": (a.get("data-cantidad-disponible") or "").strip(),
            "monto": (a.get("data-monto") or "").strip(),
            "fecha": (a.get("data-fecha") or "").strip(),
        })
    # Más saldo primero: es lo que se mira al elegir de dónde sacar los fondos.
    ventas.sort(key=_venta_saldo, reverse=True)
    return ventas


def _venta_saldo(v):
    try:
        return float(v.get("disponible") or 0)
    except (TypeError, ValueError):
        return 0.0


# A qué perfil de IG pertenece cada campaña. El nombre del CRM no sirve para
# agrupar ("Peter J Fouernier" / "Peter Fournier" / "Peter Fouernier" son la misma
# persona), pero editarv.php trae el cliente_url, que es el perfil real. Una
# campaña no cambia de perfil, así que se cachea sin vencimiento.
_VENTA_IG_CACHE = {}
_IG_URL_RE = re.compile(r"instagram\.com/+([^/?#\s]+)", re.I)


def _venta_ig_username(account_id, idventa):
    key = (account_id, str(idventa))
    if key in _VENTA_IG_CACHE:
        return _VENTA_IG_CACHE[key]
    ig = ""
    try:
        resp = _growi_request(
            "GET", f"/paginas/editarv.php?idv={idventa}", account_id=account_id, timeout=20,
            headers={
                "referer": f"{_crm_base(account_id)}/paginas/ventas.php",
                "x-requested-with": "XMLHttpRequest",
            },
        )
        if resp.status_code == 200:
            m = _IG_URL_RE.search((resp.json() or {}).get("cliente_url") or "")
            if m:
                ig = m.group(1).strip().lower()
    except Exception as e:
        print(f"[ventas] no pude resolver el perfil de la campaña {idventa}: {e!r}", flush=True)
    _VENTA_IG_CACHE[key] = ig
    return ig


def _agregar_ig_a_ventas(account_id, ventas):
    """Completa el @usuario de cada campaña. En paralelo porque son 20-120
    pedidos y en serie la pantalla tardaba demasiado en abrir."""
    from concurrent.futures import ThreadPoolExecutor
    pendientes = [v for v in ventas if (account_id, v["idventa"]) not in _VENTA_IG_CACHE]
    if pendientes:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda v: _venta_ig_username(account_id, v["idventa"]), pendientes))
    for v in ventas:
        v["ig_username"] = _VENTA_IG_CACHE.get((account_id, v["idventa"]), "")
    return ventas


# Listado de campañas por cuenta. TTL corto: el saldo cambia con cada envío, pero
# pedirlo en cada request costaría los ~120 pedidos de resolución de perfil.
_VENTAS_CACHE = {}
_VENTAS_TTL = 300


def _traer_ventas(account_id, usar_cache=True):
    import time
    entry = _VENTAS_CACHE.get(account_id)
    if usar_cache and entry and (time.time() - entry[0]) < _VENTAS_TTL:
        return entry[1]

    def _post(antiguas):
        resp = _growi_request(
            "POST", "/paginas/traer_campanas.php", account_id=account_id, timeout=60,
            data={"antiguas": antiguas},
            headers={
                "referer": f"{_crm_base(account_id)}/paginas/trafico.php",
                "x-requested-with": "XMLHttpRequest",
            },
        )
        resp.raise_for_status()
        return _parse_ventas(resp.text)

    activas = _post("0")
    ids_activas = {v["idventa"] for v in activas}
    por_id = {v["idventa"]: v for v in _post("1")}
    for v in activas:
        por_id.setdefault(v["idventa"], v)
    ventas = list(por_id.values())
    for v in ventas:
        v["activa"] = v["idventa"] in ids_activas
    _agregar_ig_a_ventas(account_id, ventas)
    ventas.sort(key=lambda v: (not v["activa"], -_venta_saldo(v)))
    _VENTAS_CACHE[account_id] = (time.time(), ventas)
    return ventas


def _ultima_campana(ventas, ig_username):
    """La campaña MÁS RECIENTE del perfil. Se prefiere entre las activas; si el
    cliente no tiene ninguna activa, se cae a la última histórica."""
    ig = (ig_username or "").strip().lstrip("@").lower()
    if not ig:
        return None
    propias = [v for v in ventas if v.get("ig_username") == ig]
    if not propias:
        return None
    activas = [v for v in propias if v.get("activa")]
    pool = activas or propias
    return max(pool, key=lambda v: (v.get("fecha") or "", _venta_saldo(v)))


def resolver_venta(account_id, ig_username, idventa_elegida=None):
    """De dónde sale la plata para este cliente, en orden:
       0) la campaña elegida a mano en el envío (posts sin cliente),
       1) la campaña asignada a mano en Mis clientes,
       2) la ÚLTIMA campaña de su propio perfil de IG (lo normal),
       3) la campaña por defecto de la cuenta / .env — solo si no hay match.
    Devuelve dict con idventa, idvendedor, origen, detalle y saldo (None si no
    se pudo leer la campaña)."""
    ig = (ig_username or "").strip().lstrip("@").lower()
    cfg = _account_crm_cfg(account_id)
    default = {
        "idventa": cfg.get("crm_idventa") or GROWI_IDVENTA,
        "idvendedor": cfg.get("crm_idvendedor") or GROWI_IDVENDEDOR,
        "origen": "default",
        "detalle": "campaña por defecto de la cuenta",
        "saldo": None,
    }

    ventas = []
    try:
        ventas = _traer_ventas(account_id)
    except Exception as e:
        print(f"[fondos] no pude leer las campañas ({e!r}); uso la de por defecto", flush=True)

    # 0) Elegida a mano en este envío. Se valida contra las campañas de la cuenta
    #    para que nadie pueda descontarle a una venta que no es suya.
    elegida = str(idventa_elegida or "").strip()
    if elegida:
        v = next((x for x in ventas if x["idventa"] == elegida), None)
        if v:
            return {
                "idventa": v["idventa"],
                "idvendedor": v["idvendedor"] or default["idvendedor"],
                "origen": "elegida",
                "detalle": f"campaña #{v['idventa']} ({v['nombre']}) elegida en el envío",
                "saldo": _venta_saldo(v),
            }
        print(f"[fondos] la campaña elegida #{elegida} no es de esta cuenta; sigo con la resolución normal", flush=True)

    # 1) Asignación manual: manda siempre, pero solo si la campaña sigue existiendo.
    if _repo is not None and ig:
        try:
            cli = _repo.get_client_by_ig_username(ig, account_id)
        except Exception:
            cli = None
        manual = (cli or {}).get("crm_idventa") or ""
        if manual:
            v = next((x for x in ventas if x["idventa"] == manual), None)
            if v or not ventas:
                return {
                    "idventa": manual,
                    "idvendedor": (v or {}).get("idvendedor") or (cli or {}).get("crm_idvendedor") or default["idvendedor"],
                    "origen": "manual",
                    "detalle": f"campaña #{manual} asignada al cliente",
                    "saldo": _venta_saldo(v) if v else None,
                }
            print(f"[fondos] la campaña #{manual} de @{ig} ya no existe; busco la última", flush=True)

    # 2) Última campaña del propio perfil.
    v = _ultima_campana(ventas, ig)
    if v:
        return {
            "idventa": v["idventa"],
            "idvendedor": v["idvendedor"] or default["idvendedor"],
            "origen": "auto",
            "detalle": f"última campaña de @{ig} (#{v['idventa']}, {v['nombre']})",
            "saldo": _venta_saldo(v),
        }

    # 3) Sin similitudes: la de por defecto.
    return default


@app.route("/api/ventas", methods=["GET"])
@require_login
def ventas_crm():
    """Campañas del CRM de la cuenta logueada, con su saldo disponible y el perfil
    de IG al que pertenecen. El front las usa para asignarle a cada cliente de qué
    campaña salen sus fondos, y para poder ir cambiando entre las suyas.

    Trae SIEMPRE activas + antiguas: cada campaña viene marcada con "activa", para
    que se vea el saldo remanente de las viejas sin perder de vista cuál es la
    vigente. ?ig=<usuario> filtra las de un perfil."""
    try:
        account_id = _target_account_id() if session.get("is_admin") else session.get("account_id")
    except Exception:
        account_id = session.get("account_id")

    try:
        ventas = _traer_ventas(account_id, usar_cache=request.args.get("fresh") != "1")
        ig = (request.args.get("ig") or "").strip().lstrip("@").lower()
        if ig:
            ventas = [v for v in ventas if v["ig_username"] == ig]
        return jsonify({"ventas": ventas})
    except Exception as e:
        print(f"[ventas] error leyendo traer_campanas.php: {e!r}", flush=True)
        return jsonify({"error": _mensaje_amigable(e), "ventas": []}), 502


@app.route("/api/admin/clients", methods=["GET"])
@require_login
@_repo_error_response
def admin_clients_list():
    clients = _repo.list_clients(_target_account_id())
    # El genérico no pertenece a ninguna cuenta a los fines del panel: es del
    # sistema, se aplica a los posts sin cliente de TODOS los vendedores y solo
    # el admin lo ve (marcado como reservado, sin borrar/pausar/renombrar).
    if session.get("is_admin"):
        for c, extra in ((_repo.get_generic_client(), _SISTEMA[_repo.GENERIC_IG]),
                         (_repo.get_keyword_client(), _SISTEMA[_repo.KEYWORD_IG])):
            if c:
                clients = clients + [{**c, "reserved": True, **extra}]
    return jsonify({"clients": clients})


# Textos de las fichas del sistema (las dos las edita solo el admin). Viven acá
# y no en el JS para que la ficha nueva no obligue a tocar el front.
_SISTEMA = {
    "__generico__": {
        "display_name": "Genéricos (posts sin cliente)",
        "system_icon": "🌐",
        "system_desc": "Se aplica a todo post que no sea de un cliente cargado, en todos los vendedores",
        "system_title": "Prompt de los posts sin cliente",
        "system_sub": "Es el prompt que se usa en TODOS los vendedores cuando el post no es de un cliente cargado. Se edita solo desde acá.",
    },
    "__keyword__": {
        "display_name": "Palabra clave (modo keyword)",
        "system_icon": "🔑",
        "system_desc": "Los comentarios que son una sola palabra repetida (CLAUDE / Claude / claude), en todos los vendedores",
        "system_title": "Prompt del modo palabra clave",
        "system_sub": "Es el prompt que se usa cuando el vendedor activa \"Comentarios de palabra clave\". No se le suma el genérico ni el prompt del cliente: es autónomo. Usá {keyword} donde va la palabra.",
    },
}


@app.route("/api/admin/clients", methods=["POST"])
@require_login
@_repo_error_response
def admin_clients_create():
    d = request.get_json(silent=True) or {}
    c = _repo.create_client(
        _target_account_id(),
        ig_username=d.get("ig_username", ""),
        display_name=d.get("display_name", ""),
        prompt=d.get("prompt", ""),
        status=d.get("status", "active"),
        gender=d.get("gender"),
        quality=d.get("quality"),
        ranges=d.get("ranges"),
        crm_idventa=d.get("crm_idventa"),
        crm_idvendedor=d.get("crm_idvendedor"),
        keyword_mode=d.get("keyword_mode"),
    )
    return jsonify({"client": c}), 201


@app.route("/api/admin/clients/<int:client_id>", methods=["PATCH"])
@require_login
@_repo_error_response
def admin_clients_update(client_id):
    d = request.get_json(silent=True) or {}
    # Las fichas del sistema (genérico y keyword) se editan por su propio camino:
    # solo el prompt y los ajustes, nunca el @usuario ni el estado.
    sistema = {c["id"]: c["ig_username"]
               for c in (_repo.get_generic_client(), _repo.get_keyword_client()) if c}
    if client_id in sistema:
        if not session.get("is_admin"):
            return jsonify({"error": "Requiere permisos de administrador"}), 403
        c = _repo.update_system_client(
            sistema[client_id],
            prompt=d.get("prompt"),
            gender=d.get("gender"), gender_set=("gender" in d),
            quality=d.get("quality"), quality_set=("quality" in d),
            ranges=d.get("ranges"), ranges_set=("ranges" in d),
        )
        return jsonify({"client": {**c, "reserved": True,
                                   **_SISTEMA.get(sistema[client_id], {})}})
    c = _repo.update_client(
        _target_account_id(), client_id,
        display_name=d.get("display_name"),
        prompt=d.get("prompt"),
        status=d.get("status"),
        ig_username=d.get("ig_username"),
        gender=d.get("gender"),
        gender_set=("gender" in d),
        quality=d.get("quality"),
        quality_set=("quality" in d),
        ranges=d.get("ranges"),
        ranges_set=("ranges" in d),
        crm_idventa=d.get("crm_idventa"),
        crm_idvendedor=d.get("crm_idvendedor"),
        keyword_mode=d.get("keyword_mode"),
        keyword_mode_set=("keyword_mode" in d),
    )
    return jsonify({"client": c})


@app.route("/api/admin/clients/<int:client_id>", methods=["DELETE"])
@require_login
@_repo_error_response
def admin_clients_delete(client_id):
    sistema = [c["id"] for c in (_repo.get_generic_client(), _repo.get_keyword_client()) if c]
    if client_id in sistema:
        return jsonify({"error": "Es una ficha del sistema y no se puede borrar"}), 400
    ok = _repo.delete_client(_target_account_id(), client_id)
    return jsonify({"deleted": ok})


# ── Pedidos de ajuste de prompt ──────────────────────────────────────────────
# El vendedor deja el pedido escrito (texto plano, sin IA: no gasta tokens) y el
# admin lo resuelve desde /admin con el asistente de IA, que es admin-only.

@app.route("/api/prompt-requests", methods=["POST"])
@require_login
@_repo_error_response
def prompt_requests_create():
    d = request.get_json(silent=True) or {}
    try:
        client_id = int(d.get("client_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Falta indicar el cliente"}), 400
    p = _repo.create_prompt_request(
        _target_account_id(), client_id,
        user_id=session.get("user_id"),
        username=session.get("username", ""),
        text=d.get("text", ""),
    )
    return jsonify({"request": p}), 201


@app.route("/api/prompt-requests", methods=["GET"])
@require_login
@_repo_error_response
def prompt_requests_list():
    # El admin ve la cola de TODAS las cuentas; el vendedor solo la suya.
    account_id = None if session.get("is_admin") else session.get("account_id")
    status = (request.args.get("status") or "").strip() or None
    return jsonify({"requests": _repo.list_prompt_requests(account_id, status)})


@app.route("/api/prompt-requests/<int:request_id>", methods=["PATCH"])
@require_admin
@_repo_error_response
def prompt_requests_update(request_id):
    d = request.get_json(silent=True) or {}
    p = _repo.set_prompt_request_status(
        request_id, (d.get("status") or "").strip(),
        resolved_by=session.get("username", ""),
    )
    return jsonify({"request": p})


# ── Cola de órdenes pendientes de envío al CRM ───────────────────────────────

@app.route("/api/ordenes-pendientes", methods=["GET"])
@require_login
@_repo_error_response
def ordenes_pendientes_list():
    """Órdenes que no pudieron salir al CRM y su estado en la cola de reintentos.

    Sin esta vista, una orden que queda en 'revisar' o 'fallida' es invisible:
    el worker deja de tocarla y nadie se entera hasta que el cliente reclama.
    """
    # El admin ve la cola de TODAS las cuentas; el vendedor solo la suya.
    account_id = None if session.get("is_admin") else session.get("account_id")
    estados = [e for e in (request.args.get("estados") or "").split(",") if e]
    return jsonify({
        "ordenes": _repo.list_pending_orders(account_id, estados or None),
        "conteo": _repo.contar_ordenes_en_cola(),
    })


@app.route("/api/ordenes-pendientes/<int:orden_id>/cancelar", methods=["POST"])
@require_login
@_repo_error_response
def ordenes_pendientes_cancelar(orden_id):
    """Baja manual: el vendedor ya la cargó a mano en Growi, o no la quiere más."""
    account_id = None if session.get("is_admin") else session.get("account_id")
    ok = _repo.cancelar_orden(orden_id, account_id)
    if not ok:
        return jsonify({"error": "No se pudo cancelar (ya salió, o la está enviando el worker)"}), 400
    return jsonify({"ok": True})


# ── Asistente de IA para reescribir prompts (ADMIN ONLY) ─────────────────────
# Está deliberadamente detrás de @require_admin: si cada vendedor pudiera pedirle
# a Claude que le reescriba el prompt, el consumo de tokens se dispara. Los
# vendedores piden por escrito (arriba) y el admin es el único que dispara la IA.

# El prompt final que ve el motor son DOS CAPAS: el genérico (reglas de oficio
# comunes a todos los clientes) + el prompt del cliente (solo lo propio). Ver
# openAIService/modules/ai_generator.py. El asistente escribe SOLO la capa del
# cliente: si repitiera las reglas base, un arreglo global en el genérico volvería
# a quedar pisado cliente por cliente, que es justo lo que queremos evitar.
_AI_SYSTEM = """Sos un asistente que edita prompts de generación de comentarios
de Instagram para una agencia de engagement.

El prompt que finalmente ve el modelo se arma en dos capas:
1. REGLAS GENERALES: valen para todos los clientes (largos, mayúsculas, emojis,
   cómo no sonar a bot). Se editan en otro lado, NO son tu salida.
2. INSTRUCCIONES DEL CLIENTE: lo propio de esta cuenta. ESTO es lo que escribís.

Recibís las reglas generales (como contexto), las instrucciones actuales del
cliente y un PEDIDO en castellano rioplatense. Devolvés las INSTRUCCIONES DEL
CLIENTE completas y ya modificadas.

Reglas:
- Devolvé SOLO el texto de las instrucciones del cliente. Sin explicaciones, sin
  comentarios, sin markdown de code fence, sin encabezados tipo "Prompt nuevo:".
- NO repitas ni reformules las reglas generales: ya se aplican solas. Escribí
  únicamente lo específico de este cliente.
- Si el pedido contradice a propósito una regla general (ej: "para este cliente
  todo en minúscula"), SÍ escribilo: la capa del cliente tiene prioridad.
- Aplicá el pedido y NADA más: conservá intacto todo lo que no se pidió cambiar
  (rubro, personajes, @menciones permitidas, tono, idioma, ejemplos).
- Si las instrucciones del cliente vienen vacías, escribí solo lo que el pedido
  necesite; no rellenes con generalidades.
- Mantené el mismo idioma en que están escritas las instrucciones.
"""

# Cuando lo que se edita es el genérico mismo no hay capa de arriba: ahí sí se
# escribe el prompt completo.
_AI_SYSTEM_GENERIC = """Sos un asistente que edita el PROMPT GENERAL de una
agencia de engagement: las reglas de generación de comentarios de Instagram que
aplican a TODOS los clientes (largos, mayúsculas, emojis, cómo no sonar a bot).

Recibís el prompt general actual y un PEDIDO en castellano rioplatense sobre qué
cambiar. Devolvés el prompt general completo y ya modificado.

Reglas:
- Devolvé SOLO el texto del prompt. Sin explicaciones, sin comentarios, sin
  markdown de code fence, sin encabezados tipo "Prompt nuevo:".
- Aplicá el pedido y NADA más: conservá intacto todo lo que no se pidió cambiar.
- Escribí reglas GENERALES: nada de un cliente puntual, su rubro o sus @menciones.
- Mantené el prompt en el mismo idioma en que está escrito.
"""


# El prompt del modo keyword tampoco tiene capa de arriba, pero no es un prompt
# de "cómo comentar": es un prompt de una sola palabra repetida. Con el asistente
# del genérico, los pedidos terminaban agregándole reglas de largos y emojis.
_AI_SYSTEM_KEYWORD = """Sos un asistente que edita el PROMPT DEL MODO PALABRA
CLAVE de una agencia de engagement. Ese modo genera comentarios que son SOLO una
palabra clave repetida (CLAUDE / Claude / claude), variando mayúsculas y
minúsculas, como los que deja la gente para que el bot del creador le mande un
recurso.

Recibís el prompt actual y un PEDIDO en castellano rioplatense sobre qué
cambiar. Devolvés el prompt completo y ya modificado.

Reglas:
- Devolvé SOLO el texto del prompt. Sin explicaciones, sin comentarios, sin
  markdown de code fence, sin encabezados tipo "Prompt nuevo:".
- Aplicá el pedido y NADA más: conservá intacto todo lo que no se pidió cambiar.
- NO agregues reglas de comentarios normales (largos variados, emojis, slang,
  personalidades, comentar el contenido del post): en este modo cada comentario
  es únicamente la palabra clave.
- Conservá el marcador {keyword}: es donde el sistema mete la palabra de la tanda.
- Mantené el prompt en el mismo idioma en que está escrito.
"""


def _anthropic_client():
    """Cliente de Anthropic para el asistente. None si falta la API key."""
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        print("[prompt-ai] falta el paquete 'anthropic' en webService", flush=True)
        return None
    return anthropic.Anthropic(api_key=key, max_retries=3)


@app.route("/api/admin/prompt-ai", methods=["POST"])
@require_admin
def prompt_ai():
    """Reescribe un prompt según una instrucción en castellano.

    NO guarda nada: devuelve la propuesta para que el admin la revise en el
    editor y recién ahí apriete Guardar. Así una instrucción mal entendida
    nunca pisa el prompt en producción.
    """
    d = request.get_json(silent=True) or {}
    instruccion = (d.get("instruction") or "").strip()
    actual = d.get("prompt") or ""
    if not instruccion:
        return jsonify({"error": "Escribí qué querés cambiar"}), 400
    if len(instruccion) > 4000:
        return jsonify({"error": "El pedido es demasiado largo"}), 400

    client = _anthropic_client()
    if client is None:
        return jsonify({"error": "El asistente no está configurado "
                                 "(falta ANTHROPIC_API_KEY en el web-service)"}), 503

    # Editando el genérico no hay capa de arriba; editando un cliente, sí: se le
    # pasa el genérico como contexto para que NO lo repita.
    es_generico = bool(d.get("is_generic"))
    nombre = (d.get("client_name") or "").strip()
    if es_generico:
        sistema = (_AI_SYSTEM_KEYWORD
                   if (d.get("system_key") or "") == "__keyword__"
                   else _AI_SYSTEM_GENERIC)
        user_msg = (
            f"PROMPT GENERAL ACTUAL:\n<<<\n{actual}\n>>>\n\n"
            f"PEDIDO:\n<<<\n{instruccion}\n>>>\n\n"
            "Devolvé el prompt general completo ya modificado, sin nada alrededor."
        )
    else:
        base = ""
        if _repo is not None:
            try:
                base = _repo.get_generic_prompt() or ""
            except Exception as e:
                print(f"[prompt-ai] no pude leer el prompt genérico ({e})", flush=True)
        sistema = _AI_SYSTEM
        contexto = f"Cliente: {nombre}\n\n" if nombre else ""
        bloque_base = (f"REGLAS GENERALES (contexto: ya se aplican solas, NO las repitas "
                       f"en tu salida):\n<<<\n{base}\n>>>\n\n") if base.strip() else ""
        user_msg = (
            f"{contexto}{bloque_base}"
            f"INSTRUCCIONES ACTUALES DEL CLIENTE:\n<<<\n{actual}\n>>>\n\n"
            f"PEDIDO:\n<<<\n{instruccion}\n>>>\n\n"
            "Devolvé las instrucciones del cliente completas y ya modificadas, "
            "sin nada alrededor."
        )
    try:
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=16000,
            system=sistema,
            messages=[{"role": "user", "content": user_msg}],
            # Igual que en ai_generator: el SDK pineado (anthropic 0.54.0) no
            # expone estos kwargs, así que van por extra_body.
            extra_body={"output_config": {"effort": "high"}},
        )
    except Exception as e:
        print(f"[prompt-ai] error llamando a Claude: {e!r}", flush=True)
        return jsonify({"error": "No pude generar la propuesta. Probá de nuevo."}), 502

    # Opus 5 puede rechazar por políticas: devuelve 200 con stop_reason refusal
    # y content vacío. Sin este chequeo, el front pisaría el prompt con "".
    if getattr(resp, "stop_reason", None) == "refusal":
        return jsonify({"error": "El asistente no puede procesar ese pedido. "
                                 "Reformulalo o editá el prompt a mano."}), 422

    texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not texto:
        return jsonify({"error": "El asistente devolvió una respuesta vacía. Probá de nuevo."}), 502
    # Por si igual devuelve el prompt envuelto en un code fence.
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\n?", "", texto)
        texto = re.sub(r"\n?```$", "", texto).strip()
    return jsonify({"prompt": texto})


# ── Envío de followers (pantalla /followers) ────────────────────────────────
# La orden en sí sale por /api/enviar_trafico, igual que cualquier otro producto;
# acá solo va lo propio de la pantalla: resolver el @usuario del link y listar
# los clientes de la cuenta para saber de qué campaña salen los fondos.


def _followers_clients():
    """Clientes para el selector de la pantalla. El vendedor ve los suyos; el
    ADMIN ve los de TODOS los vendedores (necesita poder probar cualquiera), con
    el nombre del vendedor al lado para saber de quién es cada uno."""
    if _repo is None:
        return []
    if session.get("is_admin"):
        clients = _repo.list_clients()
        nombres = {v["id"]: v.get("name") or "" for v in _repo.list_vendedores()}
        for c in clients:
            c["vendedor"] = nombres.get(c.get("account_id"), "")
        return [c for c in clients if not c.get("reserved")]
    acc = session.get("account_id")
    if not acc:
        return []
    return [c for c in _repo.list_clients(acc) if not c.get("reserved")]


@app.route("/api/followers/clientes", methods=["GET"])
@require_login
def followers_clientes():
    try:
        return jsonify({"clients": _followers_clients()})
    except Exception as e:
        print(f"[followers] no pude listar clientes: {e!r}", flush=True)
        return jsonify({"error": "No pude traer tus clientes", "clients": []}), 502


@app.route("/api/uso", methods=["GET"])
@require_admin
def uso():
    """Contador de acciones por cuenta y, dentro de cada una, por usuario.
    El desglose por usuario es lo que sostiene el cobro por asiento."""
    if _repo is None:
        return jsonify({"vendedores": []})
    try:
        filas = _repo.usage_counts_all_accounts()
        for f in filas:
            try:
                f["usuarios"] = _repo.usage_counts_by_user(f["account_id"])
            except Exception as e:
                print(f"[uso] no pude desglosar la cuenta {f.get('account_id')} ({e})", flush=True)
                f["usuarios"] = []
        return jsonify({"vendedores": filas})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fecha_arg(nombre):
    """Lee un ?desde=/?hasta= en formato YYYY-MM-DD. Una fecha mal escrita se
    ignora (se cae al default del repo) en vez de tirar un 400: el panel no
    puede quedar en blanco porque el navegador mandó otro formato."""
    from datetime import datetime
    v = (request.args.get(nombre) or "").strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        print(f"[tokens] fecha {nombre}={v!r} inválida, la ignoro", flush=True)
        return None


@app.route("/api/tokens", methods=["GET"])
@require_admin
def tokens():
    """Gasto de IA (tokens y dólares) por vendedor, por rango de fechas y con el
    corte mensual. A diferencia de /api/uso, que cuenta acciones, acá se ve la
    plata: qué cuenta consume, con qué usuario adentro, y cuánto se va en
    reintentos.

    ?desde/?hasta son YYYY-MM-DD y ambos INCLUSIVOS (es lo que espera quien elige
    "del 1 al 31 de agosto"). Adentro `hasta` se convierte en exclusivo.
    """
    vacio = {"vendedores": [], "total": {}, "sin_atribuir": {}}
    if _repo is None:
        return jsonify(vacio)
    from datetime import timedelta
    desde = _fecha_arg("desde")
    hasta = _fecha_arg("hasta")
    if hasta is not None:
        hasta = hasta + timedelta(days=1)
    try:
        datos = _repo.gasto_por_vendedor(desde=desde, hasta=hasta) or dict(vacio)
    except Exception as e:
        print(f"[tokens] no pude calcular el gasto: {e!r}", flush=True)
        return jsonify({"error": "No pude calcular el gasto de IA"}), 500

    # Lo que Anthropic facturó de verdad, para contrastar con nuestro estimado.
    # Nunca puede tumbar el endpoint: el gasto por vendedor ya está calculado y
    # es lo que el admin vino a ver.
    try:
        import anthropic_costs
        datos["facturado"] = anthropic_costs.facturado(datos["desde"],
                                                       _rango_exclusivo(datos["hasta"]))
    except Exception as e:
        print(f"[tokens] no pude traer el facturado: {e!r}", flush=True)
        datos["facturado"] = {"disponible": False, "motivo": "no se pudo consultar"}
    return jsonify(datos)


def _rango_exclusivo(hasta_inclusivo: str) -> str:
    """El panel habla en fechas inclusivas y la API de costos en exclusivas."""
    from datetime import datetime, timedelta as _td
    return (datetime.strptime(hasta_inclusivo, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")


@app.route("/api/cantidades_usadas", methods=["GET"])
@require_login
def cantidades_usadas():
    """Cantidades ya enviadas para un cliente y tipo de producto. El front las usa
    para no repetir nunca un número en la tirada automática."""
    if _repo is None:
        return jsonify({"usadas": []})
    try:
        usadas = _repo.used_quantities(
            session.get("account_id"),
            request.args.get("client", ""),
            request.args.get("tipo", ""),
        )
        return jsonify({"usadas": usadas})
    except Exception as e:
        print(f"[cantidades_usadas] {e!r}", flush=True)
        return jsonify({"usadas": []})


@app.route("/api/me", methods=["GET"])
@require_login
def me():
    """Info del usuario logueado (para que el front muestre nombre/rol)."""
    return jsonify(_current_user())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
