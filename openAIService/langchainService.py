from flask import Flask, request
import os
import json
import traceback
import anthropic
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config.negocios import get_negocio, get_carta, build_menu, resolver_seleccion

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# In-memory session store: phone_number -> list of messages
conversation_sessions = {}

# Negocio elegido por cada conversación: phone_number -> negocio_id.
# Mientras un número no esté acá, está en "modo menú" (todavía no eligió).
session_negocio = {}

# Palabras que reabren el menú para cambiar de negocio en cualquier momento.
MENU_COMMANDS = {"menu", "menú", "cambiar", "opciones", "negocios", "volver"}

CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "admin-key.json")

def build_system_prompt(negocio: dict, is_first_message: bool = False):
    today = datetime.now().strftime("%d/%m/%Y")
    nombre = negocio["nombre"]
    rubro = negocio["rubro"]
    horarios = negocio["horarios"]
    greeting_instruction = ""
    if is_first_message:
        greeting_instruction = f"""
PRIMER MENSAJE: El cliente escribe por primera vez. Preséntate de forma profesional y cordial: saluda, di que eres de {nombre}, {rubro} en Ibiza, y ponte a su disposición para reservas o cualquier consulta. Máximo 3 frases, sin listas.
"""
    return f"""Hoy es {today}.

Eres la persona que atiende el WhatsApp de {nombre}, {rubro} en Ibiza. No eres un bot ni un asistente virtual: eres un profesional que trabaja aquí y responde los mensajes. No lo menciones nunca, simplemente actúa así.
{greeting_instruction}
CÓMO ESCRIBES:
Español de España, trato de tú. Tono profesional y cordial: cercano pero correcto, educado sin sonar acartonado. Breve y directo, como un mensaje de WhatsApp: una o dos frases, nunca más. Sin listas, sin asteriscos, sin títulos, sin formato de ningún tipo. Sin emojis salvo que el cliente los use. Si el cliente escribe en inglés, respondes en inglés; si escribe en español, en español.

Resuelve rápido y con amabilidad, sin frases hechas ni relleno. No repitas lo que ya dijo el cliente ni confirmes cada detalle innecesariamente.

EL LUGAR:
{nombre} está en Ibiza.
Horarios: {horarios}.
Para precios, servicios y dudas frecuentes usas la herramienta consultar_carta y respondes con lo que devuelve, resumiendo lo justo para WhatsApp. Nunca inventes precios, servicios ni datos: si el cliente pregunta por algo que no está en la carta, indícale con amabilidad que llame al local.

RESERVAS:
Tienes herramientas reales para gestionar el calendario. Úsalas siempre.

Si alguien quiere reservar:
- Si no ha dicho fecha/hora, pregúntale solo eso, nada más
- Usas check_availability para ver si hay hueco
- Si hay hueco, pides nombre y correo en un solo mensaje
- Creas la reserva con create_reservation
- Confirmas en una frase, sin exagerar

Si el horario está ocupado, se lo dices con naturalidad y le ofreces el más cercano disponible.

Recuerda el nombre del cliente cuando te lo diga y úsalo con naturalidad, no en cada mensaje."""



def get_calendar_service():
    scopes = ["https://www.googleapis.com/auth/calendar"]
    creds_json = os.environ.get("_CREDENTIALS_JSON")
    if creds_json:
        # En Railway/producción: credenciales inyectadas por variable de entorno
        creds = service_account.Credentials.from_service_account_info(
            json.loads(creds_json), scopes=scopes
        )
    else:
        # En local: archivo admin-key.json junto al servicio
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_PATH, scopes=scopes
        )
    return build("calendar", "v3", credentials=creds)


def check_availability(date_str: str, time_str: str, duration_hours: int = 2) -> dict:
    """Check if a time slot is available in  Calendar."""
    try:
        service = get_calendar_service()
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        dt_end = dt_start + timedelta(hours=duration_hours)

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=dt_start.isoformat() + "-03:00",
            timeMax=dt_end.isoformat() + "-03:00",
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        if events:
            return {
                "available": False,
                "message": f"El horario {time_str} del {date_str} ya está reservado.",
                "conflicts": [e.get("summary", "Reserva") for e in events],
            }
        return {
            "available": True,
            "message": f"El horario {time_str} del {date_str} está disponible.",
        }
    except Exception as e:
        return {"available": False, "message": f"Error consultando disponibilidad: {str(e)}"}


def create_reservation(
    date_str: str,
    time_str: str,
    client_name: str,
    client_email: str,
    duration_hours: int = 2,
) -> dict:
    """Create a reservation event in Google Calendar."""
    try:
        service = get_calendar_service()
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        dt_end = dt_start + timedelta(hours=duration_hours)

        event = {
            "summary": f"Reserva - {client_name}",
            "description": f"Cliente: {client_name}\nEmail: {client_email}",
            "start": {"dateTime": dt_start.isoformat() + "-03:00", "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": dt_end.isoformat() + "-03:00", "timeZone": "America/Argentina/Buenos_Aires"},
        }

        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return {
            "success": True,
            "message": f"Reserva confirmada para {client_name} el {date_str} a las {time_str}.",
            "event_id": created.get("id"),
        }
    except Exception as e:
        return {"success": False, "message": f"Error creando la reserva: {str(e)}"}


