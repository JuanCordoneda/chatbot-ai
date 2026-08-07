from flask import Flask, request
import requests
import json
import os
import time

app = Flask(__name__)

# Utiliza variables de entorno para los tokens y las URLs
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL')
OPENAI_SERVICE_URL = os.environ.get('OPENAI_SERVICE_URL')

# Pausa entre los mensajes de una tanda (segundos). Configurable porque el punto
# justo depende de cuántos vendedores repartan a la vez sobre el mismo número.
SEND_BULK_DELAY = float(os.environ.get('SEND_BULK_DELAY', '1'))

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
    
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        message = value["messages"][0]
        text = message["text"]
        question = text["body"]
        number = message["from"]
        
        print(f"El texto recibido del usuario es: {question}")
        
        body_answer = enviar_mensaje(question, number)
        send_message = whatsapp_service(body_answer)
        
        if send_message:
            print("Mensaje enviado correctamente.")
        else:
            print("Error en el envío del mensaje.")
            
        return "EVENT_RECEIVED"
    
    except Exception as e:
        print(e)
        return "EVENT_RECEIVED", 200

def whatsapp_service(body):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}"
        }
        
        response = requests.post(WHATSAPP_API_URL, data=json.dumps(body), headers=headers)
        
        print(f"Estado de la respuesta: {response.text}")
        return response.status_code == 200
        
    except Exception as e:
        print(e)
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
    url = f"{OPENAI_SERVICE_URL}/getresponsegpt?user_prompt={text}&phone_number={numero}"
    response_gpt = requests.get(url).content.decode("utf-8")
    
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "type": "text",
        "text": {"body": response_gpt}
    }
    
    return body

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    """Manda una tanda de mensajes SUELTOS al mismo número, uno por mensaje.

    Existe para repartir los comentarios: el vendedor los recibe separados y de
    ahí los reenvía al grupo del cliente con el reenvío múltiple de WhatsApp
    (mantener apretado, tildar todos, reenviar). Mandarlos en un solo mensaje no
    sirve: el grupo no podría reenviarlos de a uno.

    NO se puede mandar a un grupo: la Cloud API de Meta solo entrega a números.

    Ojo con la ventana de 24h de Meta: si el destinatario no le escribió al bot
    en las últimas 24 horas, rechaza el texto libre y acá se ve como fallidos.
    """
    data = request.get_json(silent=True) or {}
    numero = normalizar_numero((data.get("to") or "").strip().lstrip("+"))
    mensajes = [m for m in (data.get("mensajes") or []) if (m or "").strip()]

    if not numero:
        return {"error": "Falta el número de destino."}, 400
    if not mensajes:
        return {"error": "No hay mensajes para mandar."}, 400
    # Tope de cordura: una tanda normal son ~20. Un número mucho más grande es
    # un bug del llamador, y mandarlo igual arriesga la calidad del número.
    if len(mensajes) > 40:
        return {"error": f"Demasiados mensajes ({len(mensajes)}). El máximo es 40."}, 400

    enviados, fallidos = 0, []
    for i, texto in enumerate(mensajes):
        ok, detalle = _enviar_texto(numero, texto)
        if ok:
            enviados += 1
        else:
            fallidos.append({"indice": i, "detalle": detalle})
        # Un respiro entre mensajes. Meta tolera la ráfaga, pero 20 mensajes
        # idénticos en un segundo es justo el patrón que le baja la calidad al
        # número, y perder el número es perder el bot y las alertas del CRM.
        if i < len(mensajes) - 1:
            time.sleep(SEND_BULK_DELAY)

    # 207: se mandó parte. El front necesita distinguirlo de un fallo total para
    # decirle al vendedor cuáles reintentar en vez de repetir toda la tanda.
    status = 200 if not fallidos else (207 if enviados else 502)
    return {"enviados": enviados, "total": len(mensajes), "fallidos": fallidos}, status


def _enviar_texto(numero, texto):
    """Manda un texto suelto. Devuelve (ok, detalle) — el detalle es lo que se
    le muestra al vendedor cuando falla, así que vale que sea el error de Meta."""
    try:
        resp = requests.post(
            WHATSAPP_API_URL,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": numero,
                "type": "text",
                "text": {"body": texto},
            },
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code == 200:
            return True, ""
        print(f"[send-bulk] Meta rechazó el mensaje: {resp.status_code} {resp.text}", flush=True)
        return False, _error_meta(resp)
    except Exception as e:
        print(f"[send-bulk] error mandando: {e!r}", flush=True)
        return False, "No se pudo conectar con WhatsApp."


def _error_meta(resp):
    """Traduce el error de Meta a algo accionable.

    El caso que de verdad importa es la ventana de 24h: sin traducir llega como
    un código y el vendedor no tiene forma de saber que se arregla escribiéndole
    al bot."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:
        return f"WhatsApp respondió {resp.status_code}."
    codigo = err.get("code")
    if codigo == 131047:
        return ("Pasaron más de 24 horas desde tu último mensaje al bot. "
                "Escribile cualquier cosa por WhatsApp y volvé a mandar.")
    if codigo == 131026:
        return "Ese número no tiene WhatsApp o no puede recibir mensajes."
    if codigo in (190, 4):
        return "El token de WhatsApp venció o se pasó del límite de envíos."
    return err.get("message") or f"WhatsApp respondió {resp.status_code}."


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
    app.run(host="0.0.0.0", port=8501, debug=False)  # Cambia debug a False para producción
