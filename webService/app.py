from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, make_response
import requests
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "growi-secret-2026")

OPENAI_SERVICE_URL = os.environ.get("OPENAI_SERVICE_URL", "http://openai-service:8000")

USERS = {
    "growi":       {"password": "growi2026",  "admin": False},
    "growi-admin": {"password": "growi2026",  "admin": True},
}

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
_growi_session = None


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


def _growi_login():
    s = requests.Session()
    s.headers.update({"user-agent": _GROWI_USER_AGENT})
    if _GROWI_PROXIES:
        s.proxies.update(_GROWI_PROXIES)
    s.post(
        f"{GROWI_CRM_URL}/cuenta/login.php",
        data={"correo": GROWI_CRM_EMAIL, "password": GROWI_CRM_PASSWORD},
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "referer": f"{GROWI_CRM_URL}/cuenta/login.php",
            "origin": GROWI_CRM_URL,
        },
        timeout=15,
    )
    return s


def _get_growi_session():
    global _growi_session
    if _growi_session is None:
        _growi_session = _growi_login()
    return _growi_session


def _growi_request(method, path, **kwargs):
    """GET/POST autenticado contra el CRM, reintentando con login fresco ante 401."""
    global _growi_session
    session_ = _get_growi_session()
    for intento in range(1, 5):
        resp = session_.request(method, f"{GROWI_CRM_URL}{path}", timeout=kwargs.pop("timeout", 15), **kwargs)
        if resp.status_code != 401:
            return resp
        print(f"[growi-web] 401 en intento {intento}/4 para {path}, reintentando con login fresco", flush=True)
        _growi_session = None
        session_ = _get_growi_session()
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = USERS.get(username)
        if user and user["password"] == password:
            session["logged_in"] = True
            session["is_admin"] = user["admin"]
            return redirect(url_for("index"))
        error = "Usuario o contraseña incorrectos"
    resp = make_response(render_template("login.html", error=error))
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
def procesar():
    data = request.get_json()
    post_url = data.get("url", "").strip()
    if not post_url:
        return jsonify({"error": "Falta el link de Instagram"}), 400

    # "Cargar más" manda los comentarios ya generados para que no se repitan.
    evitar = data.get("evitar", []) or []

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
def publicar():
    data = request.get_json()
    post_url    = data.get("url", "").strip()
    comentarios = data.get("comentarios", [])
    ordenes     = data.get("ordenes", [])
    disponible  = data.get("disponible", DISPONIBLE)

    if not post_url or not comentarios:
        return jsonify({"error": "Faltan datos"}), 400

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
def nombre_red():
    red_id = request.args.get("red", "1")
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_nombre.php",
            params={"red": red_id},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/costo_trafico", methods=["POST"])
def costo_trafico():
    data = request.get_json()
    try:
        resp = _growi_request(
            "POST", "/paginas/obtenercostotrafico.php",
            json=data,
            headers={
                "referer": f"{GROWI_CRM_URL}/paginas/trafico.php",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/demora", methods=["GET"])
def demora():
    redsocial = request.args.get("redsocial", "")
    producto  = request.args.get("producto", "")
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_demora.php",
            params={"redsocial": redsocial, "producto": producto},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/productos", methods=["GET"])
def productos():
    rrss_id = request.args.get("rrss", "1")
    try:
        resp = _growi_request(
            "GET", "/paginas/obtener_productos_con_precios.php",
            params={"rrss": rrss_id},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/server_time_ar", methods=["GET"])
def server_time_ar():
    import random
    try:
        resp = _growi_request(
            "GET", "/paginas/server_time_ar.php",
            params={"_": random.random()},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/enviar_trafico", methods=["POST"])
def enviar_trafico():
    data = request.get_json()
    ordenes = data.get("ordenes", [])
    if not ordenes:
        return jsonify({"error": "Sin órdenes"}), 400

    # Obtener fecha/hora del servidor en AR
    try:
        import random
        ts_resp = _growi_request(
            "GET", "/paginas/server_time_ar.php",
            params={"_": random.random()},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
        )
        ts_data = ts_resp.json()
        fecha_ar = ts_data.get("ymdhmAR", "")[:10]  # "2026-06-25"
    except Exception:
        from datetime import date as _date
        fecha_ar = _date.today().isoformat()

    costo_total = sum(float(o.get("costo", 0)) for o in ordenes)
    disponible  = float(data.get("disponible", DISPONIBLE))

    for o in ordenes:
        o["disponible"] = disponible

    payload = {
        "idvendedor": GROWI_IDVENDEDOR,
        "idventa":    GROWI_IDVENTA,
        "fecha":      fecha_ar,
        "vendedor":   " ",
        "cant_enviada": 0,
        "aprobada":   "Aprobado",
        "ordenes":    ordenes,
        "creador":    GROWI_IDVENDEDOR,
        "disponible": disponible,
        "resto":      round(disponible - costo_total, 6),
        "costo_orden": round(costo_total, 6),
    }

    req_headers = {
        "referer":      f"{GROWI_CRM_URL}/paginas/trafico.php",
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
