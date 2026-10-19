"""Servicio del prospector de Ignacio.

- /getresponsegpt: lo llama el whatsapp-service con cada mensaje entrante y
  devuelve la respuesta del bot (204 si no hay que responder).
- /panel: el panel web para cargar la lista y seguir las conversaciones.
- /api/...: lo que usa el panel. Protegido con PANEL_PASSWORD.
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
PANEL_USER = os.environ.get("PANEL_USER", "ignacio")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
PAUSA_ENTRE_ENVIOS = float(os.environ.get("PAUSA_ENTRE_ENVIOS", "2"))
AQUI = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Bot (WhatsApp entrante)
# ---------------------------------------------------------------------------

@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    texto = request.args.get("user_prompt", "").strip()
    telefono = request.args.get("phone_number", "").strip()
    if not texto or not telefono:
        return "", 204
    respuesta = prospector.responder(telefono, texto)
    if not respuesta:
        return "", 204
    return respuesta


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def requiere_clave(f):
    """Pide usuario y contraseña (HTTP Basic) si PANEL_PASSWORD está configurada."""
    @functools.wraps(f)
    def envoltura(*args, **kwargs):
        if PANEL_PASSWORD:
            auth = request.authorization
            if not auth or auth.username != PANEL_USER or auth.password != PANEL_PASSWORD:
                return Response("Necesitás usuario y contraseña.", 401, {"WWW-Authenticate": 'Basic realm="Prospector"'})
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
    return p


@app.route("/api/prospectos", methods=["GET"])
@requiere_clave
def api_listar():
    ps = db.listar()
    return jsonify({
        "prospectos": [_publico(p) for p in ps],
        "resumen": _resumen(ps),
        "ajustes": db.ajustes(),
        "pasos": [{"clave": k, "nombre": n} for k, n in prospector.PASOS],
        "plantilla": {"nombre": prospector.PLANTILLA_NOMBRE, "texto": prospector.PLANTILLA_TEXTO},
        "enviando": _campania["activa"],
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
    """Ignacio escribe en persona. El bot queda pausado para no pisarlo."""
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
        return jsonify({"error": "El mínimo de unidades tiene que ser un número."}), 400


# ---------------------------------------------------------------------------
# Campaña: primer mensaje a los prospectos nuevos
# ---------------------------------------------------------------------------

_campania = {"activa": False}
_campania_lock = threading.Lock()


def _whatsapp(ruta, cuerpo):
    """Llama al whatsapp-service. Devuelve (ok, mensaje de error legible)."""
    try:
        r = requests.post(WHATSAPP_SERVICE_URL + ruta, json=cuerpo, timeout=30)
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


def _enviar_campania(claves):
    try:
        for clave in claves:
            p = db.obtener(clave)
            if not p or p["estado"] not in ("nuevo", "error"):
                continue
            ok, error = _whatsapp("/send-template", {
                "to": p["telefono"],
                "template": prospector.PLANTILLA_NOMBRE,
                "language": prospector.PLANTILLA_IDIOMA,
                # Solo se manda el nombre si la plantilla tiene la variable {{1}}.
                "params": [prospector.primer_nombre(p) or "qué tal"] if "{{1}}" in prospector.PLANTILLA_TEXTO else [],
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
        candidatos = [p["clave"] for p in db.listar() if p["estado"] in ("nuevo", "error")]
        if pedidas:
            candidatos = [c for c in candidatos if c in set(pedidas)]
        if not candidatos:
            return jsonify({"error": "No hay prospectos nuevos para escribirles."}), 400
        _campania["activa"] = True
    threading.Thread(target=_enviar_campania, args=(candidatos,), daemon=True).start()
    return jsonify({"encolados": len(candidatos)})


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
