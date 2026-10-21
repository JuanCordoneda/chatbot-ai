"""Servicio del prospector de Toloache (campaña Midi Toloache).

- /getresponsegpt: lo llama el whatsapp-service con cada mensaje entrante y
  devuelve la respuesta del bot (204 si no hay que responder).
- /panel: el panel web para cargar la lista y seguir las conversaciones.
- /api/...: lo que usa el panel. Protegido con PANEL_PASSWORD.
- /midi: la página pública de la oferta y las imágenes de la plantilla.
"""

import functools
import logging
import os
import sys
import threading
import time
import traceback

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import db
import prospector

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("servicio")

app = Flask(__name__)
db.init()

WHATSAPP_SERVICE_URL = os.environ.get("WHATSAPP_SERVICE_URL", "http://whatsapp-service:8501").rstrip("/")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
# Clave compartida con el whatsapp-service: protege /getresponsegpt y la usan los envíos.
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")
PAUSA_ENTRE_ENVIOS = float(os.environ.get("PAUSA_ENTRE_ENVIOS", "2"))
# Meta baja la calidad del número (y puede bloquearlo) si se escribe en frío a muchos por día.
MAX_ENVIOS_DIA = int(os.environ.get("MAX_ENVIOS_DIA", "25"))
# URL pública de este servicio (https://...). La necesitan Meta, para bajar la imagen
# de la plantilla, y el bot, para pasar el link a /midi. Si no está, se toma del
# dominio con el que se abre el panel.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
AQUI = os.path.dirname(os.path.abspath(__file__))
MIDI = os.path.join(AQUI, "midi")
_base = {"url": PUBLIC_URL}


def base_publica() -> str:
    return PUBLIC_URL or _base["url"]


def _recordar_base():
    """Guarda el dominio público con el que se abrió el panel (Railway pasa el esquema en X-Forwarded-Proto)."""
    if PUBLIC_URL:
        return
    host = request.headers.get("X-Forwarded-Host") or request.host
    if host.split(":")[0] in ("localhost", "127.0.0.1", "0.0.0.0") or "." not in host:
        return
    proto = (request.headers.get("X-Forwarded-Proto") or "https").split(",")[0].strip()
    _base["url"] = f"{proto}://{host}"


# ---------------------------------------------------------------------------
# Bot (WhatsApp entrante)
# ---------------------------------------------------------------------------

@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    if SERVICE_TOKEN and request.headers.get("X-Service-Token") != SERVICE_TOKEN:
        return "", 401
    texto = request.args.get("user_prompt", "").strip()
    telefono = request.args.get("phone_number", "").strip()
    if not texto or not telefono:
        return "", 204
    base = base_publica()
    respuesta = prospector.responder(telefono, texto, base + "/midi" if base else "")
    if not respuesta:
        return "", 204
    return respuesta


# ---------------------------------------------------------------------------
# Página pública de la oferta
# ---------------------------------------------------------------------------

@app.route("/midi")
@app.route("/midi/")
def midi():
    with open(os.path.join(MIDI, "index.html"), encoding="utf-8") as f:
        html = f.read()
    # WhatsApp exige URLs absolutas en og:image: se completan con el dominio de la petición.
    proto = (request.headers.get("X-Forwarded-Proto") or request.scheme).split(",")[0].strip()
    origen = f"{proto}://{request.headers.get('X-Forwarded-Host') or request.host}"
    return Response(html.replace("__ORIGIN__", origen), mimetype="text/html",
                    headers={"Cache-Control": "public, max-age=300", "X-Robots-Tag": "noindex, nofollow"})


@app.route("/midi/<path:archivo>")
def midi_archivo(archivo):
    resp = send_from_directory(MIDI, archivo, max_age=86400)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def requiere_clave(f):
    """Pide la contraseña (HTTP Basic) si PANEL_PASSWORD está configurada. El usuario da igual."""
    @functools.wraps(f)
    def envoltura(*args, **kwargs):
        if PANEL_PASSWORD:
            auth = request.authorization
            if not auth or auth.password != PANEL_PASSWORD:
                return Response("Necesitás la contraseña.", 401, {"WWW-Authenticate": 'Basic realm="Prospector"'})
        _recordar_base()
        return f(*args, **kwargs)
    return envoltura


