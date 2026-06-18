from flask import Flask, request
import os
import json
import threading
import anthropic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build
import sys
sys.path.insert(0, os.path.dirname(__file__))
from modules.engagement_flow import es_link_instagram, extraer_link, procesar_post

app = Flask(__name__)
claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "admin-key.json")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SESSION_TTL_HOURS = 6
MAX_SESSION_MESSAGES = 20

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_calendar_service = None
_calendar_lock = threading.Lock()


# ── Session management ────────────────────────────────────────────────────────

def _get_session(phone: str) -> tuple[list, bool]:
    now = datetime.now(tz=timezone.utc)
    with _sessions_lock:
        session = _sessions.get(phone)
        if session is None or (now - session["last_activity"]).total_seconds() > SESSION_TTL_HOURS * 3600:
            _sessions[phone] = {"messages": [], "last_activity": now}
            return [], True
        session["last_activity"] = now
        return session["messages"], False


def _save_session(phone: str, messages: list):
    with _sessions_lock:
        if phone in _sessions:
            _sessions[phone]["messages"] = messages[-MAX_SESSION_MESSAGES:]
            _sessions[phone]["last_activity"] = datetime.now(tz=timezone.utc)


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_calendar_service():
    global _calendar_service
    if _calendar_service is None:
        with _calendar_lock:
            if _calendar_service is None:
                creds = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH,
                    scopes=["https://www.googleapis.com/auth/calendar"],
                )
                _calendar_service = build("calendar", "v3", credentials=creds)
    return _calendar_service


def check_availability(date_str: str, time_str: str, duration_hours: int = 2) -> dict:
    try:
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=TZ)
        dt_end = dt_start + timedelta(hours=duration_hours)
        service = _get_calendar_service()
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=dt_start.isoformat(),
            timeMax=dt_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if events:
            return {
                "available": False,
                "message": f"El horario {time_str} del {date_str} ya está reservado.",
                "conflicts": [e.get("summary", "Reserva") for e in events],
            }
        return {"available": True, "message": f"El horario {time_str} del {date_str} está disponible."}
    except Exception as e:
        return {"available": False, "message": f"Error consultando disponibilidad: {e}"}


def create_reservation(date_str: str, time_str: str, client_name: str, client_email: str, duration_hours: int = 2) -> dict:
    try:
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=TZ)
        dt_end = dt_start + timedelta(hours=duration_hours)
        service = _get_calendar_service()
        event = {
            "summary": f"Reserva - {client_name}",
            "description": f"Cliente: {client_name}\nEmail: {client_email}",
            "start": {"dateTime": dt_start.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": dt_end.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return {
            "success": True,
            "message": f"Reserva confirmada para {client_name} el {date_str} a las {time_str}.",
            "event_id": created.get("id"),
        }
    except Exception as e:
        return {"success": False, "message": f"Error creando la reserva: {e}"}


# ── Tools ─────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_availability",
        "description": "Consulta si hay disponibilidad en el calendario para una fecha y hora. Llamar SIEMPRE antes de confirmar cualquier reserva.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Fecha en formato YYYY-MM-DD, ej: 2026-07-15"},
                "time_str": {"type": "string", "description": "Hora en formato HH:MM, ej: 20:00"},
                "duration_hours": {"type": "integer", "description": "Duración en horas (por defecto 2)", "default": 2},
            },
            "required": ["date_str", "time_str"],
        },
    },
    {
        "name": "create_reservation",
        "description": "Crea una reserva en el calendario. Llamar solo después de confirmar disponibilidad y obtener nombre y email del cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                "time_str": {"type": "string", "description": "Hora en formato HH:MM"},
                "client_name": {"type": "string", "description": "Nombre completo del cliente"},
                "client_email": {"type": "string", "description": "Email del cliente"},
                "duration_hours": {"type": "integer", "description": "Duración en horas (por defecto 2)", "default": 2},
            },
            "required": ["date_str", "time_str", "client_name", "client_email"],
        },
    },
]

TOOL_HANDLERS = {
    "check_availability": lambda i: check_availability(i["date_str"], i["time_str"], i.get("duration_hours", 2)),
    "create_reservation": lambda i: create_reservation(i["date_str"], i["time_str"], i["client_name"], i["client_email"], i.get("duration_hours", 2)),
}


def run_tool(name: str, input: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    result = handler(input) if handler else {"error": f"Herramienta desconocida: {name}"}
    return json.dumps(result, ensure_ascii=False)


# ── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(is_first_message: bool = False) -> str:
    today = datetime.now(tz=TZ).strftime("%d/%m/%Y")
    greeting = (
        "\nPRIMER MENSAJE: Saludá de forma corta y natural, presentá el bar en una línea y mencioná "
        "que pueden reservar o preguntar lo que quieran. Máximo 3 frases.\n"
        if is_first_message else ""
    )
    return f"""Hoy es {today}.
{greeting}
Sos quien atiende el WhatsApp del bar. No sos un bot, sos una persona real que trabaja ahí. Nunca lo menciones.

ESTILO: Corto y directo, como un mensaje de WhatsApp. Una o dos frases máximo. Sin listas, asteriscos, títulos ni formato. Sin emojis salvo que el cliente los use primero. Respondés en el idioma del cliente. Si cambia de idioma, vos también.

No uses frases de bot: "claro que sí", "por supuesto", "encantado de ayudarte". Hablá como habla la gente.

EL BAR:
Horarios: lunes a viernes 18-02, sábados y domingos 16-03.
Carta: cerveza artesanal $1200, copa de vino $1500, cóctel de la casa $2000, gaseosa $800.
Ante otras dudas del menú o del lugar, decile que llame al local.

RESERVAS: Tenés herramientas reales para el calendario. Úsalas siempre.
Flujo: pedís fecha/hora → check_availability → si hay lugar pedís nombre y mail → create_reservation → confirmás en una frase.
Si está ocupado, lo decís natural y ofrecés el horario más cercano.

Recordá el nombre del cliente cuando te lo diga y usalo con naturalidad."""


# ── Main route ────────────────────────────────────────────────────────────────

@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    user_prompt = request.args.get("user_prompt", "").strip()
    phone = request.args.get("phone_number", "default")

    if not user_prompt:
        return "", 200

    if es_link_instagram(user_prompt):
        return procesar_post(extraer_link(user_prompt))

    messages, is_first = _get_session(phone)
    messages.append({"role": "user", "content": user_prompt})

    while True:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=build_system_prompt(is_first),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            messages.append({"role": "assistant", "content": text})
            _save_session(phone, messages)
            return text

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)}
                for b in response.content if b.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    _save_session(phone, messages)
    return "Hubo un problema, intentá de nuevo."


@app.route("/clear_session", methods=["GET"])
def clear_session():
    phone = request.args.get("phone_number", "default")
    with _sessions_lock:
        _sessions.pop(phone, None)
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