TOOLS = [
    {
        "name": "check_availability",
        "description": "Consulta si hay disponibilidad en el calendario para una fecha y hora. Llamar antes de confirmar cualquier reserva.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD, ej: 2025-07-15",
                },
                "time_str": {
                    "type": "string",
                    "description": "Hora en formato HH:MM, ej: 20:00",
                },
                "duration_hours": {
                    "type": "integer",
                    "description": "Duración en horas (por defecto 2)",
                    "default": 2,
                },
            },
            "required": ["date_str", "time_str"],
        },
    },
    {
        "name": "create_reservation",
        "description": "Crea una reserva en el calendario una vez confirmada la disponibilidad y obtenidos los datos del cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD",
                },
                "time_str": {
                    "type": "string",
                    "description": "Hora en formato HH:MM",
                },
                "client_name": {
                    "type": "string",
                    "description": "Nombre completo del cliente",
                },
                "client_email": {
                    "type": "string",
                    "description": "Email del cliente para enviarle la confirmación",
                },
                "duration_hours": {
                    "type": "integer",
                    "description": "Duración en horas (por defecto 2)",
                    "default": 2,
                },
            },
            "required": ["date_str", "time_str", "client_name", "client_email"],
        },
    },
    {
        "name": "consultar_carta",
        "description": "Devuelve la carta del lugar: servicios con descripción y precio, y preguntas frecuentes con su respuesta. Úsala siempre que el cliente pregunte por precios, servicios, productos, planes, detalles o dudas habituales (horarios, reservas, formas de pago, etc.), en vez de responder de memoria.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def process_tool_call(tool_name: str, tool_input: dict, negocio: dict) -> str:
    if tool_name == "check_availability":
        result = check_availability(
            tool_input["date_str"],
            tool_input["time_str"],
            tool_input.get("duration_hours", 2),
        )
    elif tool_name == "create_reservation":
        result = create_reservation(
            tool_input["date_str"],
            tool_input["time_str"],
            tool_input["client_name"],
            tool_input["client_email"],
            tool_input.get("duration_hours", 2),
        )
    elif tool_name == "consultar_carta":
        result = get_carta(negocio)
    else:
        result = {"error": f"Herramienta desconocida: {tool_name}"}
    return json.dumps(result, ensure_ascii=False)


@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    user_prompt = request.args.get("user_prompt", "")
    phone_number = request.args.get("phone_number", "default")

    texto = user_prompt.strip().lower()

    # Comando explícito para (re)abrir el menú y cambiar de negocio.
    if texto in MENU_COMMANDS:
        session_negocio.pop(phone_number, None)
        conversation_sessions.pop(phone_number, None)
        return build_menu()

    # Todavía no eligió negocio: estamos en modo selección.
    if phone_number not in session_negocio:
        seleccion = resolver_seleccion(user_prompt)
        if seleccion is None:
            # Primer contacto o respuesta que no coincide con ninguna opción.
            return build_menu()
        session_negocio[phone_number] = seleccion
        conversation_sessions.pop(phone_number, None)  # arranca la charla limpia
        negocio = get_negocio(seleccion)
        return f"Perfecto, estás hablando con {negocio['nombre']}. ¿En qué puedo ayudarte?"

    # Ya hay negocio elegido para esta conversación.
    negocio = get_negocio(session_negocio[phone_number])

    is_first_message = phone_number not in conversation_sessions
    if is_first_message:
        conversation_sessions[phone_number] = []

    conversation_sessions[phone_number].append({"role": "user", "content": user_prompt})

    # Keep last 20 messages to avoid hitting context limits
    messages = conversation_sessions[phone_number][-20:]

    # Agentic loop: handle tool calls
    while True:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=build_system_prompt(negocio, is_first_message),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next(
                (block.text for block in response.content if block.type == "text"), ""
            )
            conversation_sessions[phone_number].append(
                {"role": "assistant", "content": text}
            )
            return text

        if response.stop_reason == "tool_use":
            # Append assistant turn with tool_use blocks
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = process_tool_call(block.name, block.input, negocio)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
            # Update session with the new messages
            conversation_sessions[phone_number] = messages
        else:
            # Unexpected stop reason
            break

    return "Lo siento, hubo un problema procesando tu mensaje."


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """Ante un fallo inesperado devolvemos texto plano, nunca el HTML de Flask.

    Si se escapa una excepcion (fallo de la API, del calendario, etc.), Flask
    responderia con su pagina HTML de error 500 y el whatsapp-service acabaria
    mandandosela al cliente. Devolvemos texto y dejamos el 500 para que el
    whatsapp-service lo convierta en su mensaje de fallback.
    """
    traceback.print_exc()
    return "Lo siento, hubo un problema procesando tu mensaje.", 500


@app.route("/clear_session", methods=["GET"])
def clear_session():
    phone_number = request.args.get("phone_number", "default")
    conversation_sessions.pop(phone_number, None)
    session_negocio.pop(phone_number, None)
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
