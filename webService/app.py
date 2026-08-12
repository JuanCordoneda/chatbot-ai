from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, make_response, has_request_context
from functools import wraps
import requests
import os
import json
import re
import time
import secrets
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
    # La sesión dura 30 días de INACTIVIDAD, no 30 días desde el login. Cortarla
    # a las 24 hs echaba a todo el mundo en medio del día de trabajo.
    PERMANENT_SESSION_LIFETIME=timedelta(
        hours=int(os.environ.get("SESSION_HOURS", "720"))),
    # Refresco en cada request: el plazo cuenta desde la última pantalla abierta,
    # así el que usa el sistema todos los días no vuelve a ver el login nunca.
    SESSION_REFRESH_EACH_REQUEST=True,
)

# Cookie aparte de la sesión: guarda SOLO el usuario para precargar el campo del
# formulario de login. Va cifrada con Fernet (misma DB_ENCRYPTION_KEY que el
# resto) y HttpOnly. Sobrevive al logout y al vencimiento de la sesión.
#
# Antes guardaba también la contraseña, para que al vencer la sesión alcanzara
# con apretar Enter. Se sacó a propósito: la contraseña del login ES la del CRM
# de Growi, y el sistema ya no la guarda en ningún lado (ver _CREDENCIALES). Una
# cookie de 365 días con la credencial adentro era justamente el lugar donde más
# tiempo vivía. El navegador sigue ofreciendo autocompletar la contraseña, que es
# donde corresponde que esté esa decisión: en el cliente, no en nuestro server.
REMEMBER_USER_COOKIE = "GROWI_LAST_USER"
REMEMBER_USER_DAYS = 365

# Sello de sesión: las cookies viejas no lo traen (o traen otro valor), así que
# al subir este deploy todos quedan deslogueados una vez y vuelven a entrar.
# Para forzar otro logout masivo más adelante, subir el número.
#
# Se sube a 2 con el cambio de credenciales-en-memoria: las sesiones abiertas de
# antes no tienen su contraseña en el almacén (nunca la pidieron por esta vía) y
# quedarían sin poder operar contra el CRM hasta reloguear. Mejor un logout
# masivo limpio que un vendedor descubriéndolo al mandar una orden.
SESSION_STAMP = 2

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
WHATSAPP_SERVICE_URL = os.environ.get("WHATSAPP_SERVICE_URL", "http://whatsapp-service:8501")
# A qué WhatsApp se manda la tanda de comentarios. Es UN número para todos, por
# diseño: desde ahí se reenvía adonde haga falta. Si no se configura uno propio
# se usa el mismo que recibe las alertas del CRM.
REPARTO_WHATSAPP_TO = (os.environ.get("REPARTO_WHATSAPP_TO")
                       or os.environ.get("GROWI_ALERTA_WHATSAPP", "")).strip()
# Template que se usa SOLO para abrir la ventana de 24h de Meta (ver
# /api/activar-wa). Cualquiera aprobado sirve: lo único que importa es que
# llegue y se pueda responder. hello_world viene aprobado de fábrica.
WHATSAPP_TEMPLATE_ACTIVACION = os.environ.get("WHATSAPP_TEMPLATE_ACTIVACION", "hello_world")
WHATSAPP_TEMPLATE_IDIOMA = os.environ.get("WHATSAPP_TEMPLATE_IDIOMA", "en_US")
# Número DEL BOT (el emisor), en formato internacional sin "+". Se usa para el
# link wa.me que abre el chat con un "hola" ya escrito: es la forma más corta de
# abrir la ventana de 24h, un toque en vez de esperar un template y responderlo.
WHATSAPP_NUMERO_BOT = os.environ.get("WHATSAPP_NUMERO_BOT", "").strip().lstrip("+")

# Capa de datos multi-tenant. Opcional: si no está, se usa el login legacy.
try:
    from common import repository as _repo
except Exception:
    _repo = None

# Armado de las órdenes (mezcla de comentarios, normalización, fecha AR). Es el
# mismo módulo que usa el openAIService: una sola definición de "cómo se arma
# una orden" para los dos servicios.
from common import ordenes as _ordenes

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

from contextlib import contextmanager

try:
    from common.growi_trace import (trazar, contexto as traza_contexto,
                                    rechazo_local as traza_rechazo_local)
except Exception:
    # Mismo criterio que los imports de arriba: sin el paquete común el
    # webService tiene que seguir andando, solo que sin auditoría.
    @contextmanager
    def trazar(*a, **kw):
        class _Nada:
            def headers(self, *a, **kw): pass
            def respuesta(self, *a, **kw): pass
            def fallo(self, *a, **kw): pass
            def intento(self, *a, **kw): pass
            def via(self, *a, **kw): pass
            def datos(self, *a, **kw): pass
        yield _Nada()

    @contextmanager
    def traza_contexto(*a, **kw):
        yield {}

    @contextmanager
    def traza_rechazo_local(*a, **kw):
        yield

_GROWI_PROXY_URL = os.environ.get("GROWI_HTTP_PROXY", "")
_GROWI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
# Sesiones del CRM cacheadas POR CREDENCIAL ((crm_url, crm_email) -> {"session",
# "cfg"}), NO por account_id. Dos cuentas distintas pueden apuntar al MISMO
# usuario de Growi — pasa con la cuenta del admin y el fallback del .env, que
# comparten email —, y si cada una abre su propia sesión, cada login invalida el
# de la otra: la primera se encuentra deslogueada a mitad de un envío y el error
# sale como "credenciales rechazadas" cuando la contraseña está perfecta.
# Compartiendo la sesión por credencial, ese caso desaparece.
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


# ── Contraseñas del CRM: en memoria, nunca en disco ─────────────────────────────
#
# La contraseña de Growi del vendedor NO se guarda: ni en la DB, ni en la cookie
# de sesión, ni en la de "recordar usuario". Vive solo acá, en la memoria de este
# proceso, desde que la tipea al entrar hasta que se desloguea o vence la sesión.
# Si el proceso se reinicia, se pierde y hay que reloguear — que es exactamente
# lo que se pidió.
#
# Por qué en memoria y no en la sesión de Flask: `session` es una cookie FIRMADA,
# no cifrada. Poner la contraseña ahí la mandaría al navegador en algo que
# cualquiera con la cookie puede leer en claro; sería peor que la DB cifrada que
# había antes. Lo que viaja en la cookie es `cred_key`, un token aleatorio que no
# significa nada fuera de esta memoria.
#
# Ojo si algún día esto pasa a correr con varios workers (gunicorn): la memoria
# no se comparte entre procesos y el vendedor quedaría sin credencial según a qué
# worker caiga su request. Hoy el web-service corre como un solo proceso.
_CREDENCIALES = {}
_credenciales_lock = threading.Lock()


class CredencialAusente(RuntimeError):
    """No tenemos la contraseña del CRM de esta cuenta en memoria. No es un
    rechazo del CRM: es que el vendedor no está logueado (o se reinició el
    servicio) y hay que pedírsela de nuevo."""

    # Se levanta antes de mandar nada: la orden se puede guardar y reintentar
    # sin riesgo de duplicarla. Ver _seguro_de_reintentar.
    pre_envio = True


def _cred_ttl():
    return app.config["PERMANENT_SESSION_LIFETIME"].total_seconds()


def _purgar_credenciales(ahora):
    """Saca las vencidas. Se llama en cada acceso: son pocas (una por vendedor
    logueado) y así no hace falta un hilo de limpieza."""
    ttl = _cred_ttl()
    for k, v in list(_CREDENCIALES.items()):
        if ahora - v["ts"] > ttl:
            del _CREDENCIALES[k]


def _guardar_credencial(account_id, password):
    """Guarda la contraseña recién tipeada y devuelve la clave para la sesión."""
    clave = secrets.token_urlsafe(32)
    with _credenciales_lock:
        _purgar_credenciales(time.time())
        _CREDENCIALES[clave] = {"account_id": account_id, "password": password,
                                "ts": time.time()}
    return clave


def _credencial_de_sesion(account_id):
    """La contraseña del CRM del vendedor logueado, o "" si no está disponible.

    Se valida que la entrada sea de ESTA cuenta: la clave sale de la cookie, y
    una cookie vieja de otra cuenta no puede terminar prestando su contraseña.
    Fuera de un request (hilos de fondo) no hay sesión y devuelve "" — a
    propósito: ya no hay operaciones automáticas contra el CRM.
    """
    if not has_request_context():
        return ""
    clave = session.get("cred_key")
    if not clave:
        return ""
    with _credenciales_lock:
        entry = _CREDENCIALES.get(clave)
        if not entry:
            return ""
        if time.time() - entry["ts"] > _cred_ttl():
            del _CREDENCIALES[clave]
            return ""
        if account_id and entry["account_id"] != account_id:
            return ""
        return entry["password"]


def _olvidar_credencial():
    """Borra la contraseña del vendedor que se está deslogueando."""
    clave = session.get("cred_key") if has_request_context() else None
    if not clave:
        return
    with _credenciales_lock:
        _CREDENCIALES.pop(clave, None)


