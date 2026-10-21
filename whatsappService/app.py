from flask import Flask, request
import requests
import json
import os
import sys
import logging
import traceback
from functools import wraps
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whatsapp")

app = Flask(__name__)

# Utiliza variables de entorno para los tokens y las URLs
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL')
OPENAI_SERVICE_URL = os.environ.get('OPENAI_SERVICE_URL')
# Clave compartida con el openai-service. Sin ella, cualquiera que conozca la URL
# podría mandar mensajes de WhatsApp con nuestro número.
SERVICE_TOKEN = os.environ.get('SERVICE_TOKEN', '')


def solo_servicio(f):
    """Deja pasar solo las llamadas del openai-service (header X-Service-Token)."""
    @wraps(f)
    def envoltura(*args, **kwargs):
        if SERVICE_TOKEN and request.headers.get("X-Service-Token") != SERVICE_TOKEN:
            return {"error": "No autorizado."}, 401
        return f(*args, **kwargs)
    return envoltura

# Meta devuelve code=190 (OAuthException) cuando el access token ya no sirve.
# El subcode dice por que, para no tener que interpretar el JSON a mano.
TOKEN_ERROR_SUBCODES = {
    463: "el token ha caducado",
    467: "el token ya no es valido",
    460: "cambio la contrasena del usuario y el token se invalido",
    458: "el token fue revocado o la app fue desinstalada",
}

# Lo que ve el cliente si el servicio de IA falla. Preferimos un mensaje cuidado
# antes que reenviar lo que devuelva el servicio (ante un 500 seria HTML crudo).
FALLBACK_MESSAGE = (
    "Désolés, nous avons un petit souci technique. "
    "Réécrivez-nous dans quelques minutes, s'il vous plaît."
)

# Limite de caracteres de un mensaje de texto de WhatsApp.
MAX_WHATSAPP_TEXT = 4096

@app.route("/saludar", methods=["GET"])
def saludar():
    return "Hola"

@app.route("/whatsapp", methods=["GET"])
def verify_token():
    try:
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if token == VERIFY_TOKEN:
            return challenge
        else:
            return "Error en la verificación del token.", 400
    except Exception as e:
        return str(e), 400

@app.route("/whatsapp", methods=["POST"])
def received_message():
    try:
        body = request.get_json()
        log.info("Webhook recibido: %s", json.dumps(body))

        value = body["entry"][0]["changes"][0]["value"]

        # Meta manda tambien webhooks de estado (sent/delivered/read) que NO
        # traen "messages". Los ignoramos en vez de romper.
        messages = value.get("messages")
        if not messages:
            log.info("Webhook sin mensajes (probablemente un status update); se ignora.")
            return "EVENT_RECEIVED", 200

        message = messages[0]
        number = message["from"]

        tipo = message.get("type")
        if tipo == "text":
            question = message["text"]["body"]
        elif tipo == "button":
            # Toque en un botón de respuesta rápida de una plantilla ("Oui, avec plaisir").
            question = message["button"].get("text") or message["button"].get("payload", "")
        elif tipo == "interactive":
            interactivo = message["interactive"]
            respuesta = interactivo.get("button_reply") or interactivo.get("list_reply") or {}
            question = respuesta.get("title", "")
        elif tipo in ("audio", "image", "video", "document", "sticker", "location", "contacts"):
            # El bot no puede ver ni escuchar esto: le avisamos para que pida que lo escriban.
            question = f"[Le client a envoyé un contenu de type {tipo} que tu ne peux ni voir ni écouter]"
        else:
            log.info("Mensaje de tipo '%s' no soportado; se ignora.", tipo)
            return "EVENT_RECEIVED", 200
        log.info("Mensaje recibido de %s (%s): %s", number, tipo, question)

        body_answer = enviar_mensaje(question, number)
        if body_answer is None:
            log.info("El bot no responde a %s (pausado o sin respuesta).", number)
            return "EVENT_RECEIVED", 200
        send_message = whatsapp_service(body_answer)

        if send_message:
            log.info("Mensaje enviado correctamente a %s.", number)
        else:
            log.error("Error en el envio del mensaje a %s.", number)

        return "EVENT_RECEIVED", 200

    except Exception:
        log.error("Excepcion procesando webhook:\n%s", traceback.format_exc())
        return "EVENT_RECEIVED", 200

