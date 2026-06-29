from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, make_response
import requests
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "growi-secret-2026")

OPENAI_SERVICE_URL = os.environ.get("OPENAI_SERVICE_URL", "http://openai-service:8000")

USERS = {
    "growi":       {"password": "growi2026",  "admin": False},
    "growi-admin": {"password": "growi2026",  "admin": True},
}

GROWI_CRM_URL    = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
GROWI_PHPSESSID  = os.environ.get("GROWI_CRM_PHPSESSID", "")
GROWI_REMEMBERME = os.environ.get("GROWI_CRM_REMEMBERME", "")
GROWI_IDVENDEDOR = os.environ.get("GROWI_IDVENDEDOR", "")
GROWI_IDVENTA    = os.environ.get("GROWI_IDVENTA", "1")
DISPONIBLE       = float(os.environ.get("GROWI_DISPONIBLE", "150"))


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

    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/procesar_post",
            json={"url": post_url},
            timeout=30,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stream/<job_id>", methods=["GET"])
def stream(job_id):
    offset = request.args.get("offset", "0")
    progreso_offset = request.args.get("progreso_offset", "0")

    def generate():
        try:
            with requests.get(
                f"{OPENAI_SERVICE_URL}/procesar_post/stream/{job_id}",
                params={"offset": offset, "progreso_offset": progreso_offset},
                stream=True,
                timeout=300,
            ) as resp:
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
        except Exception as e:
            import json
            yield f"data: {json.dumps({'tipo': 'error', 'mensaje': str(e)})}\n\n".encode()

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
        return jsonify({"error": str(e)}), 500


@app.route("/api/nombre_red", methods=["GET"])
def nombre_red():
    red_id = request.args.get("red", "1")
    try:
        resp = requests.get(
            f"{GROWI_CRM_URL}/paginas/obtener_nombre.php",
            params={"red": red_id},
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/costo_trafico", methods=["POST"])
def costo_trafico():
    data = request.get_json()
    try:
        resp = requests.post(
            f"{GROWI_CRM_URL}/paginas/obtenercostotrafico.php",
            json=data,
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={
                "referer": f"{GROWI_CRM_URL}/paginas/trafico.php",
                "content-type": "application/json",
            },
            timeout=10,
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
        resp = requests.get(
            f"{GROWI_CRM_URL}/paginas/obtener_demora.php",
            params={"redsocial": redsocial, "producto": producto},
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "text/plain")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/productos", methods=["GET"])
def productos():
    rrss_id = request.args.get("rrss", "1")
    try:
        resp = requests.get(
            f"{GROWI_CRM_URL}/paginas/obtener_productos_con_precios.php",
            params={"rrss": rrss_id},
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
            timeout=10,
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/server_time_ar", methods=["GET"])
def server_time_ar():
    import random
    try:
        resp = requests.get(
            f"{GROWI_CRM_URL}/paginas/server_time_ar.php",
            params={"_": random.random()},
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
            timeout=10,
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
        ts_resp = requests.get(
            f"{GROWI_CRM_URL}/paginas/server_time_ar.php",
            params={"_": random.random()},
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={"referer": f"{GROWI_CRM_URL}/paginas/trafico.php"},
            timeout=10,
        )
        ts_data = ts_resp.json()
        fecha_ar = ts_data.get("ymdhmAR", "")[:10]  # "2026-06-25"
    except Exception:
        from datetime import date as _date
        fecha_ar = _date.today().isoformat()

    costo_total = sum(float(o.get("costo", 0)) for o in ordenes)
    disponible  = float(data.get("disponible", 0))

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

    try:
        resp = requests.post(
            f"{GROWI_CRM_URL}/paginas/enviar_trafico.php",
            json=payload,
            cookies={"PHPSESSID": GROWI_PHPSESSID, "rememberme": GROWI_REMEMBERME},
            headers={
                "referer":      f"{GROWI_CRM_URL}/paginas/trafico.php",
                "content-type": "application/json; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text, resp.status_code, {"Content-Type": resp.headers.get("Content-Type", "application/json")}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
