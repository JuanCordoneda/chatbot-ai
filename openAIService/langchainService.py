from flask import Flask, request
import os
import json
import anthropic
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# In-memory session store: phone_number -> list of messages
conversation_sessions = {}

CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "admin-key.json")

def build_system_prompt(is_first_message: bool = False):
    today = datetime.now().strftime("%d/%m/%Y")
    greeting_instruction = ""
    if is_first_message:
        greeting_instruction = """
PRIMER MENSAJE: El cliente acaba de escribir por primera vez. Respondé con un saludo corto y natural, presentá el bar en una línea, mencioná que pueden reservar o consultar lo que quieran. Máximo 3 frases. Nada de listas.
"""
    return f"""Hoy es {today}.

Sos quien atiende el WhatsApp del bar. No sos un bot, no sos un asistente virtual, sos una persona real que trabaja ahí y responde mensajes. Nunca lo menciones, simplemente actuá así.
{greeting_instruction}
CÓMO ESCRIBÍS:
Corto. Directo. Como cualquier persona respondería un mensaje de WhatsApp. Una o dos frases, nunca más. Sin listas, sin asteriscos, sin títulos, sin formato de ningún tipo. Sin emojis a menos que el cliente los use. Si el cliente habla en inglés, respondés en inglés. Si habla en español, en español. Si cambia, vos también.

No repitas información innecesaria. No confirmes todo lo que dijo el cliente. No uses frases de bot como "claro que sí", "por supuesto", "encantado de ayudarte". Hablá como habla la gente.

EL BAR:
Horarios: lunes a viernes de 18 a 02, sábados y domingos de 16 a 03.
Carta: cerveza artesanal $1200, copa de vino $1500, cóctel de la casa $2000, gaseosa $800.
Para cualquier otra duda del menú o del lugar, decile que llame al local.

RESERVAS:
Tenés herramientas reales para gestionar el calendario. Úsalas siempre.

Si alguien quiere reservar:
- Si no dijo fecha/hora, preguntás solo eso, nada más
- Usás check_availability para ver si hay lugar
- Si hay lugar, pedís nombre y mail en un solo mensaje
- Creás la reserva con create_reservation
- Confirmás en una frase, sin dramatismo

Si el horario está ocupado, lo decís natural y ofrecés el más cercano disponible.

Recordá el nombre del cliente una vez que te lo dice y usalo con naturalidad, no en cada mensaje."""



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
]


def process_tool_call(tool_name: str, tool_input: dict) -> str:
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
    else:
        result = {"error": f"Herramienta desconocida: {tool_name}"}
    return json.dumps(result, ensure_ascii=False)


@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    user_prompt = request.args.get("user_prompt", "")
    phone_number = request.args.get("phone_number", "default")

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
            system=build_system_prompt(is_first_message),
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
                    result = process_tool_call(block.name, block.input)
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


@app.route("/clear_session", methods=["GET"])
def clear_session():
    phone_number = request.args.get("phone_number", "default")
    if phone_number in conversation_sessions:
        del conversation_sessions[phone_number]
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
