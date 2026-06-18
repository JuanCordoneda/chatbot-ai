from flask import Flask, request
import requests
import json
import os

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