@app.route("/")
@app.route("/panel")
@requiere_clave
def panel():
    return send_from_directory(AQUI, "panel.html")


def _resumen(prospectos):
    r = {e: 0 for e in db.ESTADOS}
    for p in prospectos:
        r[p["estado"]] = r.get(p["estado"], 0) + 1
    r["atencion"] = sum(1 for p in prospectos if p["atencion"])
    return r


def _publico(p):
    p = dict(p)
    p["paso"] = prospector.paso_actual(p["ficha"])
    p["saludo"] = prospector.saludo(p)
    return p


def _imagen_plantilla() -> str:
    """URL pública de la imagen de cabecera de la plantilla, o "" si la plantilla no lleva imagen."""
    if not prospector.PLANTILLA_IMAGEN:
        return ""
    base = base_publica()
    return f"{base}/midi/{prospector.PLANTILLA_IMAGEN}" if base else ""


@app.route("/api/prospectos", methods=["GET"])
@requiere_clave
def api_listar():
    ps = db.listar()
    return jsonify({
        "prospectos": [_publico(p) for p in ps],
        "resumen": _resumen(ps),
        "ajustes": db.ajustes(),
        "pasos": [{"clave": k, "nombre": n} for k, n in prospector.PASOS],
        "plantilla": {"nombre": prospector.PLANTILLA_NOMBRE, "texto": prospector.PLANTILLA_TEXTO,
                      "imagen": prospector.PLANTILLA_IMAGEN and f"/midi/{prospector.PLANTILLA_IMAGEN}"},
        "enviando": _campania["activa"],
        "cupo_hoy": max(0, MAX_ENVIOS_DIA - db.enviados_hoy()),
        "max_dia": MAX_ENVIOS_DIA,
    })


@app.route("/api/prospectos", methods=["POST"])
@requiere_clave
def api_cargar():
    datos = request.get_json(silent=True) or {}
    return jsonify(db.cargar_lista(datos.get("texto", "")))


@app.route("/api/prospectos/<clave>", methods=["GET"])
@requiere_clave
def api_detalle(clave):
    p = db.obtener(clave)
    if not p:
        return jsonify({"error": "No existe ese prospecto."}), 404
    return jsonify({"prospecto": _publico(p), "mensajes": db.mensajes(clave)})


@app.route("/api/prospectos/<clave>", methods=["PATCH"])
@requiere_clave
def api_editar(clave):
    if not db.obtener(clave):
        return jsonify({"error": "No existe ese prospecto."}), 404
    datos = request.get_json(silent=True) or {}
    try:
        db.actualizar(clave, **{k: v for k, v in datos.items() if k in ("estado", "bot_activo", "atencion", "nombre", "empresa")})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"prospecto": _publico(db.obtener(clave))})


@app.route("/api/prospectos/<clave>", methods=["DELETE"])
@requiere_clave
def api_borrar(clave):
    db.borrar(clave)
    return jsonify({"ok": True})


