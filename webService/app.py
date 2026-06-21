from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import requests
import os

app = Flask(__name__)

OPENAI_SERVICE_URL = os.environ.get("OPENAI_SERVICE_URL", "http://openai-service:8000")


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=False)
