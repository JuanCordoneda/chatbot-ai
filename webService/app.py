from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, make_response
from functools import wraps
import requests
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "growi-secret-2026")
# Recargar templates ante cambios sin reiniciar el proceso (dev / edición en caliente).
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

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
_GROWI_PROXY_URL = os.environ.get("GROWI_HTTP_PROXY", "")
_GROWI_PROXIES = {"http": _GROWI_PROXY_URL, "https": _GROWI_PROXY_URL} if _GROWI_PROXY_URL else None
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
                return cfg
        except Exception as e:
            print(f"[growi-web] no pude leer config de cuenta {account_id} ({e})", flush=True)
    return _env_crm_cfg()


def _growi_login_with(cfg):
    """Abre una sesión autenticada contra el CRM con las credenciales dadas."""
    s = requests.Session()
    s.headers.update({"user-agent": _GROWI_USER_AGENT})
    proxy = cfg.get("crm_proxy")
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    url = cfg.get("crm_url") or GROWI_CRM_URL
    s.post(
        f"{url}/cuenta/login.php",
        data={"correo": cfg.get("crm_email", ""), "password": cfg.get("crm_password", "")},
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "referer": f"{url}/cuenta/login.php",
            "origin": url,
        },
        timeout=15,
    )
    return s


def _growi_validate_credentials(cfg):
    """True si las credenciales loguean correctamente en el CRM. Se comprueba
    pidiendo una página que exige sesión (trafico.php): 200 = adentro; un redirect
    al login = credenciales inválidas. Mismo patrón que openAIService/growi_client."""
    url = cfg.get("crm_url") or GROWI_CRM_URL
    try:
        s = _growi_login_with(cfg)
        check = s.get(f"{url}/paginas/trafico.php", allow_redirects=False, timeout=15)
        return check.status_code == 200
    except Exception as e:
        print(f"[auth] error validando credenciales de Growi ({e})", flush=True)
        return False


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


def _growi_request(method, path, account_id=None, **kwargs):
    """GET/POST autenticado contra el CRM de la cuenta logueada, reintentando con
    login fresco ante 401. account_id explícito o el de la sesión."""
    if account_id is None:
        account_id = session.get("account_id")
    timeout = kwargs.pop("timeout", 15)
    entry = _get_growi_session(account_id)
    for intento in range(1, 5):
        url = entry["cfg"].get("crm_url") or GROWI_CRM_URL
        resp = entry["session"].request(
            method, f"{url}{path}", timeout=timeout, **kwargs
        )
        if resp.status_code != 401:
            return resp
        print(f"[growi-web] 401 en intento {intento}/4 para {path} (cuenta {account_id}), relogueando", flush=True)
        cfg = _account_crm_cfg(account_id)
        entry = {"session": _growi_login_with(cfg), "cfg": cfg}
        _growi_sessions[account_id] = entry
    return resp


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


def _authenticate_vendedor(email, password):
    """Login del vendedor por credenciales de Growi. Busca la cuenta por su email
    de Growi y valida la password EN VIVO contra el CRM. Si es válida, refresca la
    password guardada (cifrada) y devuelve el dict de sesión. None si no aplica."""
    if _repo is None:
        return None
    try:
        acc = _repo.get_account_by_crm_email(email)
    except Exception as e:
        print(f"[auth] no pude buscar la cuenta por email ({e})", flush=True)
        return None
    if not acc:
        return None
    cfg = {
        "crm_url": acc.get("crm_url") or GROWI_CRM_URL,
        "crm_email": email,
        "crm_password": password,
        "crm_proxy": acc.get("crm_proxy"),
    }
    if not _growi_validate_credentials(cfg):
        return None
    # Credenciales válidas: refrescamos la password guardada y limpiamos cualquier
    # sesión cacheada vieja de esta cuenta para que las próximas operaciones usen
    # la password recién validada.
    try:
        _repo.update_account_crm_password(acc["id"], password)
    except Exception as e:
        print(f"[auth] no pude refrescar la password del CRM ({e})", flush=True)
    _growi_sessions.pop(acc["id"], None)
    return {"user_id": None, "account_id": acc["id"],
            "username": acc.get("crm_email") or email, "is_admin": False}