@app.route("/api/prospectos/<clave>/mensaje", methods=["POST"])
@requiere_clave
def api_mensaje_humano(clave):
    """Alguien del equipo escribe en persona. El bot queda pausado para no pisarlo."""
    p = db.obtener(clave)
    if not p:
        return jsonify({"error": "No existe ese prospecto."}), 404
    texto = ((request.get_json(silent=True) or {}).get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Escribí un mensaje."}), 400
    ok, error = _whatsapp("/send-text", {"to": p["telefono"], "text": texto})
    if not ok:
        return jsonify({"error": error}), 502
    db.agregar_mensaje(clave, "humano", texto)
    db.actualizar(clave, bot_activo=False, atencion="")
    return jsonify({"prospecto": _publico(db.obtener(clave)), "mensajes": db.mensajes(clave)})


@app.route("/api/ajustes", methods=["PUT"])
@requiere_clave
def api_ajustes():
    try:
        return jsonify(db.guardar_ajustes(request.get_json(silent=True) or {}))
    except (TypeError, ValueError):
        return jsonify({"error": "No se pudieron guardar los ajustes."}), 400


# ---------------------------------------------------------------------------
# Campaña: primer mensaje a los prospectos nuevos
# ---------------------------------------------------------------------------

_campania = {"activa": False}
_campania_lock = threading.Lock()


def _whatsapp(ruta, cuerpo):
    """Llama al whatsapp-service. Devuelve (ok, mensaje de error legible)."""
    try:
        r = requests.post(WHATSAPP_SERVICE_URL + ruta, json=cuerpo, headers={"X-Service-Token": SERVICE_TOKEN}, timeout=30)
    except requests.RequestException as e:
        log.error("No se pudo conectar con el whatsapp-service: %s", e)
        return False, "No se pudo conectar con el servicio de WhatsApp."
    if r.status_code == 200:
        return True, ""
    try:
        detalle = r.json().get("message") or r.json().get("error") or r.text
    except ValueError:
        detalle = r.text
    log.error("whatsapp-service %s devolvió %s: %s", ruta, r.status_code, detalle)
    return False, f"WhatsApp rechazó el mensaje: {detalle}"[:300]


def _enviar_campania(claves, imagen):
    try:
        for clave in claves:
            p = db.obtener(clave)
            if not p or p["estado"] not in ("nuevo", "error"):
                continue
            ok, error = _whatsapp("/send-template", {
                "to": p["telefono"],
                "template": prospector.PLANTILLA_NOMBRE,
                "language": prospector.PLANTILLA_IDIOMA,
                # Solo se manda el saludo si la plantilla tiene la variable {{1}}.
                "params": [prospector.saludo(p)] if "{{1}}" in prospector.PLANTILLA_TEXTO else [],
                "header_image": imagen,
            })
            if ok:
                db.agregar_mensaje(clave, "bot", prospector.texto_plantilla(p))
                db.actualizar(clave, estado="contactado", ultimo_error="")
            else:
                db.actualizar(clave, estado="error", ultimo_error=error)
            time.sleep(PAUSA_ENTRE_ENVIOS)
    except Exception:
        log.error("Falló la campaña:\n%s", traceback.format_exc())
    finally:
        _campania["activa"] = False


@app.route("/api/enviar", methods=["POST"])
@requiere_clave
def api_enviar():
    datos = request.get_json(silent=True) or {}
    pedidas = datos.get("claves")
    with _campania_lock:
        if _campania["activa"]:
            return jsonify({"error": "Ya se están mandando mensajes. Esperá a que termine."}), 409
        imagen = _imagen_plantilla()
        if prospector.PLANTILLA_IMAGEN and not imagen:
            return jsonify({"error": "Falta PUBLIC_URL: sin la dirección pública del servicio, Meta no puede bajar la imagen de la plantilla."}), 400
        # La lista se recorre de la más vieja a la más nueva: primero los que se cargaron antes.
        candidatos = [p["clave"] for p in reversed(db.listar()) if p["estado"] in ("nuevo", "error")]
        if pedidas:
            candidatos = [c for c in candidatos if c in set(pedidas)]
        if not candidatos:
            return jsonify({"error": "No hay prospectos nuevos para escribirles."}), 400
        cupo = MAX_ENVIOS_DIA - db.enviados_hoy()
        if cupo <= 0:
            return jsonify({"error": f"Hoy ya se mandaron {MAX_ENVIOS_DIA} primeros mensajes. Seguí mañana para cuidar el número."}), 409
        quedan = max(0, len(candidatos) - cupo)
        candidatos = candidatos[:cupo]
        _campania["activa"] = True
    threading.Thread(target=_enviar_campania, args=(candidatos, imagen), daemon=True).start()
    return jsonify({"encolados": len(candidatos), "quedan": quedan})


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """Ante un fallo inesperado devolvemos texto plano, nunca el HTML de Flask.

    El whatsapp-service convierte el 500 en su mensaje de fallback, así el
    cliente nunca recibe una página de error.
    """
    if isinstance(error, HTTPException):
        return error
    traceback.print_exc()
    if request.path.startswith("/api/"):
        return jsonify({"error": "Algo falló en el servidor. Probá de nuevo."}), 500
    return "Lo siento, hubo un problema procesando tu mensaje.", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
