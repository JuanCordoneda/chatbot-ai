from flask import Flask, request
import requests
import json
import os
import sys
import logging
import traceback
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whatsapp")

app = Flask(__name__)

# Utiliza variables de entorno para los tokens y las URLs
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL')
OPENAI_SERVICE_URL = os.environ.get('OPENAI_SERVICE_URL')

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

        # Solo procesamos mensajes de texto
        if message.get("type") != "text":
            log.info("Mensaje de tipo '%s' no soportado; se ignora.", message.get("type"))
            return "EVENT_RECEIVED", 200

        question = message["text"]["body"]
        log.info("Texto recibido de %s: %s", number, question)

        body_answer = enviar_mensaje(question, number)
        send_message = whatsapp_service(body_answer)

        if send_message:
            log.info("Mensaje enviado correctamente a %s.", number)
        else:
            log.error("Error en el envio del mensaje a %s.", number)

        return "EVENT_RECEIVED", 200

    except Exception:
        log.error("Excepcion procesando webhook:\n%s", traceback.format_exc())
        return "EVENT_RECEIVED", 200

def whatsapp_service(body):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}"
        }

        log.info("Enviando a Meta (%s): %s", WHATSAPP_API_URL, json.dumps(body))
        response = requests.post(WHATSAPP_API_URL, data=json.dumps(body), headers=headers, timeout=30)

        log.info("Respuesta de Meta [%s]: %s", response.status_code, response.text)
        return response.status_code == 200

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

def enviar_mensaje(text, numero):
    numero = normalizar_numero(numero)
    url = f"{OPENAI_SERVICE_URL}/getresponsegpt?user_prompt={quote(text)}"
    log.info("Consultando openai-service: %s", url)
    resp = requests.get(url, timeout=60)
    log.info("Respuesta openai-service [%s]: %s", resp.status_code, resp.text[:500])
    response_gpt = resp.content.decode("utf-8")

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "type": "text",
        "text": {"body": response_gpt}
    }
    
    return body

@app.route("/send-template", methods=["POST"])
def send_template():
    try:
        data = request.get_json()
        number = data.get("to")
        template_name = data.get("template", "hello_world")
        language_code = data.get("language", "en_US")

        if not number:
            return {"error": "El campo 'to' es requerido."}, 400

        body = {
            "messaging_product": "whatsapp",
            "to": number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code}
            }
        }

        success = whatsapp_service(body)
        if success:
            return {"status": "ok", "message": f"Template '{template_name}' enviado a {number}."}, 200
        else:
            return {"status": "error", "message": "Fallo al enviar el template."}, 500

    except Exception as e:
        print(e)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    app.run(host="0.0.0.0", port=port, debug=False)  # Cambia debug a False para producción
