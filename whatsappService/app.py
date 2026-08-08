from flask import Flask, request
import requests
import json
import os
import threading
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

# ── Estado de la ventana de 24h ──────────────────────────────────────────────
#
# Meta solo entrega texto libre si la persona le escribió al bot en las últimas
# 24 horas. Lo peligroso es que con la ventana cerrada NO devuelve error: acepta
# el envío con 200 y descarta los mensajes, avisando por este webhook. Sin
# escucharlo, el sistema no tiene forma de saber si algo llegó — que fue
# exactamente lo que pasó: 15 mensajes "enviados con éxito", cero entregados.
#
# Acá se guarda lo mínimo para responder dos preguntas: ¿está abierta la
# ventana? y ¿el último envío llegó? Es memoria del proceso, no DB: si el
# servicio reinicia se pierde y el estado vuelve a "no sé", que se muestra como
# desconocido en vez de mentir.
VENTANA_HORAS = float(os.environ.get('WHATSAPP_VENTANA_HORAS', '24'))

_ultimo_inbound = {}    # numero normalizado -> timestamp del último mensaje suyo
_ultimos_estados = []   # los últimos status de entrega, para diagnosticar
_estado_lock = threading.Lock()


def _registrar_inbound(numero):
    n = normalizar_numero(numero)
    with _estado_lock:
        _ultimo_inbound[n] = time.time()
    print(f"[ventana] {n} escribió: ventana abierta por {VENTANA_HORAS}h", flush=True)


def _registrar_estado(st):
    """Guarda un status de entrega (sent/delivered/read/failed) del webhook."""
    with _estado_lock:
        _ultimos_estados.append({
            "estado": st.get("status"),
            "para": st.get("recipient_id"),
            "cuando": time.time(),
            "error": ((st.get("errors") or [{}])[0].get("title")
                      or (st.get("errors") or [{}])[0].get("message") or ""),
        })
        del _ultimos_estados[:-40]   # solo interesa lo reciente
    if st.get("status") == "failed":
        print(f"[ventana] ENTREGA FALLIDA a {st.get('recipient_id')}: "
              f"{st.get('errors')}", flush=True)


@app.route("/estado-ventana", methods=["GET"])
def estado_ventana():
    """¿Se puede mandar texto libre a este número ahora mismo?

    abierta=None significa "no sé": el webhook nunca llegó (no está configurado
    o apunta a otro lado). Es distinto de False y se muestra distinto: no vamos
    a decirle al vendedor que está cerrada cuando en realidad no tenemos idea.
    """
    n = normalizar_numero(request.args.get("to", ""))
    ahora = time.time()
    with _estado_lock:
        ts = _ultimo_inbound.get(n)
        fallos = [e for e in _ultimos_estados
                  if e["estado"] == "failed" and ahora - e["cuando"] < 3600]
        hubo_webhook = bool(_ultimo_inbound or _ultimos_estados)
    if not hubo_webhook:
        return {"abierta": None, "motivo": "sin datos del webhook"}
    if not ts:
        return {"abierta": False, "motivo": "nunca escribió", "fallos_ultima_hora": len(fallos)}
    resta = VENTANA_HORAS * 3600 - (ahora - ts)
    return {"abierta": resta > 0,
            "minutos_restantes": max(0, round(resta / 60)),
            "ultimo_mensaje_hace_min": round((ahora - ts) / 60),
            "fallos_ultima_hora": len(fallos)}