def _account_crm_cfg(account_id):
    """Credenciales del CRM de la cuenta (vendedor). Cae al .env global si no hay
    cuenta / DB. La URL siempre queda seteada.

    La contraseña NO viene de la DB (ya no se guarda): se toma del almacén en
    memoria de la sesión del vendedor. Si no está, `crm_password` queda vacía y
    el login contra el CRM falla con CredencialAusente, que el front traduce a
    "volvé a iniciar sesión".
    """
    if _repo is not None and account_id:
        try:
            cfg = _repo.get_account_crm_config(account_id)
            if cfg and cfg.get("crm_email"):
                cfg["crm_password"] = _credencial_de_sesion(account_id)
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
    enviar_trafico.php, que no dice nada de lo que realmente pasó.

    `pre_envio` dice si esto pasó ANTES de mandar la orden. Importa mucho: si el
    login falló, a enviar_trafico.php no llegó nada y la orden se puede guardar
    para reintentarla sin riesgo. Si en cambio la sesión se cayó DESPUÉS de que
    el POST saliera, no sabemos si el CRM lo procesó, y ahí reintentar puede
    cobrarle dos veces al cliente. Por defecto False: el que sabe que es seguro
    tiene que decirlo explícitamente."""

    pre_envio = False


def _verificar_sesion(s, url):
    """Comprueba si la sesión quedó autenticada, devolviendo TAMBIÉN el porqué.

    Devuelve {"ok": True|False|None, "status": int|None, "location": str|None}.
    ok=None significa "no se pudo comprobar" (timeout, red): un problema de red
    no es un login rechazado.

    El status y el Location son lo que faltaba para diagnosticar: sin ellos,
    cualquier respuesta que no fuera 200 se reportaba como "usuario o contraseña
    incorrectos", que es una conclusión que este chequeo NO puede sacar.
    """
    try:
        r = s.get(f"{url}/paginas/trafico.php", allow_redirects=False, timeout=15)
        return {"ok": r.status_code == 200, "status": r.status_code,
                "location": r.headers.get("location")}
    except Exception as e:
        print(f"[growi-web] no pude verificar la sesión ({e!r})", flush=True)
        return {"ok": None, "status": None, "location": None}


def _destino_del_login(resp):
    """A dónde mandó el CRM después del POST a login.php, o None si no redirigió.

    El POST se sigue mandando con redirects habilitados (no cambiamos cómo se
    abre la sesión), así que el 302 original está en resp.history y no en resp.
    """
    if resp is None:
        return None
    if resp.history:
        return resp.history[0].headers.get("location") or resp.url
    return None


def _login_parece_aceptado(resp):
    """True si el POST a login.php parece un login ACEPTADO, None si no hay señal.

    El CRM contesta con un redirect: a una página interna si entró, y de vuelta a
    login.php si rebotó. No es una certeza, y por eso solo se usa para redactar el
    error: si el login fue aceptado y aun así trafico.php nos manda al login, el
    problema no es la contraseña. Sin redirect no hay señal confiable (un 200
    puede ser el formulario de vuelta), y entonces no afirmamos nada.
    """
    destino = _destino_del_login(resp)
    if not destino:
        return None
    return "login.php" not in destino.lower()


# Cuántos logins contra el CRM llevamos por credencial, con su hora. Sirve para
# una sola cosa, pero importante: saber si cuando el CRM nos rebota veníamos de
# hacerle muchos logins seguidos. Growi no documenta si tiene protección contra
# fuerza bruta; si la tiene, el síntoma sería exactamente el que vemos (rechaza
# un login con credenciales buenas, y se arregla solo al rato). Sin este contador
# no hay forma de distinguir eso de una contraseña mal cargada.
_LOGINS_RECIENTES = {}
_logins_lock = threading.Lock()
VENTANA_LOGINS = 600.0     # 10 minutos


def _anotar_login(clave):
    """Registra un login y devuelve cuántos van en los últimos 10 minutos."""
    ahora = time.time()
    with _logins_lock:
        hist = [t for t in _LOGINS_RECIENTES.get(clave, []) if ahora - t < VENTANA_LOGINS]
        hist.append(ahora)
        _LOGINS_RECIENTES[clave] = hist
        return len(hist)


def _huella_respuesta(resp):
    """Qué contestó el CRM al POST del login, en una línea y sin secretos.

    El status y el redirect no alcanzaron para diagnosticar: cuando el CRM
    contesta 200 sin redirect, la diferencia entre "te rechacé la contraseña",
    "te estoy limitando" y "cambié el formulario" está en el CUERPO, y era lo
    único que no mirábamos. No se guarda el HTML entero: solo su tamaño, el tipo
    y el texto visible recortado, que es donde Growi pone el mensaje de error.
    """
    if resp is None:
        return "sin respuesta"
    tipo = (resp.headers.get("content-type") or "?").split(";")[0]
    cuerpo = resp.text or ""
    # El texto visible: sin tags ni scripts, colapsado. Un form de login son
    # ~40 caracteres útiles, y ahí está el "usuario o contraseña incorrectos" o
    # el "demasiados intentos" que necesitamos leer.
    visible = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", cuerpo,
                     flags=re.S | re.I)
    visible = re.sub(r"<[^>]+>", " ", visible)
    visible = re.sub(r"\s+", " ", visible).strip()[:300]
    cookies = ",".join(sorted(resp.cookies.keys())) or "ninguna"
    return f"{tipo} {len(cuerpo)}b; cookies={cookies}; texto=\"{visible}\""


def _detalle_login(cfg, login_resp, check, logins_recientes=None):
    """Arma la explicación del rechazo con los datos crudos, sin inventar causa."""
    email = cfg.get("crm_email") or "(sin email)"
    dest = _destino_del_login(login_resp)
    # El POST sigue redirects, así que login_resp.status_code es el de la página
    # FINAL. Para el log sirve el del redirect original (el 302), que es el que
    # se corresponde con el destino que mostramos al lado.
    if login_resp is None:
        status_login = "—"
    elif login_resp.history:
        status_login = login_resp.history[0].status_code
    else:
        status_login = login_resp.status_code
    crudo = (f"login.php → {status_login}"
             f"{' → ' + dest if dest else ''}; "
             f"trafico.php → {check.get('status')}"
             f"{' → ' + check['location'] if check.get('location') else ''}")
    # Los logins recientes van SIEMPRE en el detalle: si el CRM nos rechaza el
    # login número 15 en diez minutos, esa es la pista, y no aparecía por ningún
    # lado. Con 1 o 2 se lee igual y descarta la hipótesis.
    if logins_recientes:
        crudo += f"; {logins_recientes} login/s en los últimos 10 min"
    # Tres casos, no dos. Antes el "no hay señal" caía en el mismo texto que el
    # rebote confirmado y mandaba a revisar una contraseña sin tener con qué
    # afirmar que el problema fuera esa: _login_parece_aceptado devuelve None
    # justamente para no afirmar nada, y tratar ese None como False lo tiraba.
    aceptado = _login_parece_aceptado(login_resp)

    if aceptado is True:
        # El CRM aceptó las credenciales y ACÁ ABAJO igual no hay sesión: es la
        # sesión, no la contraseña. Pasa cuando el CRM ata la sesión a la IP de
        # salida (y la IP rota) o cuando dos sesiones del MISMO usuario de Growi
        # se pisan entre sí.
        return (f"El CRM aceptó el login de {email} pero la sesión no quedó abierta "
                f"({crudo}). No es la contraseña: suele ser la IP de salida o dos "
                f"sesiones del mismo usuario de Growi pisándose.")

    if aceptado is False:
        # Redirect de vuelta a login.php: el CRM rechazó explícitamente.
        return (f"El CRM no dejó entrar a {email} y rebotó al login ({crudo}). "
                "Casi seguro la contraseña de Growi está mal o cambió: probá "
                "entrar al CRM a mano con esas credenciales.")

    # Sin redirect no sabemos si rechazó o si ni siquiera procesó el formulario
    # (un 200 puede ser el form de vuelta con el error, pero también un cambio
    # en el login del CRM que hace que el POST no llegue a nada). Se describe lo
    # que pasó y se dan las dos posibilidades, sin elegir una. Va la huella de la
    # respuesta: es lo único que puede desempatar, y se lee de la pantalla sin
    # tener que entrar a los logs del servidor.
    return (f"El CRM no abrió la sesión de {email} y no redirigió a ningún lado "
            f"({crudo}). Respuesta del CRM: {_huella_respuesta(login_resp)}. "
            "Puede ser la contraseña, o que el CRM nos esté limitando o haya "
            "cambiado su formulario de login. Probá entrar a mano: si entrás vos "
            "y el sistema no, no es la contraseña.")


def _growi_login_with(cfg, verify=True, account_id=None):
    """Abre una sesión autenticada contra el CRM con las credenciales dadas.
    Con verify, comprueba que el login haya funcionado de verdad (mismo patrón
    que openAIService/growi_client) y falla fuerte si no."""
    url = cfg.get("crm_url") or GROWI_CRM_URL
    # Sin contraseña no se intenta el login: el CRM contestaría "credenciales
    # inválidas" y el vendedor leería "revisá tu usuario y contraseña", que es
    # una causa equivocada. Lo que pasa es que su sesión no tiene la credencial
    # en memoria (se deslogueó, venció, o se reinició el servicio).
    if not cfg.get("crm_password"):
        raise CredencialAusente(
            "Tu sesión ya no tiene la contraseña de Growi. Volvé a iniciar "
            "sesión para poder cargar órdenes.")
    # crm_proxy puede traer VARIOS proxies separados por coma: se prueban en
    # orden y gana el primero que responda. Con uno solo se comporta igual que
    # antes. Ojo: sin este parseo, una lista se pasaría entera como si fuera una
    # única URL de proxy y no conectaría con ninguno.
    pool = ProxyPool(cfg.get("crm_proxy") or "")
    ultimo_error = None

    # El contador va por credencial (a qué CRM y con qué usuario), que es como el
    # CRM nos ve: si nos está limitando, nos limita al usuario, no al proceso.
    logins = _anotar_login(_clave_sesion(cfg))

    for proxy in pool.candidatos():
        s = requests.Session()
        s.headers.update({"user-agent": _GROWI_USER_AGENT})
        s.proxies.update(proxies_de(proxy))
        try:
            login_resp = s.post(
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

        check = _verificar_sesion(s, url)
        if check["ok"] is None:
            # No se pudo comprobar por un problema de red: puede ser este proxy.
            ultimo_error = ultimo_error or RuntimeError("sesión no verificable")
            pool.marcar_muerto(proxy)
            continue
        if check["ok"] is False:
            detalle = _detalle_login(cfg, login_resp, check, logins_recientes=logins)
            print(f"[growi-web] sesión no autenticada tras el login: {detalle}", flush=True)
            # La huella completa va al log SIEMPRE (aunque verify=False, que es el
            # camino del login del vendedor): es el rastro que permite reconstruir
            # qué contestó el CRM cuando esto pasa en producción y nadie mira.
            print(f"[growi-web] respuesta cruda del login: {_huella_respuesta(login_resp)}",
                  flush=True)
            if verify:
                # Falla en el login: a enviar_trafico.php no salió nada todavía,
                # así que la orden es segura de guardar y reintentar.
                err = GrowiAuthError(detalle)
                err.pre_envio = True
                raise err
        pool.marcar_vivo(proxy)
        return s

    raise requests.exceptions.ConnectionError(
        f"No hay ruta hasta el CRM por ninguno de los proxies configurados "
        f"({ultimo_error.__class__.__name__ if ultimo_error else 'sin detalle'})"
    )


def _idvendedor_del_crm(account_id):
    """El ID de vendedor que el CRM le reconoce a esta cuenta.

    Sale del listado de campañas: cada campaña viene con `data-idvendedor`, y ese
    listado está acotado al vendedor logueado (verificado contra el CRM real: una
    cuenta no ve las campañas de otra). O sea que el id de sus campañas ES el
    suyo. Se toma el más frecuente por si alguna fila viniera incompleta.

    Es un solo POST a propósito: `_traer_ventas` además resuelve el perfil de IG
    de cada campaña, que son ~120 pedidos, y esto corre en el login.

    Devuelve "" si no se pudo averiguar; nunca lanza.
    """
    from collections import Counter

    def _pedir(antiguas):
        resp = _growi_request(
            "POST", "/paginas/traer_campanas.php", account_id=account_id, timeout=30,
            data={"antiguas": antiguas},
            headers={"referer": f"{_crm_base(account_id)}/paginas/trafico.php",
                     "x-requested-with": "XMLHttpRequest"},
        )
        resp.raise_for_status()
        return [v["idvendedor"] for v in _parse_ventas(resp.text) if v.get("idvendedor")]

    try:
        # Primero las activas, que es el pedido barato y el caso normal. Si la
        # cuenta no tiene ninguna abierta (recién dada de alta, o todo cerrado),
        # se buscan también las viejas: el idvendedor es el mismo y sirve igual
        # para saber a nombre de quién carga esta cuenta.
        ids = _pedir("0")
        if not ids:
            ids = _pedir("1")
            if ids:
                print(f"[cuenta {account_id}] sin campañas activas; el idvendedor "
                      f"salió de las viejas", flush=True)
        if not ids:
            print(f"[cuenta {account_id}] el CRM no devolvió ningún idvendedor "
                  f"(no tiene campañas)", flush=True)
            return ""
        return Counter(ids).most_common(1)[0][0]
    except Exception as e:
        print(f"[cuenta {account_id}] no pude averiguar el idvendedor: {e!r}", flush=True)
        return ""


def _refrescar_idvendedor(account_id):
    """Guarda en la cuenta el idvendedor que dice el CRM. Silencioso ante
    cualquier error: que falle esto no puede impedirle a nadie entrar."""
    if _repo is None or not account_id:
        return
    try:
        idv = _idvendedor_del_crm(account_id)
        if idv:
            _repo.guardar_idvendedor(account_id, idv)
    except Exception as e:
        print(f"[login] no pude guardar el idvendedor de {account_id}: {e!r}", flush=True)


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
        check = _verificar_sesion(s, url)
        if check["ok"] is None:
            return "unreachable"
        if check["ok"]:
            return "ok"
        print(f"[auth] credenciales rechazadas para {cfg.get('crm_email')}: "
              f"trafico.php → {check['status']}"
              f"{' → ' + check['location'] if check['location'] else ''}", flush=True)
        return "invalid"
    except Exception as e:
        print(f"[auth] no pude hablar con el CRM al validar credenciales ({e!r})", flush=True)
        return "unreachable"


# Un lock por cuenta para abrir sesión. Sin esto, dos vendedores de la misma
# cuenta mandando a la vez disparan dos logins simultáneos contra el CRM y cada
# uno pisa la sesión del otro (el CRM ata la sesión a la IP del login, así que
# el segundo login puede invalidar el primero justo cuando el otro está por
# postear su orden). Con el lock, el segundo espera y reusa la sesión abierta.
_growi_sessions_lock = threading.Lock()
_growi_login_locks = {}


def _clave_sesion(cfg):
    """Identidad de una sesión del CRM: a qué CRM y con qué usuario. Dos cuentas
    con el mismo usuario de Growi comparten sesión, que es justo lo que evita que
    se pisen entre sí."""
    return ((cfg.get("crm_url") or GROWI_CRM_URL).rstrip("/").lower(),
            (cfg.get("crm_email") or "").strip().lower())


def _lock_de_sesion(clave):
    with _growi_sessions_lock:
        lock = _growi_login_locks.get(clave)
        if lock is None:
            lock = _growi_login_locks[clave] = threading.Lock()
        return lock


def _abrir_sesion(cfg, account_id=None):
    """Loguea y guarda la sesión bajo su credencial. Devuelve la entrada.

    `token` arranca vacío a propósito: es el x-growi-token de ESTA sesión y se
    pide recién cuando hace falta (ver _growi_token). Al reloguear se arma una
    entrada nueva, así que el token viejo se descarta solo.
    """
    entry = {"session": _growi_login_with(cfg, account_id=account_id),
             "cfg": cfg, "token": None}
    _growi_sessions[_clave_sesion(cfg)] = entry
    return entry


# El CRM incorporó un token anti-anomalía por sesión: viaja en el header
# x-growi-token y sale de <meta name="growi-token"> del HTML. Sin él,
# enviar_trafico.php NO inserta y —lo peor— contesta 200 con
# {"success":false,"insertadas":0,"errors":[]}, sin decir que falta el token.
# Eso es exactamente lo que dejó a los vendedores sin poder publicar: para
# nosotros era un fallo mudo. El navegador lo manda solo (guard-api.js); acá hay
# que ir a buscarlo.
_TOKEN_RE = re.compile(r"""<meta\s+name=["']growi-token["']\s+content=["']([^"']+)["']""", re.I)
# Cualquier página del gestor sirve; se usa trafico.php porque es la que ya
# consultamos para las campañas y está garantizado que existe.
_PATH_TOKEN = "/paginas/trafico.php"


def _growi_token(entry, account_id=None, refrescar=False):
    """El x-growi-token de esta sesión, cacheado en la entrada de sesión.

    Devuelve None si no se pudo leer: en ese caso el request sale sin el header
    y se comporta como antes. Preferimos eso a bloquear todo el tráfico al CRM
    por una página que no cargó; el envío igual detecta el rebote mudo y avisa.
    """
    if entry.get("token") and not refrescar:
        return entry["token"]
    try:
        url = entry["cfg"].get("crm_url") or GROWI_CRM_URL
        # OJO: con entry["session"] directo, no con _growi_request, que llamaría
        # de vuelta a esta función y entraría en recursión infinita.
        resp = entry["session"].get(f"{url}{_PATH_TOKEN}", timeout=15)
        m = _TOKEN_RE.search(resp.text or "")
        if not m:
            print(f"[growi-web] no encontré el growi-token en {_PATH_TOKEN} "
                  f"(cuenta {account_id}); mando sin él", flush=True)
            return None
        entry["token"] = m.group(1)
        return entry["token"]
    except Exception as e:
        print(f"[growi-web] no pude leer el growi-token (cuenta {account_id}): {e!r}",
              flush=True)
        return None


def _rebote_mudo_de_envio(resp):
    """¿El CRM aceptó el POST del envío pero no insertó nada y no dijo por qué?

    Es la firma del token vencido/faltante. Se distingue de un rechazo legítimo
    (saldo, validación) en que NO trae ni errors ni warnings ni messages: el CRM
    cuando rechaza de verdad los completa.
    """
    try:
        d = resp.json()
    except Exception:
        return False
    return (isinstance(d, dict) and d.get("success") is False
            and not d.get("insertadas")
            and not d.get("errors") and not d.get("warnings") and not d.get("messages"))


def _olvidar_sesion(cfg):
    _growi_sessions.pop(_clave_sesion(cfg), None)


def _olvidar_sesion_de_cuenta(account_id):
    """Tira la sesión cacheada de una cuenta. Se resuelve por credencial, así que
    hay que llamarla ANTES de cambiarle el email/password a la cuenta: después ya
    no se puede calcular la clave vieja."""
    try:
        _olvidar_sesion(_account_crm_cfg(account_id))
    except Exception as e:
        print(f"[growi-web] no pude invalidar la sesión de la cuenta {account_id} ({e})",
              flush=True)


def _get_growi_session(account_id):
    cfg = _account_crm_cfg(account_id)
    clave = _clave_sesion(cfg)
    entry = _growi_sessions.get(clave)
    if entry is not None:
        return entry
    with _lock_de_sesion(clave):
        # Otro hilo pudo haberla abierto mientras esperábamos el lock.
        entry = _growi_sessions.get(clave)
        if entry is None:
            entry = _abrir_sesion(cfg, account_id=account_id)
        return entry


def _crm_base(account_id=None):
    """URL base del CRM de la cuenta logueada (para armar referers). Cae al .env.

    has_request_context: también se llama desde hilos de fondo (el refresco de
    ventas), donde tocar `session` explota.
    """
    if account_id is None:
        account_id = session.get("account_id") if has_request_context() else None
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
    Detectamos ambos casos: el 401 y el redirect a la página de login.

    El match es sobre el PATH y contra el login exacto, no un `"login" in url`.
    Esa versión suelta daba positivo con cualquier URL que tuviera la palabra
    (una query `?next=login`, un producto con "login" en el nombre), y un falso
    positivo acá no es cosmético: dispara el reintento del POST a
    enviar_trafico.php, que no es idempotente, y duplica la orden.
    """
    if resp.status_code == 401:
        return True
    from urllib.parse import urlparse
    path = (urlparse(resp.url or "").path or "").lower()
    return path.endswith("/cuenta/login.php") or path.endswith("/login.php")


def _respuesta_de_orden_valida(resp) -> bool:
    """True si el CRM ya nos contestó como CRM sobre una orden.

    Si vino nuestro JSON con `success`/`insertadas`, el pedido LLEGÓ y se
    procesó: pase lo que pase con la sesión, reintentarlo cargaría la orden dos
    veces. Es el cinturón de seguridad del reintento.
    """
    try:
        data = resp.json()
    except Exception:
        return False
    return isinstance(data, dict) and ("success" in data or "insertadas" in data)


# Solo se audita el ENVÍO DE ÓRDENES. El resto del tráfico al CRM (la hora del
# server, el listado de ventas, el login) son consultas de apoyo que se repiten
# todo el tiempo y no mueven plata: trazarlas llenaba la tabla de ruido y tapaba
# lo único que hay que poder reconstruir cuando un vendedor reclama.
_PATH_ENVIO_ORDENES = "/paginas/enviar_trafico.php"


class _SinTraza:
    """Traza de mentira para las llamadas que no se auditan: mismo API, no hace
    nada. Evita llenar _growi_request de `if hay_traza`."""

    def headers(self, *a, **kw): pass
    def respuesta(self, *a, **kw): pass
    def fallo(self, *a, **kw): pass
    def intento(self, *a, **kw): pass
    def via(self, *a, **kw): pass
    def datos(self, *a, **kw): pass


@contextmanager
def _sin_traza():
    yield _SinTraza()


def _growi_request(method, path, account_id=None, **kwargs):
    """GET/POST autenticado contra el CRM de la cuenta logueada, reintentando con
    login fresco si la sesión murió (401 o redirect al login). account_id
    explícito o el de la sesión.

    Todo el tráfico al CRM pasa por acá, así que es también el único lugar donde
    hay que auditar: el envío de órdenes queda en `growi_calls` con lo que se
    mandó, lo que contestaron y cuántos relogins costó.
    """
    if account_id is None:
        account_id = session.get("account_id") if has_request_context() else None
    es_envio = path.split("?")[0] == _PATH_ENVIO_ORDENES
    # El envío de órdenes tiene su propio timeout: una tanda de 100+ comentarios
    # tarda bastante más que una consulta, y con los 15s de las consultas se
    # cortaba la lectura justo en el caso más caro — sin saber si la orden entró.
    timeout = kwargs.pop("timeout", (10, 60) if es_envio else 15)
    entry = _get_growi_session(account_id)
    traza = (
        trazar("enviar_trafico", method, f"{_crm_base(account_id)}{path}",
               payload=kwargs.get("json"), account_id=account_id,
               user_id=session.get("user_id") if has_request_context() else None,
               username=session.get("username") if has_request_context() else None)
        if es_envio else _sin_traza()
    )
    with traza as tr:
        # Igual que en growi_client: si el request no llega a salir, la traza
        # igual tiene que poder mostrar con qué headers se intentó.
        tr.headers(kwargs.get("headers"))
        token_reintentado = False
        for intento in range(1, 5):
            tr.intento(intento)
            url = entry["cfg"].get("crm_url") or GROWI_CRM_URL
            tr.via(entry["cfg"].get("crm_proxy"))
            # El token va en TODAS las llamadas, no solo en el envío: el CRM lo
            # valida en cada endpoint del gestor y el navegador también lo manda
            # siempre. Se arma acá adentro del loop porque después de un relogin
            # la entrada (y con ella el token) cambia.
            tok = _growi_token(entry, account_id)
            if tok:
                kwargs["headers"] = {**(kwargs.get("headers") or {}),
                                     "x-growi-token": tok}
                tr.headers(kwargs["headers"])
            resp = entry["session"].request(
                method, f"{url}{path}", timeout=timeout, **kwargs
            )
            if not _growi_sesion_caida(resp):
                # Envío que vuelve sin insertar y sin explicación: lo más probable
                # es que el token haya vencido (el CRM no lo dice). Se pide uno
                # nuevo y se reintenta UNA sola vez. Es seguro: no insertó nada,
                # así que no hay riesgo de duplicar el cobro.
                if es_envio and not token_reintentado and _rebote_mudo_de_envio(resp):
                    token_reintentado = True
                    print(f"[growi-web] el CRM no insertó nada y no dijo por qué "
                          f"(cuenta {account_id}): renuevo el growi-token y reintento",
                          flush=True)
                    if _growi_token(entry, account_id, refrescar=True):
                        continue
                tr.respuesta(resp)
                return resp
            # La sesión parece caída. Antes de reintentar un POST que NO es
            # idempotente, comprobamos que el CRM no haya contestado ya sobre la
            # orden: si mandó su JSON, el pedido se procesó y reintentarlo la
            # duplicaría. Ese es el único caso en que preferimos devolver una
            # respuesta "rara" antes que arriesgar un cobro doble.
            if es_envio and _respuesta_de_orden_valida(resp):
                print(f"[growi-web] la sesión parecía caída pero el CRM ya respondió "
                      f"sobre la orden: NO reintento (cuenta {account_id})", flush=True)
                tr.respuesta(resp)
                return resp
            print(f"[growi-web] sesión caída ({resp.status_code}, url={resp.url}) en "
                  f"intento {intento}/4 para {path} (cuenta {account_id}), relogueando", flush=True)
            _olvidar_sesion(entry["cfg"])
            entry = _abrir_sesion(_account_crm_cfg(account_id), account_id=account_id)
        # Se agotaron los reintentos con la sesión cayéndose una y otra vez.
        #
        # Ojo con leer esto como "probó 4 logins": no llega hasta acá con el login
        # rechazado. `_abrir_sesion` verifica la sesión y levanta GrowiAuthError en
        # el PRIMER relogin que no abre, así que ese caso sale por otro lado, con
        # el detalle de `_detalle_login`. Acá se llega solo si cada relogin abre
        # bien y la sesión se muere igual entre un request y el siguiente — que es
        # el síntoma de la sesión atada a una IP que rota, o de otra sesión del
        # mismo usuario de Growi pisando la nuestra.
        raise GrowiAuthError(
            f"La sesión de Growi se cayó {intento} veces seguidas en {path}, "
            "reabriéndola cada vez. El login funciona, así que no son las "
            "credenciales: es la IP de salida rotando, o alguien más usando el "
            "mismo usuario de Growi al mismo tiempo."
        )


def _growi_relogin(account_id=None):
    """Tira la sesión guardada del CRM y abre una nueva. Para los casos que
    _growi_request no detecta como 'sesión caída': el CRM contesta 200 pero con
    HTML/vacío en vez del JSON esperado."""
    if account_id is None:
        account_id = session.get("account_id")
    cfg = _account_crm_cfg(account_id)
    _olvidar_sesion(cfg)
    return _abrir_sesion(cfg, account_id=account_id)


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
                crm_email=email,
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

    # Credenciales válidas y cuenta habilitada. La password NO se guarda: el
    # llamador la mete en el almacén en memoria de la sesión (ver
    # _guardar_credencial). Acá solo se limpia la sesión cacheada vieja de esta
    # cuenta, para que las próximas operaciones usen la recién validada.
    _olvidar_sesion_de_cuenta(acc["id"])
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


@app.before_request
def _invalidar_sesiones_viejas():
    """Si la cookie no trae el sello actual, la sesión es de un deploy anterior:
    se limpia y el usuario vuelve a loguear (una sola vez por sello)."""
    if session.get("logged_in") and session.get("stamp") != SESSION_STAMP:
        session.clear()


# Rutas que se sirven aunque no haya sesión (o justamente para recuperarla).
_SIN_SESION_CRM = {"login", "logout", "static", "ayuda_page", "ayuda_ordenes_page"}


def _sesion_crm_perdida():
    """True si la cookie dice "logueado" pero ya no tenemos la contraseña del CRM.

    Pasa siempre que se reinicia el web-service: `_CREDENCIALES` vive en memoria
    del proceso y la cookie de Flask sobrevive 30 días. El vendedor entraba, veía
    la pantalla completa, cargaba el link, elegía comentarios y recién ahí
    reventaba. El admin no tiene account_id (opera con la config del .env), así
    que no se le pide credencial en memoria.
    """
    if not session.get("logged_in"):
        return False
    account_id = session.get("account_id")
    if not account_id:
        return False
    return not _credencial_de_sesion(account_id)


@app.before_request
def _exigir_sesion_crm():
    """Manda al login ANTES de servir la página si la sesión del CRM ya no sirve.

    Va en before_request y no en cada vista a propósito: el chequeo reactivo
    (401 con `relogin`) solo salta cuando algo toca el CRM, o sea después de que
    el vendedor ya cargó el link y laburó. Acá se corta en el primer request.
    """
    if request.endpoint in _SIN_SESION_CRM:
        return None
    # Las /api/ NO se tocan acá, a propósito, y esto es más sutil de lo que
    # parece. Cortarlas de entrada rompía dos cosas:
    #   1) /api/stream/<job_id> es el único endpoint sin @require_login, justo
    #      para que la reconexión sobreviva a un reinicio; atajarlo tiraba la
    #      tanda ya generada y los tokens ya pagados.
    #   2) _respuesta_relogin() limpia la sesión, así que un GET cualquiera
    #      dejaba sin cookie al POST /api/publicar que venía después: moría en
    #      require_login ("No autenticado") sin llegar al except que guarda la
    #      orden en "Órdenes que no entraron". Se perdía justo lo que el carve-out
    #      de POST quería salvar.
    # Las APIs ya tienen su camino: CredencialAusente → _respuesta_relogin →
    # el interceptor de app.js manda al login. Acá solo se ataja la NAVEGACIÓN.
    if request.path.startswith("/api/"):
        return None
    if request.method not in ("GET", "HEAD"):
        return None
    if not _sesion_crm_perdida():
        return None
    # Sin limpiar la sesión: de eso se encarga login(). Limpiarla acá dejaba a
    # las otras pestañas sin cookie, y sus 401 pasaban a ser "No autenticado"
    # pelados —sin la marca `relogin`—, así que nadie las llevaba al login.
    volver = request.full_path if request.query_string else request.path
    return redirect(url_for("login", motivo="sesion_crm", next=volver))


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


def _recordar_usuario(resp, username):
    """Deja el usuario cifrado en una cookie propia para precargar el login. Si
    algo falla al cifrar (falta la clave), no rompe el login: se sigue sin
    recordar.

    Solo el usuario: la contraseña es la del CRM de Growi y no se guarda en
    ningún lado (ver REMEMBER_USER_COOKIE). El formato del payload se mantiene
    por compatibilidad con las cookies ya emitidas.
    """
    try:
        from common.crypto import encrypt as _encrypt
        blob = _encrypt(json.dumps({"u": username}))
    except Exception as e:
        print(f"[login] no pude recordar el usuario ({e})", flush=True)
        return resp
    resp.set_cookie(
        REMEMBER_USER_COOKIE, blob,
        max_age=REMEMBER_USER_DAYS * 24 * 3600,
        httponly=True,           # el JS de la página no puede leerla
        samesite="Lax",
        secure=app.config["SESSION_COOKIE_SECURE"],
    )
    return resp


def _usuario_recordado():
    """El usuario guardado en la cookie, o "" si no hay / no descifra.

    Las cookies emitidas por versiones anteriores traen además la contraseña en
    la clave "p": se ignora deliberadamente, no se lee ni se devuelve. La cookie
    se reescribe sin ella en el próximo login.
    """
    blob = request.cookies.get(REMEMBER_USER_COOKIE, "")
    if not blob:
        return ""
    try:
        from common.crypto import decrypt as _decrypt
        return json.loads(_decrypt(blob) or "{}").get("u", "")
    except Exception:
        # Cookie vieja (formato anterior), corrupta o cifrada con otra clave.
        return ""


@app.route("/login", methods=["GET", "POST"])
def login():
    # Sesión sin la contraseña del CRM: no sirve para operar, así que no se lo
    # rebota a la home (de ahí lo devolvían acá y quedaba un ida y vuelta).
    #
    # Y NO se limpia acá, aunque tiente: borrar la cookie desde el GET del login
    # se la borra a TODO el navegador, o sea también a las otras pestañas que el
    # vendedor dejó abiertas con la tanda ya generada. Sus fetch pasan a contestar
    # "No autenticado" pelado —sin la marca `relogin`—, así que el interceptor de
    # app.js no las lleva a ningún lado: quedan colgadas, y el POST de publicar
    # deja de guardar la orden en "Órdenes que no entraron". El POST de más abajo
    # pisa todas las claves igual, así que la sesión muerta no sobrevive al login.
    if session.get("logged_in") and not _sesion_crm_perdida():
        return redirect(url_for("index"))
    error = None
    error_kind = "error"
    # Si venció la sesión, el campo de usuario ya viene cargado; la contraseña
    # la tipea siempre (o la completa el navegador, si él la guardó).
    username = _usuario_recordado()
    olvidar = False
    status = 200
    # Llegó redirigido porque se quedó sin la contraseña del CRM en memoria. Sin
    # este aviso, el login aparece de la nada en medio de una carga de órdenes y
    # parece que se rompió algo.
    if request.method == "GET" and request.args.get("motivo") == "sesion_crm":
        error = ("Tu sesión venció y hay que abrirla de nuevo contra Growi. "
                 "Si estabas cargando una orden, quedó guardada: la vas a "
                 "encontrar en 'Órdenes que no entraron'.")
        error_kind = "info"
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
                session["stamp"] = SESSION_STAMP
                session["logged_in"] = True
                session["user_id"] = user["user_id"]
                session["account_id"] = user["account_id"]
                session["username"] = user["username"]
                session["is_admin"] = user["is_admin"]
                # La contraseña del CRM queda SOLO en memoria del proceso; en la
                # cookie viaja nada más que la clave que la referencia. Es lo
                # único que le permite al vendedor operar contra Growi, y muere
                # con el logout, el vencimiento de la sesión o un reinicio.
                if user["account_id"]:
                    session["cred_key"] = _guardar_credencial(user["account_id"], password)
                else:
                    # El admin no tiene cuenta: que no le quede colgada la clave
                    # de la sesión anterior (ahora que el login ya no hace clear).
                    session.pop("cred_key", None)
                # Al entrar, dejamos anotado en la cuenta el ID de vendedor que
                # el CRM le reconoce. Así el envío de órdenes lo lee de la base y
                # no depende de volver a parsear las campañas justo cuando el
                # vendedor aprieta Publicar. Va acá y no en _authenticate para
                # que valga por cualquier vía de login (Growi o usuario local).
                if user["account_id"]:
                    _refrescar_idvendedor(user["account_id"])
                destino = (request.form.get("next") or "").strip()
                if not (destino.startswith("/") and not destino.startswith("//")):
                    destino = url_for("index")
                return _recordar_usuario(redirect(destino), username)
            # Solo cuenta como intento de fuerza bruta la credencial equivocada.
            # Pendiente / restringido / CRM caído son credenciales válidas o un
            # problema nuestro: no penalizan al usuario.
            if auth_error is None:
                _login_register_fail(rate_key)
                # La credencial guardada ya no sirve (la cambió en el CRM):
                # borramos la cookie para no precargar siempre el usuario viejo.
                olvidar = True
            # auth_error explica el caso (pendiente de habilitación / acceso
            # restringido); sin él es un login fallido común.
            error = auth_error or MSG_CREDENCIALES
            # "Pendiente" no es un error del usuario: se muestra como aviso.
            error_kind = "info" if error == MSG_PENDIENTE else "error"
    resp = make_response(render_template("login.html", error=error, error_kind=error_kind,
                                         username=username,
                                         next=(request.values.get("next") or "")), status)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    if olvidar:
        resp.delete_cookie(REMEMBER_USER_COOKIE, samesite="Lax")
    return resp


@app.route("/logout")
def logout():
    # Primero la credencial (necesita leer la clave de la sesión), después la
    # sesión. Al revés, la contraseña quedaría huérfana en memoria hasta vencer.
    _olvidar_credencial()
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
    """Guía 1: cargar un cliente. PÚBLICA (sin login): es lo que se le manda a
    alguien que todavía no tiene usuario, y no muestra ningún dato real — las
    capturas son de clientes de demo."""
    return render_template(
        "ayuda.html",
        is_admin=session.get("is_admin", False),
        username=session.get("username", ""),
    )


@app.route("/ayuda-ordenes")
def ayuda_ordenes_page():
    """Guía 2: crear una orden. También pública. Va aparte de /ayuda porque son
    dos trabajos distintos: cargar el cliente una vez vs. laburar cada post."""
    return render_template(
        "ayuda-ordenes.html",
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


def _es_error_de_red(e: Exception) -> bool:
    """True si no llegamos al CRM (proxy caído, DNS, timeout), en vez de que el
    CRM nos haya contestado que no."""
    return isinstance(e, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout))


def _envio_pudo_haber_entrado(e: Exception) -> bool:
    """True si el POST llegó a salir y lo que falló fue esperar la respuesta.

    enviar_trafico.php NO es idempotente: si la orden entró y la reintentamos,
    se le cobra dos veces al cliente. En ese caso no se encola nada y lo mira un
    humano. Un ConnectTimeout/ProxyError, en cambio, es "nunca salió".
    """
    if isinstance(e, requests.exceptions.ReadTimeout):
        return True
    return not isinstance(e, (requests.exceptions.ConnectTimeout,
                              requests.exceptions.ProxyError,
                              requests.exceptions.ConnectionError))


def _seguro_de_reintentar(e: Exception) -> bool:
    """True si la orden se puede guardar para reintentarla sin riesgo de que se
    cargue dos veces.

    Dos familias, y hasta ahora solo se contemplaba la primera:

      1. No llegamos al CRM (proxy caído, DNS, connect timeout). El POST nunca
         salió.
      2. El CRM está bien pero NO ABRIMOS SESIÓN: el login falló, o no teníamos
         la contraseña en memoria. Acá tampoco salió nada — se frena antes de
         llegar a enviar_trafico.php.

    Faltaba la segunda, y es justo la que más se ve: un vendedor al que el CRM
    le rechaza la sesión perdía los comentarios ya generados y tenía que rehacer
    el post entero, aunque no se hubiera mandado absolutamente nada. En la
    pantalla de "Órdenes que no entraron" esas órdenes aparecían sin botón de
    reintentar, porque nunca se habían guardado.

    Lo que NO entra: que el POST haya salido y no sepamos si el CRM lo procesó
    (ReadTimeout, o la sesión cayéndose después de mandar). enviar_trafico.php no
    es idempotente y ahí reintentar le cobra dos veces al cliente.
    """
    if getattr(e, "pre_envio", False):
        return True
    return _es_error_de_red(e) and not _envio_pudo_haber_entrado(e)


@app.route("/api/publicar", methods=["POST"])
@require_login
def publicar():
    """Órdenes de COMENTARIOS.

    Salen por el mismo camino que el tráfico (`_enviar_ordenes_crm`): con las
    credenciales de la cuenta del vendedor, su idvendedor/idventa resueltos y la
    fecha en hora de Argentina. Antes esto se delegaba al openAIService, que
    mandaba con las credenciales globales del .env: la orden entraba al CRM de
    otra cuenta, así que el vendedor no la veía en su gestor y quedaba imputada
    a otro. El openAIService sigue generando los comentarios; lo único que se
    movió es el envío al CRM.
    """
    data = request.get_json()
    post_url    = data.get("url", "").strip()
    comentarios = data.get("comentarios", [])
    ordenes     = data.get("ordenes", [])
    cliente_ig  = data.get("client")

    if not post_url or not comentarios:
        return jsonify({"error": "Falta el link del post o los comentarios a publicar."}), 400
    if not ordenes:
        return jsonify({"error": "No hay ninguna orden para enviar."}), 400

    # Cada orden de comentarios usa su propia lista (verificados / no verificados)
    # y se baraja respetando los encabezados de género; si no trae, cae a la
    # lista global.
    ordenes_crm = [_ordenes.normalizar_orden(o, 0, comentarios) for o in ordenes]
    for o in ordenes_crm:
        if o.get("comentarios"):
            _ordenes.log_comentarios_debug(o.get("prod") or "comentarios", o["comentarios"])

    encolada = None
    try:
        crm, _, _ = _enviar_ordenes_crm(
            ordenes_crm,
            post_url=post_url,
            cliente_ig=cliente_ig,
            idventa_elegida=data.get("idventa"),
            account_id=session.get("account_id"),
            user_id=session.get("user_id"),
            username=session.get("username"),
        )
    except CredencialAusente as e:
        # Primero se guarda la orden (los comentarios ya están generados y no se
        # pueden perder por una sesión vencida), y recién después se manda al
        # login. Al volver, la reintenta de un click desde "Órdenes que no
        # entraron" en vez de rehacer el post.
        print(f"[publicar] sin credencial del CRM: {e}", flush=True)
        guardada = _encolar_pendiente(post_url, comentarios, ordenes_crm,
                                      cliente_ig, str(e),
                                      trace_id=getattr(e, "growi_trace_id", None))
        return _respuesta_relogin(
            "Tu sesión venció. Guardamos la orden con los comentarios ya "
            "generados: volvé a entrar y reintentala desde 'Órdenes que no "
            "entraron'." if guardada else None,
            encolada=bool(guardada),
            encolada_id=guardada.get("id") if guardada else None,
        )
    except Exception as e:
        print(f"[publicar] error: {e!r}", flush=True)
        error = _mensaje_de_error_de_envio(e)
        # No llegó a salir: en vez de perder los comentarios ya generados, la
        # orden queda guardada con su payload completo para que el vendedor la
        # reintente de un click.
        if _seguro_de_reintentar(e):
            encolada = _encolar_pendiente(post_url, comentarios, ordenes_crm,
                                          cliente_ig, error,
                                          trace_id=getattr(e, "growi_trace_id", None))
            if encolada:
                error = (f"{error}\n\nLa orden quedó guardada con los comentarios "
                         "ya generados: reintentala desde 'Órdenes que no "
                         "entraron'. No hace falta que la cargues de nuevo.")
        elif _envio_pudo_haber_entrado(e) and _es_error_de_red(e):
            error = ("Se cortó la conexión esperando la respuesta del CRM. "
                     "Revisá en Growi si la orden entró antes de volver a mandarla.")
        return jsonify({
            "informe": _ordenes.generar_informe(post_url, comentarios, error=error),
            "resultado": {
                "ok": False, "insertadas": 0, "messages": [], "warnings": [],
                "errors": [error],
                # Si el motivo es accionable, el front ofrece elegir otra
                # campaña y reintentar SOLO los comentarios (que ya están
                # generados) en vez de rehacer todo el flujo.
                "motivo": None if encolada else _motivo_de_error_de_envio(e),
                "encolada": bool(encolada),
                "encolada_id": encolada.get("id") if encolada else None,
            },
        })

    ok = bool(crm.get("success"))
    errores = list(crm.get("errors") or [])
    # Igual que en el tráfico: el consumo se anota con la orden ya aceptada.
    _registrar_uso_de_ordenes("publicar", ordenes_crm, crm,
                              post_url=post_url, cliente_ig=cliente_ig)
    return jsonify({
        "informe": _ordenes.generar_informe(
            post_url, comentarios, insertadas=crm.get("insertadas", 0),
            messages=crm.get("messages") or [], errors=errores, ok=ok),
        "resultado": {
            "ok": ok and not errores,
            "insertadas": crm.get("insertadas", 0),
            "messages": crm.get("messages") or [],
            "warnings": crm.get("warnings") or [],
            "errors": errores,
            "encolada": False,
            "encolada_id": None,
        },
    })


def _encolar_pendiente(post_url, comentarios, ordenes_crm, cliente_ig, error,
                       disponible=None, trace_id=None):
    """Guarda la orden en la cola de reintentos. Devuelve None si no hay DB, y
    en ese caso el vendedor ve el error de siempre: sin persistencia no podemos
    prometerle que se va a reenviar sola.

    Se guarda el account_id porque el reintento tiene que salir con las
    credenciales de ESA cuenta, no con las del .env."""
    if _repo is None:
        return None
    try:
        return _repo.encolar_orden(
            post_url,
            {"comentarios": comentarios, "ordenes": ordenes_crm,
             "disponible": disponible,
             # Con qué fila de auditoría se corresponde esta orden. Sin esto, el
             # mismo fallo se le muestra al vendedor dos veces: la fila de
             # `growi_calls` (sin botón) y la orden guardada (con botón).
             "trace_id": trace_id},
            account_id=session.get("account_id"),
            user_id=session.get("user_id"),
            client_ig_username=cliente_ig or "",
            error=error,
        )
    except Exception as e:
        print(f"[cola] no pude encolar la orden: {e!r}", flush=True)
        return None


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
    except CredencialAusente:
        raise    # que llegue al errorhandler y lo mande al login
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _mi_whatsapp() -> str:
    """A qué WhatsApp se le manda la tanda al que está logueado.

    El de SU cuenta. El del .env queda solo como respaldo para el admin de
    fallback (que no tiene cuenta) y para instalaciones viejas sin número
    cargado; si el vendedor tiene el suyo, ese manda siempre.
    """
    acc = session.get("account_id")
    if acc and _repo is not None:
        try:
            propio = _repo.get_wa_phone(acc)
            if propio:
                return propio
        except Exception as e:
            print(f"[wa] no pude leer el teléfono de la cuenta {acc}: {e!r}", flush=True)
    return REPARTO_WHATSAPP_TO


@app.route("/api/mi-whatsapp", methods=["GET", "POST"])
@require_login
def mi_whatsapp():
    """El vendedor carga y edita su propio número. No pasa por el admin: es un
    dato suyo y lo necesita la primera vez que reparte, no en un alta previa."""
    acc = session.get("account_id")
    if request.method == "GET":
        propio = ""
        if acc and _repo is not None:
            try:
                propio = _repo.get_wa_phone(acc)
            except Exception:
                propio = ""
        # `editable` distingue "todavía no cargó el suyo" de "no puede cargar
        # ninguno" (el admin de fallback no tiene cuenta a la que guardárselo).
        return jsonify({"telefono": propio, "editable": bool(acc),
                        "fallback": "" if propio else REPARTO_WHATSAPP_TO})

    if not acc:
        return jsonify({"error": "Tu usuario no tiene una cuenta asociada, "
                                 "así que no puedo guardar el número."}), 400
    d = request.get_json(silent=True) or {}
    try:
        n = _repo.set_wa_phone(acc, d.get("telefono", ""))
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    print(f"[wa] cuenta {acc} guardó su WhatsApp", flush=True)
    return jsonify({"telefono": n})


@app.route("/api/wa-estado", methods=["GET"])
@require_login
def wa_estado():
    """¿Está abierta la ventana de 24h? Y con qué número hay que hablarle al bot.

    `abierta: null` = no sabemos (el webhook no llega acá). Se muestra distinto
    de "cerrada" a propósito: afirmar un estado que no conocemos es lo que hizo
    que esto fuera tan difícil de diagnosticar.
    """
    destino = _mi_whatsapp()
    out = {"abierta": None, "numero_bot": WHATSAPP_NUMERO_BOT, "destino": destino}
    if not destino:
        out["motivo"] = "sin número cargado"
        return jsonify(out)
    try:
        r = requests.get(f"{WHATSAPP_SERVICE_URL}/estado-ventana",
                         params={"to": destino}, timeout=10)
        if r.ok:
            out.update(r.json())
    except Exception as e:
        print(f"[wa-estado] no pude consultar: {e!r}", flush=True)
    return jsonify(out)


@app.route("/api/activar-wa", methods=["POST"])
@require_login
def activar_wa():
    """Manda un TEMPLATE para poder abrir la ventana de 24h de Meta.

    Por qué hace falta: Meta solo entrega texto libre si el destinatario le
    escribió al bot en las últimas 24 horas. Con la ventana cerrada NO devuelve
    error — acepta el envío con HTTP 200 y descarta el mensaje después, avisando
    por webhook (que este proyecto no escucha). O sea: la tanda "sale bien" y no
    llega nada. Verificado en producción: 15 mensajes aceptados, cero entregados.

    Los templates sí atraviesan la ventana. Entonces: se manda uno, la persona
    lo responde, y con esa respuesta la ventana queda abierta por 24 horas.
    """
    destino = _mi_whatsapp()
    if not destino:
        return jsonify({"error": "Cargá tu número de WhatsApp primero."}), 400
    try:
        resp = requests.post(f"{WHATSAPP_SERVICE_URL}/send-template",
                             json={"to": destino,
                                   "template": WHATSAPP_TEMPLATE_ACTIVACION,
                                   "language": WHATSAPP_TEMPLATE_IDIOMA},
                             timeout=30)
        body = resp.json()
    except Exception as e:
        print(f"[activar-wa] falló: {e!r}", flush=True)
        return jsonify({"error": "No se pudo contactar al servicio de WhatsApp."}), 502
    if resp.status_code != 200:
        return jsonify({"error": body.get("message") or body.get("error")
                                 or "No se pudo mandar el mensaje de activación."}), 502
    return jsonify({"ok": True})


@app.route("/api/repartir-wa", methods=["POST"])
@require_login
def repartir_wa():
    """Manda el link del post + un mensaje por comentario al WhatsApp del vendedor.

    Llegan sueltos a propósito: desde ahí se reenvían con la selección múltiple
    de WhatsApp, que es lo que reemplaza al copiar-pegar de a uno.

    Van siempre al MISMO número (el del .env), sin importar qué vendedor apriete
    el botón. A dónde va cada tanda después lo decide quien recibe, en WhatsApp:
    el sistema no conoce ni guarda los destinos.
    """
    destino = _mi_whatsapp()
    if not destino:
        return jsonify({"error": "Cargá tu número de WhatsApp para poder "
                                 "recibir los comentarios."}), 400

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    comentarios = [c for c in (data.get("comentarios") or []) if (c or "").strip()]
    if not comentarios:
        return jsonify({"error": "No hay comentarios para repartir."}), 400

    # Formato exacto del que ya se usa a mano en los grupos: un primer mensaje
    # "Comentarios" con el link, y después cada comentario solo, sin numerar ni
    # nada alrededor. Eso importa: lo que llega es lo que se reenvía al grupo, y
    # un "1." o un prefijo terminaría pegado adentro del comentario en el post.
    mensajes = ([f"Comentarios\n{url}"] if url else []) + comentarios

    try:
        resp = requests.post(f"{WHATSAPP_SERVICE_URL}/send-bulk",
                             json={"to": destino, "mensajes": mensajes},
                             # 20 mensajes con 1s de pausa y 20s de timeout cada
                             # uno: el techo real está bastante abajo de esto.
                             timeout=180)
    except Exception as e:
        print(f"[repartir-wa] no pude hablar con whatsapp-service: {e!r}", flush=True)
        return jsonify({"error": "No se pudo contactar al servicio de WhatsApp."}), 502

    try:
        body = resp.json()
    except Exception:
        return jsonify({"error": "Respuesta inesperada del servicio de WhatsApp."}), 502

    _log_uso("repartir_wa", post_url=url, client_ig_username=data.get("client"),
             qty=body.get("enviados"))
    return jsonify(body), resp.status_code


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
    except CredencialAusente:
        raise    # que llegue al errorhandler y lo mande al login
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
    except CredencialAusente:
        raise    # que llegue al errorhandler y lo mande al login
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
    except CredencialAusente:
        raise    # que llegue al errorhandler y lo mande al login
    except Exception as e:
        # Segundo intento con login fresco: el CRM a veces contesta 200 con una
        # página en vez del JSON, y eso _growi_request no lo ve como sesión caída.
        print(f"[growi-web] productos falló ({e}); relogueo y reintento", flush=True)
        try:
            _growi_relogin()
            return jsonify(_traer())
        except CredencialAusente:
            raise
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
        # Un reloj NO puede mandar a nadie al login. Esto es una consulta
        # auxiliar del envío de tráfico: el front la pide y una línea después
        # postea /api/enviar_trafico, en el mismo click. Cuando acá salía un 401
        # con `relogin`, el interceptor navegaba al login en el acto y el POST
        # nunca llegaba a guardar la orden en "Órdenes que no entraron": el
        # vendedor perdía el trabajo por culpa de una consulta de la hora.
        # Se contesta la hora AR local, que es el mismo fallback que ya usa
        # _fecha_ar_crm, y el que decide mandar al login es el POST del envío.
        print(f"[server_time_ar] el CRM no me dio la hora ({e!r}); uso la AR local",
              flush=True)
        return jsonify({"ymdhmAR": _ordenes.ahora_ar_texto(), "fallback": True})


def _fecha_ar_crm(account_id=None):
    """La fecha del día EN ARGENTINA, que es la que tiene que llevar la orden.

    Se le pregunta al CRM (es el reloj que después usa el gestor para filtrar) y
    solo si no contesta se calcula localmente. Nunca `date.today()`: los
    contenedores corren en UTC, así que a partir de las 21:00 AR devolvía la
    fecha del día siguiente y la orden quedaba fuera del listado de hoy.
    """
    try:
        import random
        ts_resp = _growi_request(
            "GET", "/paginas/server_time_ar.php", account_id=account_id,
            params={"_": random.random()},
            headers={"referer": f"{_crm_base(account_id)}/paginas/trafico.php"},
        )
        fecha = (ts_resp.json().get("ymdhmAR") or "")[:10]  # "2026-06-25"
        if fecha:
            return fecha
        print("[fecha] el CRM no devolvió ymdhmAR; uso la hora AR local", flush=True)
    except Exception as e:
        print(f"[fecha] no pude pedirle la hora al CRM ({e!r}); uso la hora AR local", flush=True)
    return _ordenes.fecha_ar_hoy()


def _precio_real_de(o, account_id):
    """Le pregunta al CRM cuánto cuesta REALMENTE esta orden.

    El costo venía tal cual del navegador y se reenviaba al CRM sin mirarlo: con
    las devtools abiertas, cualquiera podía mandar `costo: 0` y el backend lo
    firmaba, incluido el `resto` de la campaña. Acá se recalcula contra
    obtenercostotrafico.php, que es la misma fuente que usa la pantalla.

    Devuelve None si no se puede averiguar (el CRM no contesta, la orden no trae
    producto). En ese caso NO se bloquea el envío: dejar a un vendedor sin poder
    trabajar porque una consulta auxiliar falló es peor que el riesgo que cubre,
    y el CRM valida el costo de su lado igual.
    """
    producto = o.get("producto_id") or o.get("productoId")
    redsocial = o.get("redsocial_id") or o.get("redsocialId")
    if not producto or not redsocial:
        return None
    try:
        cantidad = int(float(o.get("cantidad") or o.get("cant_inicial") or 0))
    except (TypeError, ValueError):
        return None
    if cantidad <= 0:
        return None
    try:
        resp = _growi_request(
            "POST", "/paginas/obtenercostotrafico.php", account_id=account_id,
            json={"redsocial": str(redsocial), "producto": str(producto),
                  "cant_solicitada": cantidad},
            headers={"referer": f"{_crm_base(account_id)}/paginas/trafico.php",
                     "content-type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        costo = (resp.json() or {}).get("costoTrafico")
        return None if costo is None else float(costo)
    except Exception as e:
        print(f"[precio] no pude verificar el costo de '{o.get('prod')}' ({e!r}); "
              f"uso el que mandó el front", flush=True)
        return None


# Diferencia que se tolera entre el costo del front y el del CRM. Los precios
# vienen con 4 decimales, así que esto solo absorbe el ruido del float.
_TOLERANCIA_COSTO = 0.0001


def _corregir_costo(o, account_id):
    """Pisa el costo de la orden con el del CRM cuando difieren."""
    real = _precio_real_de(o, account_id)
    if real is None:
        return
    try:
        declarado = float(o.get("costo") or 0)
    except (TypeError, ValueError):
        declarado = 0.0
    if abs(declarado - real) > _TOLERANCIA_COSTO:
        print(f"[precio] '{o.get('prod')}' x{o.get('cantidad')}: el front dijo "
              f"{declarado} y el CRM dice {real}. Mando el del CRM.", flush=True)
        o["costo"] = real


class CuentaSinCRM(RuntimeError):
    """La cuenta no tiene su propio CRM configurado, así que no sabemos a nombre
    de quién cargar la orden. Es un error a propósito: el fallback al .env
    mandaba la orden al CRM del dueño de la agencia, donde el vendedor no la veía
    y quedaba imputada a otro.

    `motivo` es para el front: "sin_campania" es el único caso que el vendedor
    puede resolver solo (eligiendo otra campaña y reintentando). El resto
    necesita que el admin le complete la ficha, y ofrecerle un botón de
    reintentar ahí sería mandarlo a chocar contra la misma pared.
    """

    def __init__(self, mensaje, motivo=None):
        super().__init__(mensaje)
        self.motivo = motivo


class SaldoInsuficiente(RuntimeError):
    """La campaña de la que salen los fondos no tiene con qué pagar esta orden.

    Se levanta ANTES de mandar nada: el CRM la rebotaría igual, pero lo hacía
    con un error suyo, ilegible, y recién después de que el vendedor esperara el
    round-trip. Peor: la orden entraba parcial (algunas líneas sí y otras no) y
    quedaba plata descontada por un envío que el vendedor daba por fallido.

    NO lleva `pre_envio = True` a propósito, aunque el POST efectivamente nunca
    salga: eso la encolaría, y la cola la reintentaría contra la MISMA campaña
    sin saldo hasta agotar los reintentos. Este error no se arregla esperando,
    se arregla eligiendo otra campaña o cargándole plata a esta. Por eso viaja
    con motivo="sin_saldo" y el front ofrece el selector de campañas.
    """

    motivo = "sin_saldo"

    def __init__(self, mensaje, *, saldo=None, costo=None, idventa=None):
        super().__init__(mensaje)
        self.saldo = saldo
        self.costo = costo
        self.idventa = idventa


def _cuenta_tiene_crm_propio(account_id) -> bool:
    """¿Esta cuenta tiene credenciales de CRM propias en la DB?

    Sin DB (_repo None) no hay multi-tenant: el .env ES la configuración válida y
    el sistema funciona como siempre. Con DB, en cambio, que una cuenta no tenga
    email de CRM significa que está a medio dar de alta, y mandar por el .env es
    justamente el bug que estamos arreglando.
    """
    if _repo is None:
        return True
    try:
        cfg = _repo.get_account_crm_config(account_id)
    except Exception as e:
        print(f"[growi-web] no pude leer la config de la cuenta {account_id}: {e!r}", flush=True)
        return False
    return bool(cfg and cfg.get("crm_email"))


def _enviar_ordenes_crm(ordenes, *, post_url="", cliente_ig="", **kw):
    """Envía y, si la orden se rebota ANTES de salir, deja igual la fila de
    auditoría.

    `trazar` solo cubre el round-trip HTTP, así que los rebotes de acá — la
    cuenta sin campaña activa, sin idvendedor, sin CRM propio, el proxy que ni
    conecta — no quedaban registrados en ningún lado: no están en el CRM porque
    nunca llegaron, y tampoco en `growi_calls`. El vendedor veía el error una
    vez en pantalla y después no había forma de saber cuántas órdenes se habían
    rebotado ni por qué.
    """
    with traza_rechazo_local(
            "enviar_trafico", f"{_crm_base(kw.get('account_id'))}{_PATH_ENVIO_ORDENES}",
            origen=kw.get("origen") or "web", account_id=kw.get("account_id"),
            user_id=kw.get("user_id"), username=kw.get("username"),
            post_url=post_url, client_ig_username=cliente_ig,
            # QUÉ se rebotó, no solo que se rebotó. Sin las órdenes la fila dice
            # "falló" y nada más, y reconstruir el reclamo obliga a que el
            # vendedor se acuerde de lo que había cargado.
            payload={
                "idventa_elegida": kw.get("idventa_elegida") or None,
                "cantidad_ordenes": len(ordenes),
                "ordenes": ordenes,
            }):
        return _enviar_ordenes_crm_impl(ordenes, post_url=post_url,
                                        cliente_ig=cliente_ig, **kw)


def _enviar_ordenes_crm_impl(ordenes, *, post_url="", cliente_ig="", idventa_elegida="",
                             disponible=None, account_id=None, user_id=None,
                             username=None, origen="web"):
    """Manda al CRM órdenes YA normalizadas (forma de enviar_trafico.php).

    Es el ÚNICO camino de salida de órdenes del panel: lo usan tanto el tráfico
    (followers, likes…) como los comentarios. Que sea uno solo es el punto: los
    comentarios salían por un cliente aparte con las credenciales globales del
    .env, y por eso aparecían cargados a nombre de otro vendedor y en otra
    campaña.

    Devuelve (data, texto, status) con la respuesta del CRM ya parseada. Las
    excepciones suben tal cual para que quien llama decida (el envío del panel
    las muestra; el de la cola las encola y reintenta).
    """
    # Antes de nada: ¿sabemos a nombre de quién va esta orden? Si la cuenta no
    # tiene CRM propio, la config cae al .env y la orden termina cargada en el
    # CRM del dueño de la agencia. Preferimos no mandar y decirlo.
    if account_id is not None and not _cuenta_tiene_crm_propio(account_id):
        raise CuentaSinCRM(
            "Tu cuenta todavía no tiene el CRM de Growi configurado, así que la "
            "orden no se puede cargar a tu nombre. Avisale al admin antes de "
            "volver a mandarla."
        )
    if account_id is None:
        # Admin operando sin cuenta elegida (o instalación sin DB): el .env es la
        # configuración legítima acá. Se deja dicho en el log para que, si
        # aparece una orden a nombre del dueño, se sepa de dónde salió.
        print("[growi-web] envío SIN account_id: sale con las credenciales del "
              ".env (admin o modo legacy)", flush=True)

    # De qué campaña salen los FONDOS: la asignada al cliente, si no la última
    # campaña de su propio perfil, y recién si no hay ninguna la de por defecto.
    # Antes iba fija la del .env y todo el tráfico se descontaba de la misma.
    # idventa: cuando el post no es de ningún cliente, el front deja elegir a
    # mano de cuál de las campañas propias sale la plata; esa elección manda.
    cfg = _account_crm_cfg(account_id)
    fondos = resolver_venta(account_id, cliente_ig, idventa_elegida=idventa_elegida,
                            refrescar=True)
    idventa, idvendedor = fondos["idventa"], fondos["idvendedor"]
    print(f"[fondos] @{cliente_ig or '—'} → idventa {idventa} "
          f"({fondos['origen']}: {fondos['detalle']}, saldo {fondos['saldo']})", flush=True)

    # Último recurso antes de frenar al vendedor: si el idvendedor no estaba
    # guardado (cuenta vieja que no volvió a loguearse) ni vino en la campaña,
    # se le pregunta al CRM en el momento y se guarda para la próxima. Cuesta un
    # request y solo pasa una vez por cuenta.
    if account_id is not None and idventa and not idvendedor:
        print(f"[fondos] sin idvendedor para la cuenta {account_id}; se lo pregunto "
              f"al CRM antes de cortar", flush=True)
        idvendedor = _idvendedor_del_crm(account_id)
        if idvendedor and _repo is not None:
            try:
                _repo.guardar_idvendedor(account_id, idvendedor)
            except Exception as e:
                print(f"[fondos] no pude guardar el idvendedor: {e!r}", flush=True)

    # Si aun así no se sabe a nombre de quién va la orden, se corta. Antes acá
    # había un fallback silencioso a los ids del .env: la orden salía por la
    # sesión del vendedor pero cargada al dueño de la agencia y descontada de su
    # campaña. Mejor no mandar y que alguien complete la ficha.
    if account_id is not None and not (idvendedor and idventa):
        falta = "el ID de vendedor" if not idvendedor else "la campaña"
        raise CuentaSinCRM(
            f"No pudimos determinar {falta} de tu cuenta en Growi, así que la "
            "orden no se puede cargar a tu nombre. Elegí una campaña y reintentá; "
            "si sigue igual, pedile al admin que complete tu ficha.",
            # Elegir una campaña también resuelve el caso del idvendedor: el
            # listado de campañas trae el `data-idvendedor` de cada una.
            motivo="sin_campania",
        )

    # El disponible que informamos al CRM es el saldo REAL de esa campaña; el
    # valor de config queda como respaldo si no se pudo leer.
    if disponible is None:
        if fondos["saldo"] is not None:
            disponible = fondos["saldo"]
        else:
            try:
                disponible = float(cfg.get("crm_disponible") or DISPONIBLE)
            except (TypeError, ValueError):
                disponible = DISPONIBLE
    disponible = float(disponible)

    crm_base = _crm_base(account_id)

    # El precio lo dice el CRM, no el navegador (ver _precio_real_de). Y una
    # orden programada sin fecha se programaría en la nada: se le pone la hora
    # de Argentina del momento, igual que hace el front con el tráfico.
    for o in ordenes:
        o["disponible"] = disponible
        _corregir_costo(o, account_id)
        # producto_id es nuestro, para poder consultar el precio: al CRM no le
        # va (enviar_trafico.php identifica el producto por nombre).
        o.pop("producto_id", None)
        o.pop("productoId", None)
        if o.get("programado") and not o.get("fecha_programada"):
            o["fecha_programada"] = _ordenes.ahora_ar_texto()
            print(f"[orden] '{o.get('prod')}' venía programada sin fecha; "
                  f"le pongo la hora AR de ahora ({o['fecha_programada']})", flush=True)

    costo_total = sum(float(o.get("costo", 0)) for o in ordenes)

    # ¿Alcanza la plata? Se chequea contra el saldo REAL leído del CRM recién
    # ahora (fondos["saldo"], con refrescar=True), no contra `disponible`: ese
    # cae a un valor de respaldo del .env cuando no se pudo leer la campaña, y
    # comparar contra un número inventado rebota órdenes buenas.
    #
    # Solo aplica a las órdenes que cuestan: una tanda de puros comentarios da
    # costo_total 0 y no toca el saldo, así que pasa aunque la campaña esté en
    # cero. Es el caso normal de un cliente al que solo se le comenta.
    saldo_real = fondos["saldo"]
    if saldo_real is not None and costo_total > 0 and costo_total > float(saldo_real):
        print(f"[fondos] CORTE por saldo: campaña {idventa} tiene "
              f"${float(saldo_real):.4f} y la orden cuesta ${costo_total:.4f}", flush=True)
        falta = costo_total - float(saldo_real)
        raise SaldoInsuficiente(
            f"La campaña #{idventa} no tiene saldo para esta orden: "
            f"cuesta ${costo_total:.2f} y quedan ${float(saldo_real):.2f} "
            f"(faltan ${falta:.2f}). No mandamos nada. Elegí otra campaña con "
            "saldo o cargale plata a esta y reintentá.",
            saldo=float(saldo_real), costo=costo_total, idventa=idventa,
        )

    payload = {
        "idvendedor": idvendedor,
        "idventa":    idventa,
        "fecha":      _fecha_ar_crm(account_id),
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

    # El contexto le pone nombre y apellido a la traza: de qué post, de qué
    # cliente y de qué campaña salió la plata. Sin esto, la fila del envío es un
    # POST anónimo y no se puede reconstruir un reclamo días después.
    with traza_contexto(origen=origen, account_id=account_id, user_id=user_id,
                        username=username, post_url=post_url,
                        client_ig_username=cliente_ig,
                        idventa=idventa, idvendedor=idvendedor,
                        costo=round(costo_total, 6)):
        resp = _growi_request(
            "POST", "/paginas/enviar_trafico.php", account_id=account_id,
            json=payload, headers=req_headers,
        )
        print(f"[enviar_trafico] respuesta CRM ({resp.status_code}): {resp.text[:2000]}", flush=True)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            # El CRM contestó algo que no es JSON (una página de error, por
            # ejemplo). No lo damos por bueno en silencio.
            raise RuntimeError(
                f"El CRM respondió algo inesperado (no es JSON): {resp.text[:200]}"
            )
        return data, resp.text, resp.status_code


def _respuesta_relogin(mensaje=None, **extra):
    """401 que le dice al front que mande al vendedor de vuelta al login.

    Cuando falta la contraseña del CRM en memoria, la sesión de Flask sigue
    perfectamente válida: `logged_in` está puesto y `require_login` deja pasar.
    Por eso el vendedor veía "volvé a iniciar sesión" y no pasaba nada — no había
    nada que lo llevara ahí: se marca `relogin` para que el front redirija en vez
    de pintar el mensaje en un cartel que no se puede accionar.

    NO se limpia la sesión, aunque antes sí se hacía. Limpiarla acá era lo que
    convertía este 401 en pérdida de trabajo: el request siguiente del mismo
    click (el POST de publicar / enviar_trafico) llegaba SIN cookie, moría en
    require_login con "No autenticado" y no alcanzaba el `except CredencialAusente`
    que guarda la orden en "Órdenes que no entraron". Y de paso le borraba la
    sesión a las otras pestañas, cuyos 401 pasaban a ser "No autenticado" pelados,
    sin esta marca, así que nadie las llevaba al login. La sesión sigue siendo una
    identidad válida: lo que falta es la contraseña del CRM, y de eso ya se
    ocupan _sesion_crm_perdida() al navegar y CredencialAusente al operar. El
    POST del login pisa todas las claves cuando el vendedor vuelve a entrar.
    """
    return jsonify({
        "error": mensaje or ("Tu sesión ya no tiene la contraseña de Growi. "
                             "Volvé a iniciar sesión para poder cargar órdenes."),
        "relogin": True,
        **extra,
    }), 401


@app.errorhandler(CredencialAusente)
def _manejar_credencial_ausente(e):
    """Red de contención: cualquier endpoint que toque el CRM sin la credencial
    en memoria manda al login, sin tener que acordarse de capturarlo uno por uno
    (son más de veinte los que hablan con el CRM)."""
    print(f"[auth] sin credencial del CRM en la sesión: {e}", flush=True)
    return _respuesta_relogin(str(e))


def _motivo_de_error_de_envio(e):
    """Código de motivo para el front (o None si no hay ninguno accionable).

    Los dos de hoy los resuelve el vendedor solo, eligiendo otra campaña y
    reintentando SOLO lo que falló, sin volver a generar los comentarios:
    "sin_campania" (no se pudo resolver de dónde sale la plata) y "sin_saldo"
    (se resolvió, pero la campaña no llega a cubrir la orden).
    """
    return getattr(e, "motivo", None)


def _mensaje_de_error_de_envio(e):
    """Traduce una excepción del envío a un mensaje para el vendedor.

    CuentaSinCRM y GrowiAuthError ya vienen escritos para él y dicen qué hacer.
    Los de red se resumen sin volcarle el traceback con la IP y el puerto del
    proxy, que es lo que se veía antes en pantalla.
    """
    if isinstance(e, (CuentaSinCRM, SaldoInsuficiente)):
        return str(e)
    if isinstance(e, CredencialAusente):
        # No es un rechazo del CRM: no tenemos su contraseña en memoria. El texto
        # ya está escrito para el vendedor y dice exactamente qué hacer.
        return str(e)
    if isinstance(e, GrowiAuthError):
        # El texto de GrowiAuthError está escrito para el admin (menciona la
        # ruta del CRM y el proxy). Al vendedor se le dice lo que le sirve; el
        # detalle técnico ya quedó en el log.
        print(f"[envío] login rechazado por el CRM: {e}", flush=True)
        return ("Growi no está aceptando tu sesión. Probá cerrar sesión y volver "
                "a entrar; si sigue igual, avisale al administrador.")
    if _envio_pudo_haber_entrado(e) and _es_error_de_red(e):
        return ("Se cortó la conexión esperando la respuesta del CRM. "
                "Revisá en Growi si la orden entró antes de volver a mandarla.")
    if _es_error_de_red(e):
        return ("No se puede conectar con el CRM de Growi en este momento. "
                "Es un problema de conexión, no de tus datos: probá de nuevo "
                "en unos minutos.")
    print(f"[envío] error no contemplado: {e!r}", flush=True)
    return ("No pudimos enviar la orden al CRM. Si vuelve a pasar, avisale al "
            "administrador.")


def _registrar_uso_de_ordenes(accion, ordenes, crm, *, post_url=None, cliente_ig=None):
    """Registra el consumo de una tanda YA ENVIADA.

    Solo si el CRM la aceptó: `used_quantities` no distingue envíos buenos de
    fallidos, así que grabar antes de tiempo le quema al vendedor esa cantidad
    para siempre. Si el CRM aceptó solo una parte, tampoco registramos: no
    sabemos CUÁL entró, y marcar de más es peor que marcar de menos (con marcar
    de menos, como mucho se repite un número).
    """
    if not crm or not crm.get("success"):
        print(f"[uso] la tanda no entró completa; no registro consumo ({accion})", flush=True)
        return
    insertadas = crm.get("insertadas")
    if insertadas is not None and insertadas < len(ordenes):
        print(f"[uso] el CRM tomó {insertadas} de {len(ordenes)} órdenes; "
              f"no registro consumo para no quemar cantidades que no salieron", flush=True)
        return
    for o in ordenes:
        try:
            qty = int(float(o.get("cantidad") or 0)) or None
        except (TypeError, ValueError):
            qty = None
        _log_uso(accion, post_url=post_url, client_ig_username=cliente_ig,
                 qty=qty, product_type=_tipo_producto(o.get("prod") or ""))


@app.route("/api/enviar_trafico", methods=["POST"])
@require_login
def enviar_trafico():
    data = request.get_json()
    ordenes = data.get("ordenes", [])
    if not ordenes:
        return jsonify({"error": "Sin órdenes"}), 400

    cliente_ig = data.get("client")

    try:
        crm, texto, status = _enviar_ordenes_crm(
            ordenes,
            post_url=data.get("url"),
            cliente_ig=cliente_ig,
            idventa_elegida=data.get("idventa"),
            disponible=data.get("disponible"),
            account_id=session.get("account_id"),
            user_id=session.get("user_id"),
            username=session.get("username"),
        )
    except CredencialAusente as e:
        # Igual que en /api/publicar: la orden se guarda antes de mandarlo al
        # login, así no pierde lo que había cargado por una sesión vencida.
        print(f"[enviar_trafico] sin credencial del CRM: {e}", flush=True)
        guardada = _encolar_pendiente(data.get("url"), [], ordenes, cliente_ig,
                                      str(e), disponible=data.get("disponible"),
                                      trace_id=getattr(e, "growi_trace_id", None))
        return _respuesta_relogin(
            "Tu sesión venció. Guardamos la orden: volvé a entrar y reintentala "
            "desde 'Órdenes que no entraron'." if guardada else None,
            encolada=bool(guardada),
            encolada_id=guardada.get("id") if guardada else None,
        )
    except Exception as e:
        print(f"[enviar_trafico] error: {e!r}", flush=True)
        error = _mensaje_de_error_de_envio(e)
        # Igual que en /api/publicar: si el envío no llegó a salir, la orden se
        # guarda en vez de perderse. Acá no hay comentarios generados por IA de
        # por medio, pero el vendedor igual armó la orden a mano, y hasta ahora
        # un login rebotado se la borraba y lo dejaba sin nada que reintentar.
        encolada = None
        if _seguro_de_reintentar(e):
            encolada = _encolar_pendiente(data.get("url"), [], ordenes,
                                          cliente_ig, error,
                                          disponible=data.get("disponible"),
                                          trace_id=getattr(e, "growi_trace_id", None))
            if encolada:
                error = (f"{error}\n\nLa orden quedó guardada: reintentala desde "
                         "'Órdenes que no entraron'.")
        return jsonify({"error": error,
                        "motivo": None if encolada else _motivo_de_error_de_envio(e),
                        "encolada": bool(encolada),
                        "encolada_id": encolada.get("id") if encolada else None}), 500

    # El consumo se registra RECIÉN ACÁ, con la orden ya aceptada. Antes se
    # grababa antes de mandar: si el CRM rechazaba o se caía la red, la cantidad
    # quedaba igual marcada como usada y la tirada automática no volvía a
    # proponerla nunca más para ese cliente. Se quemaban números con cada fallo.
    _registrar_uso_de_ordenes("enviar_trafico", ordenes, crm,
                              post_url=data.get("url"), cliente_ig=cliente_ig)
    return texto, status, {"Content-Type": "application/json"}


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
    # crm_password no está en la lista a propósito: el admin no carga ni cambia
    # la contraseña de Growi de nadie. Si viene en el body, se ignora.
    kwargs = {k: d[k] for k in (
        "name", "active", "crm_email", "crm_url",
        "crm_idvendedor", "crm_idventa", "crm_proxy", "crm_disponible",
    ) if k in d}
    # Si cambiaron credenciales, la sesión CRM cacheada quedó vieja. Se invalida
    # ANTES (con el email viejo, que es la clave con la que está guardada) y
    # DESPUÉS (por si el email no cambió y la entrada es la misma).
    _olvidar_sesion_de_cuenta(account_id)
    v = _repo.update_vendedor(account_id, **kwargs)
    _olvidar_sesion_de_cuenta(account_id)
    return jsonify({"vendedor": v})


@app.route("/api/admin/vendedores/<int:account_id>/estado", methods=["PATCH"])
@require_admin
@_repo_error_response
def admin_vendedores_estado(account_id):
    """Resuelve una solicitud de acceso: {"status": "approved"|"rejected"}. Es lo
    que el admin toca en el panel cuando le llega un vendedor nuevo."""
    d = request.get_json(silent=True) or {}
    v = _repo.set_vendedor_status(account_id, (d.get("status") or "").strip())
    _olvidar_sesion_de_cuenta(account_id)
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


def resolver_venta(account_id, ig_username, idventa_elegida=None, refrescar=False):
    """De dónde sale la plata para este cliente, en orden:
       0) la campaña elegida a mano en el envío (posts sin cliente),
       1) la campaña asignada a mano en Mis clientes,
       2) la ÚLTIMA campaña de su propio perfil de IG (lo normal),
       3) la campaña por defecto de la cuenta / .env — solo si no hay match.
    Devuelve dict con idventa, idvendedor, origen, detalle y saldo (None si no
    se pudo leer la campaña)."""
    ig = (ig_username or "").strip().lstrip("@").lower()
    cfg = _account_crm_cfg(account_id)

    # Los valores del .env son los del DUEÑO de la agencia. Para una cuenta de
    # vendedor no son un "default razonable": son los datos de otra persona, y
    # usarlos carga la orden a su nombre y la descuenta de SU campaña, aunque la
    # sesión del CRM sea la correcta. Por eso solo se aceptan cuando no hay
    # cuenta (admin operando sin vendedor elegido, o instalación sin DB).
    if account_id is None:
        env_venta, env_vendedor = GROWI_IDVENTA, GROWI_IDVENDEDOR
    else:
        env_venta, env_vendedor = "", ""

    # El idvendedor de la CUENTA manda. Se guarda al loguearse (ver
    # _refrescar_idvendedor) leyéndolo del propio CRM, así que es su valor real y
    # no depende de que el parseo de campañas funcione justo en el envío. El de
    # la campaña queda como respaldo: son el mismo número, porque el listado de
    # campañas está acotado al vendedor logueado.
    idvendedor_cuenta = (cfg.get("crm_idvendedor") or "").strip()

    default = {
        "idventa": cfg.get("crm_idventa") or env_venta,
        "idvendedor": idvendedor_cuenta or env_vendedor,
        "origen": "default",
        "detalle": "campaña por defecto de la cuenta",
        "saldo": None,
    }

    # refrescar=True lo usa el ENVÍO: el saldo va cacheado 5 minutos, y mandarle
    # al CRM un `disponible` viejo hace que el `resto` de la segunda orden de la
    # tanda salga mal. Para las pantallas, el caché sigue estando bien.
    ventas = []
    try:
        ventas = _traer_ventas(account_id, usar_cache=not refrescar)
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
                "idvendedor": idvendedor_cuenta or v["idvendedor"] or default["idvendedor"],
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
                    "idvendedor": (idvendedor_cuenta or (v or {}).get("idvendedor")
                                   or (cli or {}).get("crm_idvendedor") or default["idvendedor"]),
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
            "idvendedor": idvendedor_cuenta or v["idvendedor"] or default["idvendedor"],
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


@app.route("/api/venta-resuelta", methods=["GET"])
@require_login
def venta_resuelta():
    """De qué campaña saldría la plata para este cliente, y cuánto le queda.

    Es la MISMA resolución que hace el envío (resolver_venta), expuesta para que
    el paso de órdenes pueda mostrar de antemano la campaña asignada y su saldo.
    Antes el front solo sabía la campaña cuando el vendedor la elegía a mano, así
    que a un cliente con campaña asignada y sin plata no había forma de avisarle
    hasta que el envío ya había rebotado.

    ?ig= vacío es válido: es el post sin cliente, y ahí la resolución cae en la
    campaña por defecto (o en ninguna).
    """
    try:
        account_id = _target_account_id() if session.get("is_admin") else session.get("account_id")
    except Exception:
        account_id = session.get("account_id")

    ig = (request.args.get("ig") or "").strip().lstrip("@").lower()
    try:
        # Sin refrescar: esto corre al abrir el paso de órdenes y el caché de 5
        # minutos alcanza para mostrarlo. El número que MANDA es el que relee el
        # envío con refrescar=True; este es el aviso temprano.
        fondos = resolver_venta(account_id, ig)
        return jsonify({
            "idventa": fondos["idventa"],
            "origen":  fondos["origen"],
            "detalle": fondos["detalle"],
            "saldo":   fondos["saldo"],
        })
    except Exception as e:
        print(f"[fondos] no pude resolver la campaña de @{ig or '—'}: {e!r}", flush=True)
        return jsonify({"error": _mensaje_amigable(e)}), 502


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


# ── Trazabilidad del CRM: qué le mandamos a Growi y qué contestó ─────────────

@app.route("/api/growi-calls", methods=["GET"])
@require_login
@_repo_error_response
def growi_calls_list():
    """Últimas llamadas al CRM. El admin ve todas; el vendedor, solo las suyas.

    Es la respuesta a "mandé la orden y no entró": acá está el payload exacto,
    la respuesta cruda del CRM y el status, sin depender de logs rotados.
    """
    account_id = None if session.get("is_admin") else session.get("account_id")
    return jsonify({
        "calls": _repo.list_growi_calls(
            account_id,
            operacion=(request.args.get("operacion") or "").strip() or None,
            solo_errores=request.args.get("errores") == "1",
            q=(request.args.get("q") or "").strip() or None,
            trace_id=(request.args.get("trace_id") or "").strip() or None,
            limite=request.args.get("limite", 200),
        ),
        "resumen": _repo.growi_calls_resumen(account_id),
    })


@app.route("/api/ordenes-pendientes/<int:orden_id>/reintentar", methods=["POST"])
@require_login
@_repo_error_response
def ordenes_pendientes_reintentar(orden_id):
    """Reintento manual de una orden que quedó frenada en la cola.

    Solo la cola: `pending_orders` guarda el payload completo (comentarios
    incluidos), así que reenviarla es reusar el envío original. Los envíos
    rebotados que NO llegaron a encolarse tienen solo la traza de auditoría —
    de esos no quedaron los comentarios, y por eso no se pueden reintentar desde
    el historial.

    El envío sale ACÁ, sincrónico. Antes se devolvía la orden a la cola y la
    despachaba el worker de fondo; ese worker ya no existe, porque reintentar
    necesita loguearse al CRM y la contraseña del vendedor solo está en memoria
    mientras él está logueado. Este request es justamente ese momento.

    El claim atómico contra el envío duplicado sigue estando: lo hace
    `tomar_orden_puntual` antes de mandar nada.
    """
    # El reintento sale con la contraseña del que está logueado, y solo tenemos
    # la suya: el admin ya no puede reintentar la orden de un vendedor por él.
    # Se corta acá, ANTES del claim, para no dejarle la orden marcada 'enviando'
    # a alguien que no la puede mandar. Que la reintente su dueño, que es quien
    # tiene la credencial.
    account_id = None if session.get("is_admin") else session.get("account_id")
    if account_id is None:
        propietario = _repo.get_pending_order_account(orden_id)
        if propietario != session.get("account_id"):
            return jsonify({"error": "Esta orden la tiene que reintentar el "
                                     "vendedor: sale con su usuario de Growi y "
                                     "su contraseña no la guarda el sistema."}), 403

    orden = _repo.tomar_orden_puntual(orden_id, account_id)
    if not orden:
        return jsonify({"error": "No se pudo reintentar (ya salió, o hay un "
                                 "envío en curso)"}), 400

    try:
        crm = _reenviar_orden_de_cola(orden)
    except Exception as e:
        # Reintentable=False salvo que sepamos que NO salió: si el POST pudo
        # haber entrado, la orden queda a revisión y no se vuelve a mandar sola.
        _repo.reprogramar_orden(orden_id, f"{e.__class__.__name__}: {e}",
                                reintentable=False)
        print(f"[reintento] orden {orden_id} falló: {e!r}", flush=True)
        return jsonify({"error": _mensaje_de_error_de_envio(e)}), 502

    if crm and crm.get("success"):
        _repo.marcar_orden_enviada(orden_id)
        return jsonify({"ok": True, "insertadas": crm.get("insertadas", 0)})

    errores = "; ".join((crm or {}).get("errors") or []) or "el CRM rechazó la orden"
    _repo.reprogramar_orden(orden_id, errores, reintentable=False)
    return jsonify({"error": errores}), 502


# Motivos en criollo para la pantalla del vendedor. Se buscan como subcadena
# sobre el error y el cuerpo que contestó el CRM, en orden: el primero que
# engancha manda. Lo que no engancha con nada cae en "el CRM la rechazó", que es
# honesto: preferimos eso antes que inventarle una causa.
_MOTIVOS_ENVIO = (
    ("sin_campania",  ("no pudimos determinar la campaña", "no pudimos determinar el id",
                       "cuentasincrm", "elegí una campaña"),
     "No había una campaña activa a la que cargarle la orden"),
    ("sin_crm",       ("todavía no tiene el crm",),
     "Tu cuenta no tiene el CRM configurado"),
    # Va ANTES de "sesion": su texto contiene la palabra "sesión" y si no,
    # quedaría clasificado como "el CRM cortó la sesión", que manda a revisar la
    # contraseña cuando lo único que hay que hacer es volver a entrar.
    ("sin_credencial", ("credencialausente", "ya no tiene la contraseña"),
     "Tu sesión venció: volvé a entrar y reintentala"),
    ("saldo",         ("saldo", "disponible", "insuficiente"),
     "No alcanzaba el saldo de la campaña"),
    ("sesion",        ("401", "login", "growiautherror", "sesión"),
     "El CRM cortó la sesión"),
    ("red",           ("timeout", "connection", "proxy", "growiunavailable",
                       "no se pudo conectar", "conexión", "conexion"),
     "No había conexión con el CRM"),
)


def _motivo_envio_criollo(*textos) -> tuple:
    """(codigo, texto) del motivo de un envío fallido, para mostrarle al vendedor."""
    plano = " ".join(t for t in textos if t).lower()
    for codigo, agujas, criollo in _MOTIVOS_ENVIO:
        if any(a in plano for a in agujas):
            return codigo, criollo
    return "rechazado", "El CRM rechazó la orden"


# Errores que se levantan ANTES de que el POST salga. Se buscan como subcadena
# sobre el error guardado en la traza: cuando el envío se frenó por uno de estos,
# a enviar_trafico.php no llegó nada y reenviarlo no puede duplicar la orden.
_ERRORES_PRE_ENVIO = (
    "growiautherror", "credencialausente", "cuentasincrm",
    "connecttimeout", "proxyerror", "connectionerror",
)


def _insertadas_de_traza(call) -> int:
    """Cuántas órdenes dijo el CRM que insertó en esa llamada. -1 si no se sabe.

    Es el dato que decide si un rebote se puede remandar: si el CRM llegó a
    insertar aunque sea una, reenviar la tanda la carga de nuevo y se le cobra
    dos veces al cliente.
    """
    cuerpo = call.get("response_snippet") or ""
    if not cuerpo:
        return -1
    try:
        data = json.loads(cuerpo)
    except Exception:
        # El snippet son los primeros 300 caracteres: si el JSON venía más
        # largo, no parsea y no podemos afirmar nada.
        m = re.search(r'"insertadas"\s*:\s*(\d+)', cuerpo)
        return int(m.group(1)) if m else -1
    return int(data.get("insertadas") or 0) if isinstance(data, dict) else -1


def _reintento_de_traza(call):
    """(se_puede_reintentar, hay_que_avisar_de_duplicado) para un envío rebotado.

    Tres situaciones:
      - El CRM contestó y dijo que insertó 0  → seguro: rechazó la tanda entera.
      - El fallo fue antes de mandar          → seguro: nunca salió.
      - Cualquier otra cosa                   → pudo haber entrado. Se deja
        reintentar, pero avisando: el vendedor es el único que puede mirar en
        Growi si la orden está o no.

    Y un caso donde NO se ofrece el botón: si el CRM insertó al menos una. Ahí
    reenviar duplica seguro, y no hay forma de mandar "solo las que faltaron"
    porque el CRM no dice cuáles entraron.
    """
    if not call.get("ordenes_guardadas"):
        return False, False          # sin órdenes guardadas no hay qué reenviar
    insertadas = _insertadas_de_traza(call)
    if insertadas > 0:
        return False, False
    if insertadas == 0:
        return True, False
    error = (call.get("error") or "").lower()
    if any(p in error for p in _ERRORES_PRE_ENVIO):
        return True, False
    return True, True


@app.route("/api/mis-envios-fallidos", methods=["GET"])
@require_login
@_repo_error_response
def mis_envios_fallidos():
    """Los envíos del vendedor que NO entraron, con el motivo en criollo.

    Junta las dos fuentes porque para el vendedor son la misma cosa: la
    auditoría de lo que el CRM rechazó (`growi_calls` con ok=false, que incluye
    los rebotes que nunca llegaron a salir) y las órdenes que quedaron en la
    cola esperando revisión (`pending_orders`). En el CRM de Growi ninguna de
    las dos aparece — justamente porque no entraron — así que esta pantalla es
    el único lugar donde el vendedor puede verlas.
    """
    account_id = None if session.get("is_admin") else session.get("account_id")
    items = []

    # Las órdenes que quedaron guardadas van primero: de paso se juntan los
    # trace_id que ya están representados por una orden reintentable, para no
    # listar además su fila de auditoría. Son el MISMO fallo, y mostrarlo dos
    # veces —una con botón y otra sin— hace parecer que rebotó dos órdenes.
    pendientes = _repo.list_pending_orders(account_id, estados=["revisar", "fallida"])
    ya_listados = {(o.get("payload") or {}).get("trace_id")
                   for o in pendientes} - {None}

    for c in _repo.list_growi_calls(account_id, solo_errores=True, limite=50):
        if c.get("trace_id") and c["trace_id"] in ya_listados:
            continue
        codigo, criollo = _motivo_envio_criollo(c.get("error"), c.get("response_snippet"))
        # La traza del envío guarda las órdenes COMPLETAS, con sus comentarios
        # adentro, así que un rebote se puede remandar tal cual sin regenerar
        # nada. (Acá decía que los comentarios "nunca se persistieron" y por eso
        # no había botón: es falso, están en request_payload.ordenes.)
        reintentable, aviso = _reintento_de_traza(c)
        items.append({
            "id": c["id"], "tipo": "envio", "fecha": c.get("created_at"),
            "motivo": codigo, "detalle": criollo,
            "post_url": c.get("post_url"), "cliente": c.get("client_ig_username"),
            "idventa": c.get("idventa"), "costo": c.get("costo"),
            "tecnico": c.get("error") or f"HTTP {c.get('status_code') or '—'}",
            "ordenes": c.get("ordenes_guardadas") or 0,
            "reintentable": reintentable,
            "aviso_duplicado": aviso,
        })

    # "revisar" = el envío pudo haber entrado, ojo con reintentar; "fallida" = no
    # salió. Las "pendiente"/"enviando" no van: hay un envío en curso o recién
    # reclamado, y mostrarlo como fallo asusta al vendedor al pedo.
    for o in pendientes:
        codigo, criollo = _motivo_envio_criollo(o.get("ultimo_error"))
        items.append({
            "id": o["id"], "tipo": "cola", "fecha": o.get("created_at"),
            "motivo": codigo, "detalle": criollo,
            "post_url": o.get("post_url"), "cliente": o.get("client_ig_username"),
            "idventa": None, "costo": None,
            "tecnico": o.get("ultimo_error") or "",
            # La cola sí guarda el payload completo, así que reintentar es
            # reenviar el envío original sin regenerar nada.
            "reintentable": True,
            # 'revisar' es el estado en que el envío PUDO haber entrado (se cortó
            # esperando la respuesta). Ahí el front pide confirmación antes de
            # reenviar: la orden duplicada se la cobran al cliente.
            "aviso_duplicado": o.get("estado") == "revisar",
        })

    items.sort(key=lambda i: i.get("fecha") or "", reverse=True)
    return jsonify({"envios": items, "total": len(items)})


@app.route("/api/growi-calls/<int:call_id>/reintentar", methods=["POST"])
@require_login
@_repo_error_response
def growi_call_reintentar(call_id):
    """Vuelve a mandar un envío que rebotó, reusando las órdenes de su traza.

    Antes esto no existía: de un rebote quedaba la traza y el vendedor tenía que
    rehacer el post entero. Pero la traza guarda las órdenes COMPLETAS (con sus
    comentarios), así que reenviar es mandar exactamente lo mismo.

    Sale con las credenciales del que está logueado y por el camino normal, así
    que resuelve campaña y precio como cualquier envío. El único caso que no se
    ofrece es aquel en que el CRM ya insertó algo: ahí reenviar duplica y no hay
    forma de mandar "solo lo que faltó".
    """
    account_id = None if session.get("is_admin") else session.get("account_id")
    call = _repo.get_growi_call(call_id, account_id)
    if not call:
        return jsonify({"error": "No existe ese envío"}), 404

    # Mismo criterio que pinta el botón: si acá no da, es que el listado se
    # desactualizó (o alguien llamó al endpoint a mano).
    reintentable, _ = _reintento_de_traza(call)
    if not reintentable:
        insertadas = _insertadas_de_traza(call)
        if insertadas > 0:
            return jsonify({"error": f"Ese envío ya cargó {insertadas} orden/es en "
                                     "el CRM. Reenviarlo se las cobraría dos veces "
                                     "al cliente."}), 409
        return jsonify({"error": "De ese envío no quedaron las órdenes guardadas, "
                                 "así que no hay nada que reenviar."}), 400

    payload = call.get("request_payload") or {}
    ordenes = [_ordenes.normalizar_orden(o, 0, o.get("comentarios") or [])
               for o in (payload.get("ordenes") or [])]
    if not ordenes:
        return jsonify({"error": "Ese envío no tiene órdenes que reenviar"}), 400

    # La cuenta es la del envío original, no la del que aprieta el botón: si el
    # admin reintenta el rebote de un vendedor, la orden tiene que seguir siendo
    # de ese vendedor. Pero la contraseña que se usa es la del que está logueado,
    # así que un admin no puede reintentar por otro (no tenemos su credencial).
    dueño = call.get("account_id")
    if account_id is None and dueño != session.get("account_id"):
        return jsonify({"error": "Este envío lo tiene que reintentar el vendedor: "
                                 "sale con su usuario de Growi."}), 403

    try:
        crm, _, _ = _enviar_ordenes_crm(
            ordenes,
            post_url=call.get("post_url") or "",
            cliente_ig=call.get("client_ig_username") or "",
            idventa_elegida=payload.get("idventa_elegida") or call.get("idventa"),
            account_id=dueño,
            user_id=call.get("user_id"),
            username=call.get("username"),
            origen="reintento",
        )
    except Exception as e:
        print(f"[reintento-traza] envío {call_id} falló: {e!r}", flush=True)
        return jsonify({"error": _mensaje_de_error_de_envio(e)}), 502

    if crm and crm.get("success"):
        return jsonify({"ok": True, "insertadas": crm.get("insertadas", 0)})
    errores = "; ".join((crm or {}).get("errors") or [])
    return jsonify({"error": errores or "El CRM rechazó la orden otra vez, sin "
                                        "decir el motivo."}), 502


@app.route("/api/growi-calls/<int:call_id>", methods=["GET"])
@require_login
@_repo_error_response
def growi_call_detail(call_id):
    """Detalle con los cuerpos completos (request y response)."""
    account_id = None if session.get("is_admin") else session.get("account_id")
    call = _repo.get_growi_call(call_id, account_id)
    if not call:
        return jsonify({"error": "No existe esa llamada"}), 404
    return jsonify({"call": call})


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


@app.route("/api/sesion-viva", methods=["GET"])
@require_login
def sesion_viva():
    """¿Esta sesión todavía sirve para operar contra el CRM?

    Existe por el caso que el redirect al abrir la página NO cubre: el vendedor
    deja el panel abierto toda la jornada, el servicio se reinicia de noche, y a
    la mañana no recarga — pega el link y genera. Como nunca navega, el guard de
    _exigir_sesion_crm no llega a correr, y se enteraba recién en el paso de
    órdenes, con los comentarios ya generados y los tokens ya gastados.

    Es una consulta barata (mira el diccionario en memoria, no toca el CRM) que
    sesion.js dispara cuando la pestaña vuelve al foco.
    """
    if _sesion_crm_perdida():
        return _respuesta_relogin()
    return jsonify({"ok": True})


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


def _reenviar_orden_de_cola(orden):
    """Reintento de una orden encolada, con las credenciales de SU cuenta.

    Todo va con account_id explícito (`_growi_request` y `resolver_venta` ya
    están preparados para eso) en vez de leerlo de la sesión: la orden puede ser
    de una cuenta distinta a la del que dispara el reintento (el admin reintenta
    órdenes ajenas), y con la sesión saldría cargada en el CRM equivocado.
    """
    payload = orden.get("payload") or {}

    # Sin cuenta no hay reintento automático. Un reenvío desatendido que no sabe
    # de quién es la orden terminaría cargándola con las credenciales del .env,
    # en el CRM equivocado. Va a revisión y la reenvía un humano desde el panel.
    if orden.get("account_id") is None:
        raise CuentaSinCRM(
            f"La orden {orden.get('id')} no tiene cuenta asociada: no se puede "
            "reenviar sola sin arriesgar cargarla en el CRM equivocado."
        )

    # Las órdenes encoladas por versiones viejas quedaron guardadas con la forma
    # del frontend (link/productoNombre) en vez de la del CRM (url/prod).
    # normalizar_orden respeta las que ya vienen en forma de CRM, así que se
    # puede aplicar a todas sin miedo a tocar las nuevas.
    ordenes = [_ordenes.normalizar_orden(o, 0, payload.get("comentarios") or [])
               for o in (payload.get("ordenes") or [])]

    crm, _, _ = _enviar_ordenes_crm(
        ordenes,
        post_url=orden.get("post_url") or "",
        cliente_ig=orden.get("client_ig_username") or "",
        disponible=payload.get("disponible"),
        account_id=orden.get("account_id"),
        user_id=orden.get("user_id"),
        # origen="cola": en el panel de auditoría hay que poder distinguir un
        # reintento de la cola de un vendedor apretando Publicar dos veces.
        origen="cola",
    )
    return crm


def _arrancar_cola():
    """El worker de reintentos automáticos está APAGADO y no se arranca.

    Reintentar solo exige loguearse al CRM sin el vendedor delante, y para eso
    hacía falta tener su contraseña guardada. Como ya no se guarda (vive en
    memoria mientras él está logueado), un reintento de fondo no tendría con qué
    autenticarse: fallaría siempre y llenaría la cola de intentos muertos.

    Las órdenes que no entran NO se pierden: quedan en `pending_orders` y el
    vendedor las ve en "Órdenes que no entraron", con el botón de reintentar
    (POST /api/ordenes-pendientes/<id>/reintentar). Ese reintento corre dentro de
    su request, así que tiene su credencial en memoria y sale con sus datos.

    Queda el rescate de las que quedaron colgadas en 'enviando' por un reinicio:
    sin eso, esas filas no las mira nadie —el claim solo busca 'pendiente'— y el
    vendedor no las vería ni podría reintentarlas a mano.
    """
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false" or _repo is None:
        return
    try:
        _repo.revisar_ordenes_colgadas()
    except Exception as e:
        print(f"[cola] no pude revisar las órdenes colgadas: {e!r}", flush=True)
    print("[cola] reintento automático desactivado: las órdenes fallidas las "
          "reintenta el vendedor desde la pantalla", flush=True)


_arrancar_cola()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