def log_meta_error(response):
    """Loguea el error de Meta y avisa claro cuando el problema es el token."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        log.error("Respuesta de Meta no es JSON [%s]: %s", response.status_code, response.text)
        return

    code = error.get("code")
    subcode = error.get("error_subcode")
    message = error.get("message", "")

    if code == 190:
        motivo = TOKEN_ERROR_SUBCODES.get(subcode, "el token no es valido")
        log.error(
            "TOKEN DE WHATSAPP INVALIDO: %s (code=%s, subcode=%s). "
            "Genera un token permanente de Usuario de Sistema en business.facebook.com "
            "y actualiza WHATSAPP_ACCESS_TOKEN en el .env. Detalle de Meta: %s",
            motivo, code, subcode, message,
        )
    else:
        log.error(
            "Error de Meta [%s] code=%s subcode=%s: %s",
            response.status_code, code, subcode, message,
        )


def whatsapp_service(body):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}"
        }

        log.info("Enviando a Meta (%s): %s", WHATSAPP_API_URL, json.dumps(body))
        response = requests.post(WHATSAPP_API_URL, data=json.dumps(body), headers=headers, timeout=30)

        log.info("Respuesta de Meta [%s]: %s", response.status_code, response.text)
        if response.status_code != 200:
            log_meta_error(response)
            return False
        return True

    except Exception:
        log.error("Excepcion enviando a Meta:\n%s", traceback.format_exc())
        return False
    
def normalizar_numero(numero):
    # Meta manda 5492233407778 pero acepta 54223153407778
    # 5492233407778 → 54 + 2233 + 15 + 407778 = 542233 15 407778
    # Pero el registrado fue 54223153407778 = 54 + 2231 + 53407778 (codigo area 2231)
    # Hardcodeamos el mapeo exacto para este numero
    mapeo = {
        "5492233407778": "54223153407778"
    }
    return mapeo.get(numero, numero)

def consultar_ia(text, numero):
    """Pide la respuesta al openai-service.

    Nunca devuelve el cuerpo crudo si algo falla: ante un error Flask responde
    una pagina HTML y esa pagina acabaria enviandose al cliente como mensaje.
    En cualquier fallo devolvemos FALLBACK_MESSAGE.
    """
    url = f"{OPENAI_SERVICE_URL}/getresponsegpt"
    params = {"user_prompt": text, "phone_number": numero}
    log.info("Consultando openai-service: %s params=%s", url, params)

    try:
        resp = requests.get(url, params=params, headers={"X-Service-Token": SERVICE_TOKEN}, timeout=60)
    except Exception:
        log.error("Excepcion consultando openai-service:\n%s", traceback.format_exc())
        return FALLBACK_MESSAGE

    log.info("Respuesta openai-service [%s]: %s", resp.status_code, resp.text[:500])

    # 204: el bot decidió no responder (por ejemplo, alguien del equipo tomó la conversación).
    if resp.status_code == 204:
        return None

    if resp.status_code != 200:
        log.error(
            "openai-service devolvio %s; se envia el mensaje de fallback en vez del cuerpo.",
            resp.status_code,
        )
        return FALLBACK_MESSAGE

    respuesta = resp.content.decode("utf-8", errors="replace").strip()
    if not respuesta:
        log.error("openai-service devolvio una respuesta vacia; se envia el fallback.")
        return FALLBACK_MESSAGE

    # WhatsApp rechaza el mensaje si supera el limite de caracteres.
    return respuesta[:MAX_WHATSAPP_TEXT]


def enviar_mensaje(text, numero):
    numero = normalizar_numero(numero)
    response_gpt = consultar_ia(text, numero)
    if response_gpt is None:
        return None

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "type": "text",
        "text": {"body": response_gpt}
    }
    
    return body

@app.route("/send-template", methods=["POST"])
@solo_servicio
def send_template():
    try:
        data = request.get_json()
        number = data.get("to")
        template_name = data.get("template", "hello_world")
        language_code = data.get("language", "en_US")
        params = data.get("params") or []
        header_image = data.get("header_image")

        if not number:
            return {"error": "El campo 'to' es requerido."}, 400

        template = {
            "name": template_name,
            "language": {"code": language_code}
        }
        components = []
        if header_image:
            # Cabecera de tipo imagen: Meta la baja de esta URL pública.
            components.append({
                "type": "header",
                "parameters": [{"type": "image", "image": {"link": header_image}}],
            })
        if params:
            # Variables {{1}}, {{2}}... del cuerpo de la plantilla, en orden.
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            })
        if components:
            template["components"] = components

        body = {
            "messaging_product": "whatsapp",
            "to": normalizar_numero(number),
            "type": "template",
            "template": template,
        }

        success = whatsapp_service(body)
        if success:
            return {"status": "ok", "message": f"Template '{template_name}' enviado a {number}."}, 200
        else:
            return {"status": "error", "message": "Fallo al enviar el template."}, 500

    except Exception as e:
        print(e)
        return {"error": str(e)}, 500

@app.route("/send-text", methods=["POST"])
@solo_servicio
def send_text():
    """Manda un texto libre (lo usa el panel cuando alguien del equipo escribe en persona).

    WhatsApp solo deja mandar texto libre dentro de las 24 h desde el último
    mensaje del cliente; fuera de esa ventana hay que usar una plantilla.
    """
    data = request.get_json(silent=True) or {}
    number = data.get("to")
    text = (data.get("text") or "").strip()
    if not number or not text:
        return {"error": "Los campos 'to' y 'text' son requeridos."}, 400

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalizar_numero(number),
        "type": "text",
        "text": {"body": text[:MAX_WHATSAPP_TEXT]},
    }
    if whatsapp_service(body):
        return {"status": "ok"}, 200
    return {"status": "error", "message": "Meta rechazó el mensaje. Si el cliente no escribió en las últimas 24 h, hay que usar una plantilla."}, 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    app.run(host="0.0.0.0", port=port, debug=False)  # Cambia debug a False para producción