def _authenticate(identifier, password):
    """Devuelve un dict de sesión o None. Orden: usuario de la DB (admin o
    vendedor) → fallback anti-lockout del .env → vendedor por credenciales de Growi."""
    user = _authenticate_db_user(identifier, password)
    if user:
        return user
    admin = _authenticate_admin_fallback(identifier, password)
    if admin:
        return admin
    return _authenticate_vendedor(identifier.strip().lower(), password)


def _current_user():
    return {
        "user_id": session.get("user_id"),
        "account_id": session.get("account_id"),
        "username": session.get("username"),
        "is_admin": session.get("is_admin", False),
    }


def _tipo_producto(nombre):
    """Clasifica el nombre del producto del CRM en likes/views/shares (o None).
    Espeja _tipoProducto() del front para que lo registrado coincida con lo que
    la tirada automática consulta después."""
    n = (nombre or "").lower()
    if "like" in n or "me gusta" in n:
        return "likes"
    if any(k in n for k in ("view", "reproduc", "visualiz", "vista")):
        return "views"
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
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = _authenticate(username, password)
        if user:
            session["logged_in"] = True
            session["user_id"] = user["user_id"]
            session["account_id"] = user["account_id"]
            session["username"] = user["username"]
            session["is_admin"] = user["is_admin"]
            return redirect(url_for("index"))
        error = "Usuario o contraseña incorrectos"
    resp = make_response(render_template("login.html", error=error, username=username))
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
    resp = make_response(render_template("index.html", is_admin=session.get("is_admin", False)))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/api/procesar", methods=["POST"])
@require_login
def procesar():
    data = request.get_json()
    post_url = data.get("url", "").strip()
    if not post_url:
        return jsonify({"error": "Falta el link de Instagram"}), 400

    # "Cargar más" manda los comentarios ya generados para que no se repitan.
    evitar = data.get("evitar", []) or []

    # Registro de uso: solo la primera tanda (no cada "Cargar más").
    if not evitar:
        _log_uso("generar", post_url=post_url, client_ig_username=data.get("client"))

    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/procesar_post",
            json={"url": post_url, "evitar": evitar},
            timeout=30,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        print(f"[procesar] error: {e!r}", flush=True)
        return jsonify({"error": _mensaje_amigable(e)}), 502


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
            json={"url": post_url, "comentarios": comentarios, "ordenes": ordenes, "disponible": disponible},
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
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_productos_con_precios.php",
            params={"rrss": rrss_id},
            headers={"referer": f"{_crm_base()}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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

    # Credenciales del CRM del vendedor logueado (idvendedor/idventa/disponible
    # propios). Cae al .env global si la cuenta no los tiene cargados.
    cfg = _account_crm_cfg(session.get("account_id"))
    idvendedor = cfg.get("crm_idvendedor") or GROWI_IDVENDEDOR
    idventa    = cfg.get("crm_idventa") or GROWI_IDVENTA
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
    resp = make_response(render_template("admin.html", username=session.get("username", "")))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


def _target_account_id():
    """account_id del vendedor sobre el que opera el admin. Viene por query string
    (?vendedor=<id>) o en el body JSON (account_id / vendedor). RepoError si falta."""
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
@app.route("/api/admin/clients", methods=["GET"])
@require_admin
@_repo_error_response
def admin_clients_list():
    return jsonify({"clients": _repo.list_clients(_target_account_id())})


@app.route("/api/admin/clients", methods=["POST"])
@require_admin
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
        ranges=d.get("ranges"),
    )
    return jsonify({"client": c}), 201


@app.route("/api/admin/clients/<int:client_id>", methods=["PATCH"])
@require_admin
@_repo_error_response
def admin_clients_update(client_id):
    d = request.get_json(silent=True) or {}
    c = _repo.update_client(
        _target_account_id(), client_id,
        display_name=d.get("display_name"),
        prompt=d.get("prompt"),
        status=d.get("status"),
        ig_username=d.get("ig_username"),
        gender=d.get("gender"),
        gender_set=("gender" in d),
        ranges=d.get("ranges"),
        ranges_set=("ranges" in d),
    )
    return jsonify({"client": c})


@app.route("/api/admin/clients/<int:client_id>", methods=["DELETE"])
@require_admin
@_repo_error_response
def admin_clients_delete(client_id):
    ok = _repo.delete_client(_target_account_id(), client_id)
    return jsonify({"deleted": ok})


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