@app.route("/whatsapp", methods=["POST"])
def received_message():
    body = request.get_json(silent=True) or {}

    # El webhook trae DOS cosas distintas: mensajes entrantes y estados de
    # entrega. Antes esto asumía que siempre había un mensaje de texto y
    # explotaba con todo lo demás (el except se lo tragaba), así que los status
    # de entrega se perdían enteros. Ahora se recorre todo lo que venga.
    try:
        for entry in body.get("entry", []):
            for cambio in entry.get("changes", []):
                value = cambio.get("value") or {}
                for st in value.get("statuses", []):
                    _registrar_estado(st)
                for message in value.get("messages", []):
                    numero = message.get("from")
                    if not numero:
                        continue
                    # CUALQUIER mensaje suyo abre la ventana, sea texto, audio o
                    # un pulgar arriba. Registrarlo solo para texto dejaría la
                    # ventana marcada como cerrada estando abierta.
                    _registrar_inbound(numero)
                    texto = (message.get("text") or {}).get("body")
                    if not texto:
                        continue
                    print(f"El texto recibido del usuario es: {texto}", flush=True)
                    if OPENAI_SERVICE_URL:
                        if whatsapp_service(enviar_mensaje(texto, numero)):
                            print("Mensaje enviado correctamente.")
                        else:
                            print("Error en el envío del mensaje.")
    except Exception as e:
        # Un error acá no puede devolver != 200: Meta reintenta el webhook y,
        # si falla seguido, deja de mandarlo.
        print(f"[webhook] error procesando: {e!r}", flush=True)

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
    
# Meta ENTREGA los mensajes con un formato (549 + área + número) y solo ACEPTA
# enviarlos a la forma que quedó registrada (con el 15 en vez del 9). Es un
# problema de los celulares argentinos. El mapeo sale del entorno para que
# agregar un número no sea un cambio de código: WHATSAPP_NUMERO_MAP es un JSON
# {"como_lo_manda_meta": "como_lo_acepta_meta"}.
def _cargar_mapeo():
    crudo = os.environ.get('WHATSAPP_NUMERO_MAP', '').strip()
    if not crudo:
        return {"5492233407778": "54223153407778"}
    try:
        m = json.loads(crudo)
        return {str(k): str(v) for k, v in m.items()} if isinstance(m, dict) else {}
    except Exception as e:
        # Un JSON mal escrito no puede dejar el servicio sin mandar nada: se cae
        # al mapeo por defecto y se avisa fuerte en el log.
        print(f"[wa] WHATSAPP_NUMERO_MAP inválido ({e}); uso el mapeo por defecto", flush=True)
        return {"5492233407778": "54223153407778"}


NUMERO_MAP = _cargar_mapeo()


def normalizar_numero(numero):
    n = (numero or "").strip().lstrip("+").replace(" ", "").replace("-", "")
    return NUMERO_MAP.get(n, n)

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

    Existe para repartir los comentarios: quien los recibe los ve separados y
    los reenvía adonde quiera con la selección múltiple de WhatsApp (mantener
    apretado, tildar todos, reenviar). Mandarlos en un solo mensaje no sirve:
    del otro lado no se podrían reenviar de a uno.

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
    if codigo == 131030:
        # Pasa siempre que se suma un vendedor: el número de PRUEBA de Meta solo
        # entrega a 5 destinatarios cargados a mano. Se arregla de verdad con un
        # número real de WhatsApp Business, que no tiene lista blanca.
        return ("Tu número no está habilitado para recibir mensajes del bot. "
                "Hay que agregarlo a la lista de destinatarios en Meta "
                "(el número de prueba admite hasta 5).")
    if codigo in (190, 4):
        return "El token de WhatsApp venció o se pasó del límite de envíos."
    return err.get("message") or f"WhatsApp respondió {resp.status_code}."


@app.route("/send-template", methods=["POST"])
def send_template():
    try:
        data = request.get_json()
        # Igual que en /send-bulk: el número del .env viene con "+" y en la forma
        # 549..., y Meta solo acepta la que quedó registrada. Sin esto el template
        # se rechaza con 131030 ("not in allowed list") aunque el número sea el
        # correcto — que es exactamente lo que pasaba acá.
        number = normalizar_numero((data.get("to") or "").strip().lstrip("+"))
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

        # Se manda acá y no con whatsapp_service() porque esa función devuelve
        # solo True/False: el motivo del rechazo se perdía y el vendedor veía
        # "Fallo al enviar el template", que no dice qué hacer.
        resp = requests.post(
            WHATSAPP_API_URL, json=body,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=25,
        )
        if resp.status_code == 200:
            return {"status": "ok", "message": f"Template '{template_name}' enviado a {number}."}, 200
        print(f"[send-template] Meta rechazó: {resp.status_code} {resp.text}", flush=True)
        return {"status": "error", "message": _error_meta(resp)}, 502

    except Exception as e:
        print(e)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8501, debug=False)  # Cambia debug a False para producción
