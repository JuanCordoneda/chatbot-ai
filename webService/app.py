from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import requests
import os

app = Flask(__name__)

OPENAI_SERVICE_URL = os.environ.get("OPENAI_SERVICE_URL", "http://openai-service:8000")

GROWI_CRM_URL    = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
GROWI_PHPSESSID  = os.environ.get("GROWI_CRM_PHPSESSID", "")
GROWI_REMEMBERME = os.environ.get("GROWI_CRM_REMEMBERME", "")


@app.route("/")
def index():
    return render_template("index.html")


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
    post_url = data.get("url", "").strip()
    comentarios = data.get("comentarios", [])

    if not post_url or not comentarios:
        return jsonify({"error": "Faltan datos"}), 400

    try:
        resp = requests.post(
            f"{OPENAI_SERVICE_URL}/publicar",
            json={"url": post_url, "comentarios": comentarios},
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
